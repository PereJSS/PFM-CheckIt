"""
Ciclo de vida del manifiesto Merkle según el estado de la inspección.

pre_save recuerda el estado anterior y post_save compara:
- al pasar a COMPLETADA se congela el manifiesto en la misma transacción y el
  sellado (con red) se encola tras el commit;
- al salir de COMPLETADA (reapertura) se archiva el manifiesto sellado.
"""
import logging

from django.db import transaction
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from .models import Inspeccion

logger = logging.getLogger(__name__)


@receiver(pre_save, sender=Inspeccion, dispatch_uid='inspecciones_manifiesto_pre_save')
def recordar_estado(sender, instance, update_fields=None, raw=False, **kwargs):
    instance._estado_anterior = None
    if raw or instance.pk is None:
        return
    if update_fields is not None and 'estado' not in update_fields:
        return
    instance._estado_anterior = (
        Inspeccion.objects.filter(pk=instance.pk).values_list('estado', flat=True).first()
    )


@receiver(post_save, sender=Inspeccion, dispatch_uid='inspecciones_manifiesto_post_save')
def encolar_sellado_manifiesto(sender, instance, created=False, raw=False, **kwargs):
    anterior = getattr(instance, '_estado_anterior', None)
    instance._estado_anterior = None
    if raw:
        return
    completada = Inspeccion.Estado.COMPLETADA

    if instance.estado != completada:
        if anterior == completada:
            from .services.manifiesto import reabrir_manifiesto
            reabrir_manifiesto(instance.pk)
        return
    if not created and anterior in (None, completada):
        return

    from .services.manifiesto import congelar_manifiesto
    congelar_manifiesto(instance.pk)
    transaction.on_commit(lambda pk=instance.pk: _encolar(pk))


def _encolar(inspeccion_id):
    from .tasks import sellar_manifiesto_async

    try:
        sellar_manifiesto_async.apply_async(args=[inspeccion_id], retry=False)
    except Exception as exc:
        # Sin broker no sellamos en línea (bloquearía la petición con llamadas a la TSA):
        # lo recoge el re-sellado periódico
        logger.warning(
            "Broker Celery no disponible (%s); manifiesto de la inspección %s queda pendiente",
            type(exc).__name__, inspeccion_id,
        )
