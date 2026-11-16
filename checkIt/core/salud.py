"""Rutas de salud para Render y para un monitor externo.

- ``/healthz``: la vigila Render (Health Check Path). Solo dice que Django responde: si
  dependiera de la BD o de Celery, un fallo de esos sacaría del tráfico la única instancia
  y la reiniciaría en bucle, y la web entera caería por algo que solo la degrada.
- ``/healthz/celery``: la consulta un monitor externo que avisa por correo. Da 200 si el
  último latido de Celery es reciente: prueba que el beat programa, el broker reparte y el
  worker ejecuta. Si Celery muere del todo, ``start.sh`` reinicia la instancia; esto cubre
  lo que no muere (worker colgado, broker perdido).

Las sirve un middleware colocado el primero: responde antes de validar el Host (Render
puede llamar con la IP interna) y sin sesiones, CORS ni autenticación. No devuelve nada
interno: solo el estado y los segundos desde el último latido.
"""
import logging
import time

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse, HttpResponseNotAllowed, JsonResponse

logger = logging.getLogger(__name__)

CLAVE_LATIDO = 'salud:celery:latido'
RUTA_WEB = '/healthz'
RUTA_CELERY = '/healthz/celery'


def registrar_latido():
    """Lo llama la tarea periódica ``core.latido`` desde el worker."""
    cache.set(CLAVE_LATIDO, time.time(), timeout=None)


def _estado_celery():
    """Devuelve (estado, segundos desde el último latido o None)."""
    try:
        ultimo = cache.get(CLAVE_LATIDO)
    except Exception as exc:  # cualquier fallo del backend de caché (Redis caído, timeout...)
        logger.warning('Salud de Celery: la caché no responde: %r', exc)
        return 'cache_no_responde', None
    if not isinstance(ultimo, (int, float)):
        return 'sin_latido', None
    segundos = max(int(time.time() - ultimo), 0)
    if segundos > settings.SALUD_LATIDO_MAX_SEG:
        return 'atrasado', segundos
    return 'ok', segundos


def _sin_cache(respuesta):
    respuesta['Cache-Control'] = 'no-store'
    # Va antes de SecurityMiddleware, que es quien la pone en el resto de respuestas
    respuesta['X-Content-Type-Options'] = 'nosniff'
    return respuesta


class SaludMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path_info not in (RUTA_WEB, RUTA_CELERY):
            return self.get_response(request)
        if request.method not in ('GET', 'HEAD'):
            return _sin_cache(HttpResponseNotAllowed(['GET', 'HEAD']))
        if request.path_info == RUTA_WEB:
            return _sin_cache(HttpResponse(b'ok', content_type='text/plain'))
        estado, segundos = _estado_celery()
        return _sin_cache(JsonResponse(
            {'celery': estado, 'segundos_desde_latido': segundos},
            status=200 if estado == 'ok' else 503,
        ))
