"""
Webhooks salientes: CheckIt notifica al PMS del cliente (inspección completada,
PDF listo). Firma HMAC-SHA256 del cuerpo y protección SSRF básica.
"""
import hashlib
import hmac
import ipaddress
import json
import logging
import socket
import uuid
from urllib.parse import urlsplit

import requests
from decouple import config
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import EntregaWebhook, WebhookDestino

logger = logging.getLogger(__name__)

TIMEOUT = config('WEBHOOK_SALIENTE_TIMEOUT', default=5, cast=float)
MAX_REINTENTOS = config('WEBHOOK_SALIENTE_MAX_REINTENTOS', default=5, cast=int)
BACKOFF_BASE = config('WEBHOOK_SALIENTE_BACKOFF_BASE', default=30, cast=int)

CABECERA_FIRMA = 'X-CheckIt-Signature'
CABECERA_EVENTO = 'X-CheckIt-Event'
CABECERA_ENTREGA = 'X-CheckIt-Delivery'

ENTREGADA = 'ENTREGADA'
REINTENTAR = 'REINTENTAR'
FALLIDA = 'FALLIDA'


class UrlNoPermitida(ValueError):
    pass


# --- SSRF ---

def _ip_no_permitida(ip):
    if getattr(ip, 'ipv4_mapped', None):
        ip = ip.ipv4_mapped
    # is_global descarta privadas, loopback, link-local, CGNAT, reservadas...
    return not ip.is_global or ip.is_multicast


def validar_esquema_url(url):
    """Validación sin red (al crear/editar un destino): esquema y host literal."""
    partes = urlsplit(url)
    esquemas = ('https', 'http') if settings.DEBUG else ('https',)
    if partes.scheme not in esquemas:
        raise UrlNoPermitida('La URL debe usar https.')
    host = partes.hostname
    if not host:
        raise UrlNoPermitida('La URL no tiene host.')
    if settings.DEBUG:
        return
    if host == 'localhost' or host.endswith('.localhost'):
        raise UrlNoPermitida('No se permiten destinos locales.')
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return  # es un nombre: se resuelve y comprueba al entregar
    if _ip_no_permitida(ip):
        raise UrlNoPermitida('No se permiten IPs privadas, loopback o link-local.')


def validar_url_destino(url):
    """Validación completa antes de cada envío: resuelve DNS y comprueba todas las IPs."""
    validar_esquema_url(url)
    if settings.DEBUG:
        return
    partes = urlsplit(url)
    try:
        infos = socket.getaddrinfo(partes.hostname, partes.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UrlNoPermitida(f'No se pudo resolver el host: {exc}')
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split('%')[0])
        if _ip_no_permitida(ip):
            raise UrlNoPermitida(f'El host resuelve a una IP no permitida ({ip}).')


# --- Firma y payload ---

def serializar(payload):
    # Serialización determinista: el cuerpo firmado es exactamente el enviado
    return json.dumps(payload, separators=(',', ':'), sort_keys=True).encode('utf-8')


def firmar(cuerpo, secreto):
    return 'sha256=' + hmac.new(secreto.encode('utf-8'), cuerpo, hashlib.sha256).hexdigest()


def construir_payload(tipo, inspeccion, event_id):
    propiedad = inspeccion.propiedad
    datos = {
        'inspeccion_id': inspeccion.pk,
        'propiedad_id': propiedad.pk,
        'external_id': propiedad.external_id,
        'estado': inspeccion.estado,
        'pdf_estado': inspeccion.pdf_estado,
    }
    # TODO(pdf.listo): añadir 'pdf_url' cuando exista un endpoint de descarga
    # firmado y con caducidad; claim-report exige JWT y el PMS no lo tiene.
    return {
        'event_id': str(event_id),
        'tipo': tipo,
        'fecha': timezone.now().isoformat(),
        'data': datos,
    }


# --- Emisión ---

def encolar_entrega(entrega_id):
    from .tasks import entregar_webhook

    try:
        entregar_webhook.apply_async(args=[entrega_id], retry=False)
    except Exception as exc:
        # No entregamos en línea: bloquearía la petición del operario con
        # llamadas de red. Queda PENDIENTE y se puede reintentar desde la API.
        logger.warning("Broker Celery no disponible (%s); entrega %s queda PENDIENTE", exc, entrega_id)


