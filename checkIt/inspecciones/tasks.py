from celery import shared_task
import logging
import time

from .services.tsa import solicitar_sello_tiempo

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60, time_limit=300, soft_time_limit=270)
def generar_pdf_async(self, inspeccion_id):
    from .models import Inspeccion
    from .services.pdf_generator import generar_informe_base
    from .services.pdf_signer import firmar_pdf_reclamacion

    inspeccion = Inspeccion.objects.get(pk=inspeccion_id)
    try:
        inspeccion.pdf_estado = Inspeccion.PdfEstado.GENERANDO
        inspeccion.save(update_fields=['pdf_estado'])

        input_pdf = generar_informe_base(inspeccion)
        ruta_final = f"tmp/Reclamacion_Fianza_{inspeccion.pk}_Firmada.pdf"
        firmar_pdf_reclamacion(input_pdf, ruta_final)

        inspeccion.pdf_estado = Inspeccion.PdfEstado.LISTO
        inspeccion.pdf_ruta = ruta_final
        inspeccion.save(update_fields=['pdf_estado', 'pdf_ruta'])
    except Exception as exc:
        inspeccion.pdf_estado = Inspeccion.PdfEstado.ERROR
        inspeccion.save(update_fields=['pdf_estado'])
        raise self.retry(exc=exc)


RESELLO_LOCK_KEY = 'tsa:resello:lock'
RESELLO_LOCK_TIMEOUT = 300  # igual que time_limit: si el worker muere, el lock caduca


def _setting_num(nombre, defecto, tipo=int, minimo=0):
    from django.conf import settings
    try:
        return max(tipo(getattr(settings, nombre, defecto)), minimo)
    except (TypeError, ValueError):
        return defecto


@shared_task(time_limit=300, soft_time_limit=270)
def resellar_evidencias_pendientes():
    """Reintenta el sellado TSA de evidencias guardadas en modo degradado (sin tsa_token)."""
    from celery.exceptions import SoftTimeLimitExceeded
    from django.core.cache import cache
    from django.db.models import F
    from django.utils import timezone
    from .models import Evidencia

    if not cache.add(RESELLO_LOCK_KEY, 1, RESELLO_LOCK_TIMEOUT):
        logger.info('Re-sellado TSA ya en ejecución; se omite esta pasada')
        return {'procesadas': 0, 'selladas': 0, 'omitida': True}

    try:
        lote = _setting_num('TSA_RESELLO_BATCH_SIZE', 20, int, 1)
        max_fallos = _setting_num('TSA_RESELLO_MAX_FALLOS', 3, int, 1)
        pausa = _setting_num('TSA_RESELLO_PAUSA_SEG', 1.0, float, 0.0)

        # Primero las nunca intentadas y luego las de intento más antiguo: unas
        # pocas evidencias que siempre fallan no bloquean el resto de la cola
        pendientes = list(
            Evidencia.objects.filter(hash_sha256__isnull=False, tsa_token__isnull=True)
            .exclude(hash_sha256='')
            .order_by(F('tsa_ultimo_intento').asc(nulls_first=True), 'pk')[:lote]
        )
        procesadas = selladas = fallos_seguidos = 0
        for n, evidencia in enumerate(pendientes):
            if n and pausa:
                time.sleep(pausa)
            procesadas += 1
            Evidencia.objects.filter(pk=evidencia.pk).update(tsa_ultimo_intento=timezone.now())
            try:
                tsa_data = solicitar_sello_tiempo(evidencia.hash_sha256)
            except SoftTimeLimitExceeded:
                logger.warning('Re-sellado TSA interrumpido por límite de tiempo')
                break
            except Exception:
                logger.warning('Fallo re-sellando evidencia %s', evidencia.pk, exc_info=True)
                tsa_data = None
            if not tsa_data or not tsa_data.get('token'):
                logger.debug('Evidencia %s sigue sin sello', evidencia.pk)
                fallos_seguidos += 1
                if fallos_seguidos >= max_fallos:
                    logger.warning(
                        'Re-sellado TSA cortado tras %s fallos consecutivos', fallos_seguidos
                    )
                    break
                continue
            fallos_seguidos = 0
            # Escritura condicional: no pisa un sello ya puesto ni falla si se borró.
            actualizadas = Evidencia.objects.filter(
                pk=evidencia.pk, tsa_token__isnull=True
            ).update(
                tsa_token=tsa_data.get('token'),
                tsa_timestamp=tsa_data.get('timestamp'),
            )
            logger.debug('Evidencia %s re-sellada (filas=%s)', evidencia.pk, actualizadas)
            selladas += actualizadas

        if procesadas and not selladas:
            logger.warning('Re-sellado TSA: 0/%s evidencias selladas', procesadas)
        else:
            logger.info('Re-sellado TSA: %s/%s evidencias selladas', selladas, procesadas)
        return {'procesadas': procesadas, 'selladas': selladas}
    finally:
        cache.delete(RESELLO_LOCK_KEY)
