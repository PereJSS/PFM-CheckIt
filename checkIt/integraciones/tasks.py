from celery import shared_task


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
