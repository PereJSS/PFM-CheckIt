"""
Webhooks salientes: CheckIt notifica al PMS del cliente (inspección completada,
PDF listo). Firma HMAC-SHA256 del cuerpo y protección SSRF:
- La URL se valida sin ambigüedades de parser (sin userinfo, '\\' ni espacios).
- La IP se valida al conectar y la conexión se fija a esa IP (sin DNS rebinding),
  manteniendo SNI y verificación del certificado con el hostname original.
"""
import base64
import hashlib
import hmac
import ipaddress
import json
import logging
import re
import socket
import uuid
from datetime import timedelta
from urllib.parse import urlsplit

import requests
from celery.exceptions import SoftTimeLimitExceeded
from decouple import config
from django.db import transaction
from django.db.models import Count, F
from django.utils import timezone
from requests.adapters import HTTPAdapter
from urllib3.util import parse_url

from .models import EVENTO_INSPECCION_REABIERTA, EVENTO_PDF_LISTO, EntregaWebhook, EventoSaliente, WebhookDestino

logger = logging.getLogger(__name__)

TIMEOUT_CONEXION = config('WEBHOOK_SALIENTE_TIMEOUT_CONEXION', default=3, cast=float)
TIMEOUT_LECTURA = config('WEBHOOK_SALIENTE_TIMEOUT', default=5, cast=float)
MAX_REINTENTOS = config('WEBHOOK_SALIENTE_MAX_REINTENTOS', default=5, cast=int)
BACKOFF_BASE = config('WEBHOOK_SALIENTE_BACKOFF_BASE', default=30, cast=int)
MAX_DESTINOS = config('WEBHOOK_SALIENTE_MAX_DESTINOS', default=10, cast=int)
# Fallos al repartir un evento del outbox tras los que se descarta
MAX_INTENTOS_DESPACHO = config('WEBHOOK_SALIENTE_MAX_INTENTOS_DESPACHO', default=5, cast=int)
# El barrido despacha los eventos PENDIENTE con más de GRACIA (el despacho en línea pudo
# no terminar) y descarta los de más de VENTANA: avisar tan tarde confundiría al PMS
GRACIA_DESPACHO = timedelta(seconds=config('WEBHOOK_SALIENTE_GRACIA_SEG', default=120, cast=int))
VENTANA_DESPACHO = timedelta(hours=config('WEBHOOK_SALIENTE_VENTANA_HORAS', default=48, cast=int))
# Días que se conservan las entregas cerradas antes de la purga periódica
RETENCION_DIAS = config('WEBHOOK_ENTREGAS_RETENCION_DIAS', default=90, cast=int)
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


_RAIZ_HEX = re.compile(r'[0-9a-f]{64}')
# Clave de EventoSaliente.datos cuando solo se pudo guardar el `extra` del evento
_EXTRA = '_extra'


def construir_datos(tipo, inspeccion):
    """Bloque `data` del evento, sin el enlace del PDF (se firma al despachar)."""
    propiedad = inspeccion.propiedad
    datos = {
        'inspeccion_id': inspeccion.pk,
        'propiedad_id': propiedad.pk,
        'external_id': propiedad.external_id,
        'estado': inspeccion.estado,
        'pdf_estado': inspeccion.pdf_estado,
    }
    manifiesto = _testigo_manifiesto(inspeccion.pk)
    if manifiesto:
        datos['manifiesto'] = manifiesto
    if tipo == EVENTO_PDF_LISTO:
        # Qué manifiesto describe el PDF (puede quedar atrás si se reabre la inspección)
        datos['pdf_manifiesto_raiz'] = inspeccion.pdf_manifiesto_raiz
    return datos


def _con_enlace_pdf(tipo, inspeccion_id, datos):
    if tipo != EVENTO_PDF_LISTO:
        return datos
    from inspecciones.pdf_publico import caducidad, url_pdf_firmada

    # Enlace firmado y con caducidad: el PMS no tiene JWT para claim-report.
    # Un reintento manual pasado ese plazo entrega un enlace ya caducado.
    return {
        **datos,
        'pdf_url': url_pdf_firmada(inspeccion_id),
        'pdf_url_caduca': (timezone.now() + caducidad()).isoformat(),
    }


