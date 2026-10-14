"""
Webhooks salientes: CheckIt notifica al PMS del cliente (inspección completada,
PDF listo). Firma HMAC-SHA256 del cuerpo y protección SSRF:
- La URL se valida sin ambigüedades de parser (sin userinfo, '\\' ni espacios).
- La IP se valida al conectar y la conexión se fija a esa IP (sin DNS rebinding),
  manteniendo SNI y verificación del certificado con el hostname original.
"""
import hashlib
import hmac
import ipaddress
import json
import logging
import socket
import uuid
from datetime import timedelta
from urllib.parse import urlsplit

import requests
from celery.exceptions import SoftTimeLimitExceeded
from decouple import config
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from requests.adapters import HTTPAdapter
from urllib3.util import parse_url

from .models import EntregaWebhook, WebhookDestino

logger = logging.getLogger(__name__)

TIMEOUT_CONEXION = config('WEBHOOK_SALIENTE_TIMEOUT_CONEXION', default=3, cast=float)
TIMEOUT_LECTURA = config('WEBHOOK_SALIENTE_TIMEOUT', default=5, cast=float)
MAX_REINTENTOS = config('WEBHOOK_SALIENTE_MAX_REINTENTOS', default=5, cast=int)
BACKOFF_BASE = config('WEBHOOK_SALIENTE_BACKOFF_BASE', default=30, cast=int)
MAX_DESTINOS = config('WEBHOOK_SALIENTE_MAX_DESTINOS', default=10, cast=int)
# Solo para desarrollo: permite http y destinos locales/privados
PERMITIR_LOCAL = config('WEBHOOK_SALIENTE_PERMITIR_LOCAL', default=False, cast=bool)

# Una PENDIENTE sin movimiento más allá del mayor backoff se considera atascada
ATASCADA_TRAS = timedelta(seconds=max(600, BACKOFF_BASE * 2 ** max(MAX_REINTENTOS - 1, 0) + 120))

CABECERA_FIRMA = 'X-CheckIt-Signature'
CABECERA_EVENTO = 'X-CheckIt-Event'
CABECERA_ENTREGA = 'X-CheckIt-Delivery'

ENTREGADA = 'ENTREGADA'
REINTENTAR = 'REINTENTAR'
FALLIDA = 'FALLIDA'


class UrlNoPermitida(Exception):
    # No hereda de ValueError: requests la convertiría en InvalidURL
    pass


# --- SSRF ---

# Rangos que encapsulan IPv4 o tunelizan (NAT64, 6to4, Teredo): se bloquean enteros
REDES_BLOQUEADAS = [
    ipaddress.ip_network('64:ff9b::/96'),
    ipaddress.ip_network('64:ff9b:1::/48'),
    ipaddress.ip_network('2002::/16'),
    ipaddress.ip_network('2001::/32'),
]


def _ip_no_permitida(ip):
    if ip.version == 6:
        if ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        elif any(ip in red for red in REDES_BLOQUEADAS):
            return True
    # is_global descarta privadas, loopback, link-local, CGNAT, reservadas...
    return not ip.is_global or ip.is_multicast


def _host_sin_corchetes(host):
    return (host or '').strip('[]').lower()


def validar_esquema_url(url):
    """Validación sin red (al crear/editar un destino y antes de cada envío)."""
    if any(c.isspace() or ord(c) < 32 or ord(c) == 127 or c == '\\' for c in url):
        raise UrlNoPermitida('La URL contiene caracteres no permitidos.')
    try:
        partes = parse_url(url)  # mismo parser que usa requests/urllib3
        host_urlsplit = urlsplit(url).hostname
    except Exception:
        raise UrlNoPermitida('URL mal formada.')
    esquemas = ('https', 'http') if PERMITIR_LOCAL else ('https',)
    if partes.scheme not in esquemas:
        raise UrlNoPermitida('La URL debe usar https.')
    if partes.auth or '@' in (urlsplit(url).netloc or ''):
        raise UrlNoPermitida('La URL no puede incluir usuario ni contraseña.')
    host = _host_sin_corchetes(partes.host)
    if not host:
        raise UrlNoPermitida('La URL no tiene host.')
    # Si dos parsers discrepan sobre el host, la URL es ambigua
    if _host_sin_corchetes(host_urlsplit) != host:
        raise UrlNoPermitida('URL ambigua.')
    if PERMITIR_LOCAL:
        return
    if host == 'localhost' or host.endswith('.localhost'):
        raise UrlNoPermitida('No se permiten destinos locales.')
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return  # es un nombre: se resuelve y comprueba al conectar
    if _ip_no_permitida(ip):
        raise UrlNoPermitida('No se permiten IPs privadas, loopback o link-local.')


