from celery import shared_task
from decouple import config


@shared_task(bind=True, max_retries=3, default_retry_delay=30, time_limit=60, soft_time_limit=50)
def procesar_evento_pms(self, evento_id):
    from .services import marcar_error, procesar_evento

    try:
        procesar_evento(evento_id)
    except Exception as exc:
        if self.request.retries >= self.max_retries:
            # Sin más reintentos: lo dejamos visible como ERROR en /integraciones/eventos/
            marcar_error(evento_id, f"Fallo tras {self.max_retries} reintentos: {exc}")
            raise
        # La transacción se revierte y el evento sigue en RECIBIDO, así que reintentar es seguro
        raise self.retry(exc=exc)



# Leído aquí (no en salientes) para no importar modelos al cargar las tareas
WEBHOOK_MAX_REINTENTOS = config('WEBHOOK_SALIENTE_MAX_REINTENTOS', default=5, cast=int)


@shared_task(bind=True, max_retries=WEBHOOK_MAX_REINTENTOS, time_limit=60, soft_time_limit=50)
def entregar_webhook(self, entrega_id):
    from celery.exceptions import SoftTimeLimitExceeded

    from .salientes import BACKOFF_BASE, REINTENTAR, entregar, marcar_fallida

    try:
        resultado = entregar(entrega_id)
    except SoftTimeLimitExceeded:
        # Fuera de la llamada HTTP (p. ej. BD lenta): se trata como reintentable
        resultado = REINTENTAR
    if resultado != REINTENTAR:
        return resultado
    if self.request.retries >= self.max_retries:
        marcar_fallida(entrega_id, f"Fallo tras {self.max_retries} reintentos.")
        return 'FALLIDA'
    # Backoff exponencial: 30s, 60s, 120s, 240s, 480s con la base por defecto
    raise self.retry(countdown=BACKOFF_BASE * (2 ** self.request.retries))


@shared_task(time_limit=120, soft_time_limit=100)
def reencolar_entregas_atascadas():
    """Tarea periódica (Celery beat): recupera entregas PENDIENTE sin movimiento."""
    from .salientes import reencolar_atascadas

    return reencolar_atascadas()


@shared_task(time_limit=600, soft_time_limit=540)
def purgar_entregas_webhook():
    """Tarea periódica (Celery beat): borra las entregas cerradas más antiguas que la retención."""
    from .salientes import purgar_entregas_antiguas

    return purgar_entregas_antiguas()