def _sobre(event_id, tipo, datos, ocurrido, secuencia=None):
    # `fecha` es la del envío (el receptor rechaza las antiguas); `ocurrido`, la del cambio
    sobre = {
        'event_id': str(event_id),
        'tipo': tipo,
        'fecha': timezone.now().isoformat(),
        'ocurrido': ocurrido.isoformat(),
        'data': datos,
    }
    if secuencia is not None:
        sobre['secuencia'] = secuencia
    return sobre


def construir_payload(tipo, inspeccion, event_id):
    datos = _con_enlace_pdf(tipo, inspeccion.pk, construir_datos(tipo, inspeccion))
    return _sobre(event_id, tipo, datos, timezone.now())


def _siguiente_secuencia(destino_id):
    """Siguiente número del destino. El UPDATE bloquea su fila hasta el commit: dos
    despachos a la vez se ordenan y un rollback devuelve el número (sin huecos falsos)."""
    WebhookDestino.objects.filter(pk=destino_id).update(ultima_secuencia=F('ultima_secuencia') + 1)
    return WebhookDestino.objects.filter(pk=destino_id).values_list('ultima_secuencia', flat=True).get()


def _testigo_manifiesto(inspeccion_id):
    """Raíz Merkle y sello, leídos de la base de datos: el manifiesto se congela y se
    sella con UPDATE, así que la instancia en memoria puede no tenerlos (#27)."""
    from inspecciones.models import Inspeccion

    fila = Inspeccion.objects.filter(pk=inspeccion_id).values(
        'manifiesto', 'manifiesto_raiz', 'manifiesto_tsa_token', 'manifiesto_tsa_timestamp',
        'manifiesto_tsa_url', 'manifiesto_tsa_cualificada', 'manifiesto_tsa_verificado',
        'manifiestos_anteriores',
    ).first()
    if not fila or not fila['manifiesto_raiz']:
        return None
    manifiesto = fila['manifiesto'] if isinstance(fila['manifiesto'], dict) else {}
    hojas = manifiesto.get('hojas')
    sellado = fila['manifiesto_tsa_timestamp']
    token = fila['manifiesto_tsa_token']
    return {
        'raiz': fila['manifiesto_raiz'],
        'hojas': len(hojas) if isinstance(hojas, list) else None,
        # El token RFC 3161 deja al PMS verificar el sello sin fiarse de CheckIt (openssl ts -verify)
        'tsa_token': base64.b64encode(bytes(token)).decode('ascii') if token else None,
        'tsa_timestamp': sellado.isoformat() if sellado else None,
        'tsa_url': fila['manifiesto_tsa_url'] or None,
        'tsa_cualificada': fila['manifiesto_tsa_cualificada'],
        'tsa_verificado': fila['manifiesto_tsa_verificado'],
        'raiz_anterior': _raiz_anterior(inspeccion_id, fila['manifiestos_anteriores']),
    }


def _raiz_anterior(inspeccion_id, anteriores):
    """Última raíz sellada archivada al reabrir (las no selladas no se archivan)."""
    if not anteriores:
        return None
    ultima = anteriores[-1] if isinstance(anteriores, list) else None
    raiz = ultima.get('raiz') if isinstance(ultima, dict) else None
    # Solo un hash: lo que no lo sea no sale en un payload firmado
    if isinstance(raiz, str) and _RAIZ_HEX.fullmatch(raiz):
        return raiz
    logger.warning("Archivo de manifiestos de la inspección %s sin una raíz válida al final", inspeccion_id)
    return None


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


def _destinos_suscritos(owner_id, tipo, hasta=None):
    destinos = WebhookDestino.objects.filter(owner_id=owner_id, activo=True)
    if hasta is not None:
        # Un destino creado después del evento no lo recibe (p. ej. al despachar tarde)
        destinos = destinos.filter(creado__lte=hasta)
    # En orden de pk: dos despachos simultáneos bloquean los destinos en el mismo orden
    return [d for d in destinos.order_by('pk') if tipo in (d.eventos or [])]