def resolver_ip_publica(host, puerto):
    """Resuelve `host` y devuelve una de sus IPs; falla si alguna no es global."""
    try:
        infos = socket.getaddrinfo(host, puerto, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise UrlNoPermitida('No se pudo resolver el host.')
    ips = [ipaddress.ip_address(info[4][0].split('%')[0]) for info in infos]
    if not ips:
        raise UrlNoPermitida('El host no tiene direcciones.')
    for ip in ips:
        if _ip_no_permitida(ip):
            # La IP concreta solo al log: en el error guardado permitiría descubrir
            # a qué IPs internas resuelven nombres arbitrarios
            logger.warning("Destino de webhook bloqueado: %s resuelve a %s", host, ip)
            raise UrlNoPermitida('El host resuelve a una IP no permitida.')
    return str(ips[0])


def validar_url_destino(url):
    """Validación previa completa (esquema + DNS). La definitiva se repite al conectar."""
    validar_esquema_url(url)
    if PERMITIR_LOCAL:
        return
    partes = parse_url(url)
    host = _host_sin_corchetes(partes.host)
    try:
        ipaddress.ip_address(host)
    except ValueError:
        resolver_ip_publica(host, partes.port or 443)


class AdaptadorIpFijada(HTTPAdapter):
    """
    Resuelve y valida la IP en el momento de abrir la conexión y conecta a esa IP
    literal: no hay una segunda resolución DNS que pueda devolver otra IP. El
    certificado se verifica y el SNI se envía con el hostname original.
    """

    def build_connection_pool_key_attributes(self, request, verify, cert=None):
        host_params, pool_kwargs = super().build_connection_pool_key_attributes(request, verify, cert)
        if PERMITIR_LOCAL:
            return host_params, pool_kwargs
        host = _host_sin_corchetes(host_params['host'])
        puerto = host_params['port'] or (443 if host_params['scheme'] == 'https' else 80)
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            ip = resolver_ip_publica(host, puerto)
        else:
            if _ip_no_permitida(ip):
                raise UrlNoPermitida(f'IP no permitida ({ip}).')
            ip = str(ip)
        host_params = {**host_params, 'host': ip}
        if host_params['scheme'] == 'https':
            pool_kwargs = {**pool_kwargs, 'server_hostname': host, 'assert_hostname': host}
        return host_params, pool_kwargs


def _sesion():
    sesion = requests.Session()
    sesion.trust_env = False  # HTTP(S)_PROXY no debe desviar la conexión
    adaptador = AdaptadorIpFijada(max_retries=0)
    sesion.mount('https://', adaptador)
    sesion.mount('http://', adaptador)
    return sesion


def _cabecera_host(url):
    # Como se conecta a la IP literal, el Host debe llevar el nombre original
    partes = parse_url(url)
    puerto_defecto = 443 if partes.scheme == 'https' else 80
    if partes.port and partes.port != puerto_defecto:
        return f'{partes.host}:{partes.port}'
    return partes.host


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
        # No entregamos en línea: bloquearía la petición del operario con llamadas
        # de red. Queda PENDIENTE y la recupera reencolar_atascadas o un reintento manual.
        logger.warning(
            "Broker Celery no disponible (%s); entrega %s queda PENDIENTE", type(exc).__name__, entrega_id,
        )


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

def _actualizar_si_pendiente(entrega_id, **campos):
    # Escritura condicional: nunca pisa una entrega ya cerrada por otro worker
    campos.setdefault('actualizada', timezone.now())
    return EntregaWebhook.objects.filter(pk=entrega_id, estado=EntregaWebhook.Estado.PENDIENTE).update(**campos)


def _cerrar(entrega_id, estado, codigo=None, error=''):
    campos = {'estado': estado, 'ultimo_codigo': codigo, 'error': error[:1000]}
    if estado == EntregaWebhook.Estado.ENTREGADA:
        campos['entregada'] = timezone.now()
    _actualizar_si_pendiente(entrega_id, **campos)
    return ENTREGADA if estado == EntregaWebhook.Estado.ENTREGADA else FALLIDA


def _reservar(entrega_id):
    """
    Reserva atómica de un intento: solo gana el worker cuya actualización,
    condicionada al valor de `actualizada` que leyó, afecta a la fila.
    """
    entrega = EntregaWebhook.objects.select_related('destino').filter(pk=entrega_id).first()
    if entrega is None or entrega.estado != EntregaWebhook.Estado.PENDIENTE:
        return None
    ahora = timezone.now()
    ganada = EntregaWebhook.objects.filter(
        pk=entrega_id, estado=EntregaWebhook.Estado.PENDIENTE, actualizada=entrega.actualizada,
    ).update(intentos=F('intentos') + 1, actualizada=ahora)
    if not ganada:
        return None
    entrega.intentos += 1
    entrega.actualizada = ahora
    return entrega


def entregar(entrega_id):
    """
    Hace un intento de envío. Devuelve ENTREGADA, FALLIDA (definitivo) o
    REINTENTAR (5xx, error de red o tiempo agotado; la tarea decide si quedan
    reintentos). Si la entrega no está PENDIENTE u otro worker la tiene, devuelve su estado.
    """
    entrega = _reservar(entrega_id)
    if entrega is None:
        return EntregaWebhook.objects.filter(pk=entrega_id).values_list('estado', flat=True).first()

    destino = entrega.destino
    if not destino.activo:
        return _cerrar(entrega.pk, EntregaWebhook.Estado.FALLIDA, error='Destino desactivado.')

    try:
        validar_url_destino(destino.url)
    except UrlNoPermitida as exc:
        return _cerrar(entrega.pk, EntregaWebhook.Estado.FALLIDA, error=f'URL bloqueada: {exc}')

    cuerpo = serializar(entrega.payload)
    cabeceras = {
        'Host': _cabecera_host(destino.url),
        'Content-Type': 'application/json',
        'User-Agent': 'CheckIt-Webhooks/1.0',
        'Connection': 'close',
        CABECERA_FIRMA: firmar(cuerpo, destino.secreto),
        CABECERA_EVENTO: entrega.evento,
        CABECERA_ENTREGA: str(entrega.entrega_id),
    }
    sesion = _sesion()
    try:
        # stream=True: no se lee el cuerpo de la respuesta (podría ser enorme).
        # Sin redirecciones: una 3xx podría apuntar a una IP interna.
        resp = sesion.post(
            destino.url, data=cuerpo, headers=cabeceras, stream=True,
            timeout=(TIMEOUT_CONEXION, TIMEOUT_LECTURA), allow_redirects=False,
        )
        codigo = resp.status_code
        resp.close()
    except UrlNoPermitida as exc:
        return _cerrar(entrega.pk, EntregaWebhook.Estado.FALLIDA, error=f'URL bloqueada: {exc}')
    except SoftTimeLimitExceeded:
        _actualizar_si_pendiente(entrega.pk, ultimo_codigo=None, error='Tiempo límite de la tarea agotado.')
        return REINTENTAR
    except requests.RequestException as exc:
        # Solo la clase: str(exc) incluye la URL del destino
        _actualizar_si_pendiente(entrega.pk, ultimo_codigo=None, error=f'Error de red: {type(exc).__name__}')
        return REINTENTAR
    finally:
        sesion.close()

    if 200 <= codigo < 300:
        return _cerrar(entrega.pk, EntregaWebhook.Estado.ENTREGADA, codigo=codigo)
    if codigo >= 500:
        _actualizar_si_pendiente(entrega.pk, ultimo_codigo=codigo, error=f'HTTP {codigo}')
        return REINTENTAR
    # 4xx (y 3xx): el PMS rechaza el envío, reintentar no lo arreglará
    return _cerrar(entrega.pk, EntregaWebhook.Estado.FALLIDA, codigo=codigo, error=f'HTTP {codigo} (sin reintento)')


def marcar_fallida(entrega_id, mensaje):
    _actualizar_si_pendiente(entrega_id, estado=EntregaWebhook.Estado.FALLIDA, error=mensaje[:1000])


def reabrir_entrega(entrega):
    """
    Deja una entrega lista para reencolar: una FALLIDA vuelve a PENDIENTE y una
    PENDIENTE atascada (sin movimiento en ATASCADA_TRAS) se reclama. True si procede.
    """
    ahora = timezone.now()
    if EntregaWebhook.objects.filter(pk=entrega.pk, estado=EntregaWebhook.Estado.FALLIDA).update(
        estado=EntregaWebhook.Estado.PENDIENTE, error='', actualizada=ahora,
    ):
        return True
    return bool(EntregaWebhook.objects.filter(
        pk=entrega.pk, estado=EntregaWebhook.Estado.PENDIENTE, actualizada__lt=ahora - ATASCADA_TRAS,
    ).update(actualizada=ahora))


def reencolar_atascadas(limite=100):
    """Reencola las PENDIENTE sin movimiento (broker caído, worker muerto...)."""
    corte = timezone.now() - ATASCADA_TRAS
    ids = list(
        EntregaWebhook.objects.filter(estado=EntregaWebhook.Estado.PENDIENTE, actualizada__lt=corte)
        .order_by('actualizada').values_list('pk', flat=True)[:limite]
    )
    reencoladas = 0
    for pk in ids:
        # Reclamo condicional: si otro proceso la tocó entretanto, no se duplica
        if EntregaWebhook.objects.filter(
            pk=pk, estado=EntregaWebhook.Estado.PENDIENTE, actualizada__lt=corte,
        ).update(actualizada=timezone.now()):
            encolar_entrega(pk)
            reencoladas += 1
    return reencoladas


# TODO: purga periódica de entregas antiguas (p. ej. ENTREGADA con más de 90 días).
