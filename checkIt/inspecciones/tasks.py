from celery import shared_task
import logging

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


@shared_task(time_limit=600, soft_time_limit=570)
def resellar_evidencias_pendientes():
    """Reintenta el sellado TSA de evidencias guardadas en modo degradado (sin tsa_token)."""
    from django.conf import settings
    from .models import Evidencia

    try:
        lote = max(int(settings.TSA_RESELLO_BATCH_SIZE), 1)
    except (TypeError, ValueError):
        lote = 50

    pendientes = list(
        Evidencia.objects.filter(hash_sha256__isnull=False, tsa_token__isnull=True)
        .exclude(hash_sha256='')
        .order_by('pk')[:lote]
    )
    selladas = 0
    for evidencia in pendientes:
        try:
            tsa_data = solicitar_sello_tiempo(evidencia.hash_sha256)
        except Exception:
            logger.warning('Fallo re-sellando evidencia %s', evidencia.pk, exc_info=True)
            continue
        if not tsa_data:
            logger.warning('TSA sin respuesta para evidencia %s; se reintentará', evidencia.pk)
            continue
        evidencia.tsa_timestamp = tsa_data.get('timestamp')
        evidencia.tsa_token = tsa_data.get('token')
        evidencia.save(update_fields=['tsa_timestamp', 'tsa_token'])
        selladas += 1

    logger.info('Re-sellado TSA: %s/%s evidencias selladas', selladas, len(pendientes))
    return {'procesadas': len(pendientes), 'selladas': selladas}