def registrar_evento(tipo, inspeccion, extra=None):
    """
    Guarda el evento en el outbox, en la transacción del cambio que lo origina, y lo
    reparte al confirmar. Sin destinos suscritos no escribe nada. Devuelve el evento.
    `extra` se añade al bloque `data`: datos del evento que la inspección ya no guarda.
    """
    owner_id = inspeccion.propiedad.owner_id
    if not _destinos_suscritos(owner_id, tipo):
        return None
    try:
        # Savepoint: un error de BD aquí no debe romper la transacción del cambio
        with transaction.atomic():
            # La base manda: `extra` solo añade claves propias del evento
            datos = {**(extra or {}), **construir_datos(tipo, inspeccion)}
            json.dumps(datos)  # lo que el JSONField no pueda guardar, aquí y no en el INSERT
    except Exception:
        # Se guarda igual y al despachar se reconstruye la base.
        # `extra` no se puede reconstruir: se guarda aparte, marcado
        logger.exception("No se pudieron construir los datos del evento %s de la inspección %s (extra=%s)",
                         tipo, inspeccion.pk, extra)
        datos = _solo_extra(extra)
    evento = EventoSaliente.objects.create(owner_id=owner_id, tipo=tipo, inspeccion=inspeccion, datos=datos)
    # robust: un fallo de este aviso no corta los demás callbacks ni llega a quien confirmó
    transaction.on_commit(lambda pk=evento.pk: despachar_evento(pk), robust=True)
    return evento


def _solo_extra(extra):
    if not extra:
        return None
    try:
        json.dumps(extra)
    except (TypeError, ValueError):
        return None
    return {_EXTRA: extra}


def _reconstruir_datos(evento):
    """Datos de un evento guardado sin base: la de ahora más el `extra` guardado."""
    from inspecciones.models import Inspeccion

    inspeccion = Inspeccion.objects.select_related('propiedad').get(pk=evento.inspeccion_id)
    base = construir_datos(evento.tipo, inspeccion)
    if evento.tipo == EVENTO_INSPECCION_REABIERTA:
        # Al reabrir no hay manifiesto vigente; si se volvió a completar, el de ahora no es el del evento
        base.pop('manifiesto', None)
    return {**((evento.datos or {}).get(_EXTRA) or {}), **base}


def emitir_evento(tipo, inspeccion, extra=None):
    return registrar_evento(tipo, inspeccion, extra=extra)


def despachar_evento(evento_id):
    """
    Reparte un evento PENDIENTE del outbox: una EntregaWebhook por destino, encoladas
    al confirmar. Idempotente (bloqueo de la fila y entrega única por destino) y sin
    llamadas de red. Nunca lanza: un fallo suma un intento y deja el evento PENDIENTE.
    """
    try:
        with transaction.atomic():
            evento = EventoSaliente.objects.select_for_update().filter(
                pk=evento_id, estado=EventoSaliente.Estado.PENDIENTE,
            ).first()
            if evento is None:
                return []
            if evento.inspeccion_id is None:
                logger.warning("Evento saliente %s descartado: su inspección se borró antes de despachar", evento.pk)
                _cerrar_evento(evento.pk, EventoSaliente.Estado.DESCARTADO, 'Inspección borrada antes de despachar.')
                return []
            datos = evento.datos
            if datos is None or _EXTRA in datos:
                datos = _reconstruir_datos(evento)
            datos = _con_enlace_pdf(evento.tipo, evento.inspeccion_id, datos)
            entregas = []
            for destino in _destinos_suscritos(evento.owner_id, evento.tipo, hasta=evento.creado):
                secuencia = _siguiente_secuencia(destino.pk)
                entregas.append(EntregaWebhook.objects.create(
                    destino=destino, evento=evento.tipo, event_id=evento.event_id, evento_saliente=evento,
                    inspeccion_id=evento.inspeccion_id, secuencia=secuencia,
                    payload=_sobre(evento.event_id, evento.tipo, datos, evento.creado, secuencia),
                ))
            _cerrar_evento(evento.pk, EventoSaliente.Estado.DESPACHADO)
            for entrega in entregas:
                # robust: un fallo al encolar no es un fallo del despacho (ya está DESPACHADO)
                transaction.on_commit(lambda pk=entrega.pk: encolar_entrega(pk), robust=True)
        return entregas
    except SoftTimeLimitExceeded:
        # El barrido se queda sin tiempo: no es un fallo de este evento
        raise
    except Exception as exc:
        logger.exception("No se pudo despachar el evento saliente %s", evento_id)
        try:
            _fallo_despacho(evento_id, exc)
        except Exception:
            # BD caída: el evento sigue PENDIENTE y lo recoge el barrido
            logger.exception("No se pudo anotar el fallo del evento saliente %s", evento_id)
        return []


