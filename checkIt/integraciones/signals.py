"""
Detecta transiciones de Inspeccion para emitir webhooks salientes: pre_save
recuerda el estado anterior y post_save compara. Las transiciones guardadas con
un UPDATE condicional llegan por las señales propias de inspecciones.signals.

Orden: `inspecciones` va antes que `integraciones` en INSTALLED_APPS, así que su
post_save congela el manifiesto antes de que aquí se emita inspeccion.completada
(con la raíz en el payload). test_completada_lleva_la_raiz_congelada_sin_sello lo fija.
"""
import logging

from django.db import transaction
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from inspecciones.models import Inspeccion
from inspecciones.signals import manifiesto_sellado, pdf_listo

from .models import EVENTO_INSPECCION_COMPLETADA, EVENTO_MANIFIESTO_SELLADO, EVENTO_PDF_LISTO

logger = logging.getLogger(__name__)

CAMPOS_VIGILADOS = ('estado', 'pdf_estado')


@receiver(pre_save, sender=Inspeccion, dispatch_uid='integraciones_inspeccion_pre_save')
def recordar_estado_anterior(sender, instance, update_fields=None, raw=False, **kwargs):
    instance._webhook_anterior = None
    if raw or instance.pk is None:
        return
    # Guardados parciales que no tocan los campos vigilados: sin consulta extra
    if update_fields is not None and not set(update_fields) & set(CAMPOS_VIGILADOS):
        return
    instance._webhook_anterior = (
        Inspeccion.objects.filter(pk=instance.pk).values(*CAMPOS_VIGILADOS).first()
    )


@receiver(post_save, sender=Inspeccion, dispatch_uid='integraciones_inspeccion_post_save')
def emitir_transiciones(sender, instance, created=False, raw=False, **kwargs):
    if raw:
        return
    anterior = getattr(instance, '_webhook_anterior', None) or {}
    instance._webhook_anterior = None
    if not created and not anterior:
        return

    eventos = []
    if instance.estado == Inspeccion.Estado.COMPLETADA and anterior.get('estado') != Inspeccion.Estado.COMPLETADA:
        eventos.append(EVENTO_INSPECCION_COMPLETADA)
    if instance.pdf_estado == Inspeccion.PdfEstado.LISTO and anterior.get('pdf_estado') != Inspeccion.PdfEstado.LISTO:
        eventos.append(EVENTO_PDF_LISTO)
    for tipo in eventos:
        _emitir(tipo, instance)


@receiver(pdf_listo, dispatch_uid='integraciones_pdf_listo')
def emitir_pdf_listo(sender, inspeccion, **kwargs):
    _emitir(EVENTO_PDF_LISTO, inspeccion)


@receiver(manifiesto_sellado, dispatch_uid='integraciones_manifiesto_sellado')
def emitir_manifiesto_sellado(sender, inspeccion, raiz=None, **kwargs):
    # Reabierta entre el sello y el aviso: el payload ya no llevaría la raíz sellada
    if raiz and inspeccion.manifiesto_raiz != raiz:
        logger.info('Manifiesto de la inspección %s reabierto tras sellarse: no se emite', inspeccion.pk)
        return
    _emitir(EVENTO_MANIFIESTO_SELLADO, inspeccion)


def _emitir(tipo, inspeccion):
    from .salientes import emitir_evento

    try:
        # Savepoint: un fallo al emitir no debe romper el guardado de la inspección
        with transaction.atomic():
            emitir_evento(tipo, inspeccion)
    except Exception:
        logger.exception("No se pudo emitir el webhook %s de la inspección %s", tipo, inspeccion.pk)
