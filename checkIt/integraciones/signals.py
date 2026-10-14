"""
Detecta transiciones de Inspeccion para emitir webhooks salientes, sin tocar la
app inspecciones: pre_save recuerda el estado anterior y post_save compara.
"""
import logging

from django.db import transaction
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from inspecciones.models import Inspeccion

from .models import EVENTO_INSPECCION_COMPLETADA, EVENTO_PDF_LISTO

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
    if not eventos:
        return

    from .salientes import emitir_evento

    for tipo in eventos:
        try:
            # Savepoint: un fallo al emitir no debe romper el guardado de la inspección
            with transaction.atomic():
                emitir_evento(tipo, instance)
        except Exception:
            logger.exception("No se pudo emitir el webhook %s de la inspección %s", tipo, instance.pk)