def despachar_pendientes(limite=100):
    """Barrido periódico del outbox: despacha los PENDIENTE que el despacho en línea no
    repartió (proceso muerto, fallo) y descarta los que pasan de la ventana."""
    ahora = timezone.now()
    caducados = EventoSaliente.objects.filter(
        estado=EventoSaliente.Estado.PENDIENTE, creado__lt=ahora - VENTANA_DESPACHO,
    )
    por_tipo = dict(caducados.values_list('tipo').annotate(n=Count('pk')))
    descartados = caducados.update(estado=EventoSaliente.Estado.DESCARTADO, error='Fuera de la ventana de despacho.')
    if descartados:
        logger.error("%s eventos salientes descartados sin entregar (fuera de la ventana de despacho): %s",
                     descartados, por_tipo)
    ids = list(
        EventoSaliente.objects.filter(estado=EventoSaliente.Estado.PENDIENTE, creado__lt=ahora - GRACIA_DESPACHO)
        .order_by('creado').values_list('pk', flat=True)[:limite]
    )
    try:
        for pk in ids:
            despachar_evento(pk)
    except SoftTimeLimitExceeded:
        logger.warning("Barrido del outbox cortado por tiempo: el resto queda para la siguiente ejecución")
    despachados = EventoSaliente.objects.filter(pk__in=ids, estado=EventoSaliente.Estado.DESPACHADO).count()
    return {'despachados': despachados, 'descartados': descartados}


def _cerrar_evento(evento_id, estado, error=''):
    campos = {'estado': estado, 'error': error}
    if estado == EventoSaliente.Estado.DESPACHADO:
        campos['despachado'] = timezone.now()
    EventoSaliente.objects.filter(pk=evento_id, estado=EventoSaliente.Estado.PENDIENTE).update(**campos)


def _fallo_despacho(evento_id, exc):
    pendiente = EventoSaliente.objects.filter(pk=evento_id, estado=EventoSaliente.Estado.PENDIENTE)
    # Solo la clase: el mensaje puede arrastrar valores de la fila (el detalle va al log)
    pendiente.update(intentos=F('intentos') + 1, error=f'Error al despachar: {type(exc).__name__}')
    if pendiente.filter(intentos__gte=MAX_INTENTOS_DESPACHO).update(estado=EventoSaliente.Estado.DESCARTADO):
        logger.error("Evento saliente %s descartado tras %s intentos de despacho", evento_id, MAX_INTENTOS_DESPACHO)


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


def purgar_entregas_antiguas(dias=RETENCION_DIAS, lote=500):
    """
    Borra por lotes las entregas cerradas (ENTREGADA/FALLIDA) y los eventos del outbox
    cerrados (DESPACHADO/DESCARTADO) creados hace más de `dias`. Lo PENDIENTE nunca se
    toca. Devuelve cuántas filas se borraron.
    """
    corte = timezone.now() - timedelta(days=dias)
    entregas = EntregaWebhook.objects.filter(
        estado__in=[EntregaWebhook.Estado.ENTREGADA, EntregaWebhook.Estado.FALLIDA], creada__lt=corte,
    )
    eventos = EventoSaliente.objects.filter(
        estado__in=[EventoSaliente.Estado.DESPACHADO, EventoSaliente.Estado.DESCARTADO], creado__lt=corte,
    )
    return _borrar_por_lotes(entregas, lote) + _borrar_por_lotes(eventos, lote)


def _borrar_por_lotes(cerradas, lote):
    borradas = 0
    while True:
        # Por lotes de ids: un DELETE masivo bloquearía la tabla mucho rato
        ids = list(cerradas.order_by('pk').values_list('pk', flat=True)[:lote])
        if not ids:
            return borradas
        # Solo las filas de este modelo: borrar un evento deja sus entregas con SET_NULL
        borradas += cerradas.model.objects.filter(pk__in=ids).delete()[1].get(cerradas.model._meta.label, 0)