def emitir_evento(tipo, inspeccion):
    """
    Crea una EntregaWebhook por cada destino activo del owner suscrito a `tipo`
    y la encola al confirmar la transacción. Devuelve las entregas creadas.
    """
    owner_id = inspeccion.propiedad.owner_id
    destinos = [
        d for d in WebhookDestino.objects.filter(owner_id=owner_id, activo=True)
        if tipo in (d.eventos or [])
    ]
    if not destinos:
        return []

    event_id = uuid.uuid4()
    payload = construir_payload(tipo, inspeccion, event_id)
    entregas = [
        EntregaWebhook.objects.create(
            destino=d, evento=tipo, event_id=event_id, inspeccion=inspeccion, payload=payload,
        )
        for d in destinos
    ]
    for entrega in entregas:
        transaction.on_commit(lambda pk=entrega.pk: encolar_entrega(pk))
    return entregas


# --- Entrega ---

def _cerrar(entrega, estado, error=''):
    entrega.estado = estado
    entrega.error = error[:1000]
    if estado == EntregaWebhook.Estado.ENTREGADA:
        entrega.entregada = timezone.now()
    entrega.save(update_fields=['estado', 'error', 'intentos', 'ultimo_codigo', 'entregada', 'actualizada'])


def entregar(entrega_id):
    """
    Hace un intento de envío. Devuelve ENTREGADA, FALLIDA (definitivo) o
    REINTENTAR (5xx o error de red; la tarea decide si quedan reintentos).
    """
    entrega = EntregaWebhook.objects.select_related('destino').get(pk=entrega_id)
    if entrega.estado != EntregaWebhook.Estado.PENDIENTE:
        return entrega.estado

    destino = entrega.destino
    entrega.intentos += 1
    if not destino.activo:
        _cerrar(entrega, EntregaWebhook.Estado.FALLIDA, 'Destino desactivado.')
        return FALLIDA

    try:
        validar_url_destino(destino.url)
    except UrlNoPermitida as exc:
        _cerrar(entrega, EntregaWebhook.Estado.FALLIDA, f'URL bloqueada: {exc}')
        return FALLIDA

    cuerpo = serializar(entrega.payload)
    cabeceras = {
        'Content-Type': 'application/json',
        'User-Agent': 'CheckIt-Webhooks/1.0',
        CABECERA_FIRMA: firmar(cuerpo, destino.secreto),
        CABECERA_EVENTO: entrega.evento,
        CABECERA_ENTREGA: str(entrega.entrega_id),
    }
    try:
        # Sin redirecciones: una 3xx podría apuntar a una IP interna
        resp = requests.post(destino.url, data=cuerpo, headers=cabeceras, timeout=TIMEOUT, allow_redirects=False)
    except requests.RequestException as exc:
        entrega.ultimo_codigo = None
        entrega.error = f'Error de red: {exc}'[:1000]
        entrega.save(update_fields=['intentos', 'ultimo_codigo', 'error', 'actualizada'])
        return REINTENTAR

    entrega.ultimo_codigo = resp.status_code
    if 200 <= resp.status_code < 300:
        _cerrar(entrega, EntregaWebhook.Estado.ENTREGADA)
        return ENTREGADA
    if resp.status_code >= 500:
        entrega.error = f'HTTP {resp.status_code}'
        entrega.save(update_fields=['intentos', 'ultimo_codigo', 'error', 'actualizada'])
        return REINTENTAR
    # 4xx (y 3xx): el PMS rechaza el envío, reintentar no lo arreglará
    _cerrar(entrega, EntregaWebhook.Estado.FALLIDA, f'HTTP {resp.status_code} (sin reintento)')
    return FALLIDA


def marcar_fallida(entrega_id, mensaje):
    EntregaWebhook.objects.filter(pk=entrega_id, estado=EntregaWebhook.Estado.PENDIENTE).update(
        estado=EntregaWebhook.Estado.FALLIDA, error=mensaje[:1000], actualizada=timezone.now(),
    )


def reabrir_entrega(entrega):
    """Devuelve una entrega FALLIDA a PENDIENTE. True si se ha reabierto."""
    actualizadas = EntregaWebhook.objects.filter(
        pk=entrega.pk, estado=EntregaWebhook.Estado.FALLIDA,
    ).update(estado=EntregaWebhook.Estado.PENDIENTE, error='', actualizada=timezone.now())
    return bool(actualizadas)
