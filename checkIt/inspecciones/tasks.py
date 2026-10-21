from celery import shared_task
import logging
import os
import tempfile
import time

from .services.tsa import solicitar_sello_tiempo

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60, time_limit=300, soft_time_limit=270)
def generar_pdf_async(self, inspeccion_id):
    from .models import Inspeccion
    from .services import pdf_signer
    from .services.almacen import guardar_pdf
    from .services.manifiesto import sellar_manifiesto
    from .services.pdf_generator import generar_informe_base

    inspeccion = Inspeccion.objects.get(pk=inspeccion_id)
    try:
        inspeccion.pdf_estado = Inspeccion.PdfEstado.GENERANDO
        inspeccion.save(update_fields=['pdf_estado'])

        # Si el manifiesto aún no tiene sello, se intenta antes para que el PDF lo recoja.
        # Se recarga siempre: aunque la TSA falle, la raíz puede haberse congelado
        if not inspeccion.manifiesto_tsa_token:
            sellar_manifiesto(inspeccion.pk)
            inspeccion.refresh_from_db()

        # Los ficheros locales son intermedios (se borran al salir): el PDF válido vive en el storage
        with tempfile.TemporaryDirectory(prefix='checkit-pdf-') as directorio:
            input_pdf = generar_informe_base(inspeccion, os.path.join(directorio, 'base.pdf'))
            ruta_firmada = os.path.join(directorio, 'firmada.pdf')
            pdf_signer.firmar_pdf_reclamacion(input_pdf, ruta_firmada)
            pdf_ruta = guardar_pdf(inspeccion.pk, ruta_firmada)

        # Solo si la raíz no cambió durante la generación (p. ej., por una reapertura)
        raiz = inspeccion.manifiesto_raiz
        guardado = Inspeccion.objects.filter(pk=inspeccion.pk, manifiesto_raiz=raiz).update(
            pdf_estado=Inspeccion.PdfEstado.LISTO, pdf_ruta=pdf_ruta, pdf_manifiesto_raiz=raiz,
        )
        if not guardado:
            logger.warning('La inspección %s cambió mientras se generaba su PDF: se descarta', inspeccion.pk)
            Inspeccion.objects.filter(pk=inspeccion.pk, pdf_estado=Inspeccion.PdfEstado.GENERANDO).update(
                pdf_estado=Inspeccion.PdfEstado.PENDIENTE,
            )
    except Exception as exc:
        inspeccion.pdf_estado = Inspeccion.PdfEstado.ERROR
        inspeccion.save(update_fields=['pdf_estado'])
        raise self.retry(exc=exc)


@shared_task(time_limit=60, soft_time_limit=50, ignore_result=True)
def sellar_manifiesto_async(inspeccion_id):
    """Congela y sella el manifiesto Merkle al completar la inspección; si la TSA falla, lo reintenta el re-sellado periódico."""
    from .services.manifiesto import sellar_manifiesto
    return sellar_manifiesto(inspeccion_id)


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
    """Reintenta el sellado TSA de evidencias guardadas en modo degradado (sin tsa_token)
    y de los manifiestos de inspecciones completadas que siguen sin sello."""
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
        sin_tiempo = False
        for n, evidencia in enumerate(pendientes):
            if n and pausa:
                time.sleep(pausa)
            procesadas += 1
            Evidencia.objects.filter(pk=evidencia.pk).update(tsa_ultimo_intento=timezone.now())
            try:
                tsa_data = solicitar_sello_tiempo(evidencia.hash_sha256)
            except SoftTimeLimitExceeded:
                logger.warning('Re-sellado TSA interrumpido por límite de tiempo')
                sin_tiempo = True
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
        # Los manifiestos usan su propia TSA: los fallos de evidencias no los bloquean
        manifiestos = 0 if sin_tiempo else _resellar_manifiestos(lote, max_fallos, pausa)
        return {'procesadas': procesadas, 'selladas': selladas, 'manifiestos_sellados': manifiestos}
    finally:
        cache.delete(RESELLO_LOCK_KEY)


def _resellar_manifiestos(lote, max_fallos, pausa):
    """Sella manifiestos pendientes de inspecciones completadas. Devuelve cuántos quedan sellados."""
    from celery.exceptions import SoftTimeLimitExceeded
    from django.db.models import F
    from .models import Inspeccion
    from .services.manifiesto import sellar_manifiesto

    pendientes = list(
        Inspeccion.objects.filter(estado=Inspeccion.Estado.COMPLETADA, manifiesto_tsa_token__isnull=True)
        .order_by(F('manifiesto_tsa_ultimo_intento').asc(nulls_first=True), 'pk')
        .values_list('pk', flat=True)[:lote]
    )
    sellados = fallos_seguidos = 0
    for n, inspeccion_id in enumerate(pendientes):
        if n and pausa:
            time.sleep(pausa)
        try:
            ok = sellar_manifiesto(inspeccion_id)
        except SoftTimeLimitExceeded:
            logger.warning('Re-sellado de manifiestos interrumpido por límite de tiempo')
            break
        except Exception:
            logger.warning('Fallo sellando el manifiesto de la inspección %s', inspeccion_id, exc_info=True)
            ok = False
        if not ok:
            fallos_seguidos += 1
            if fallos_seguidos >= max_fallos:
                logger.warning('Re-sellado de manifiestos cortado tras %s fallos consecutivos', fallos_seguidos)
                break
            continue
        fallos_seguidos = 0
        sellados += 1
    if pendientes:
        logger.info('Re-sellado TSA: %s/%s manifiestos sellados', sellados, len(pendientes))
    return sellados
