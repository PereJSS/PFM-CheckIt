"""
Ciclo de vida del manifiesto Merkle según el estado de la inspección.

pre_save recuerda el estado anterior y post_save compara:
- al pasar a COMPLETADA se congela el manifiesto en la misma transacción y el
  sellado (con red) se encola tras el commit;
- al salir de COMPLETADA (reapertura) se archiva el manifiesto sellado.

Las transiciones que se guardan con un UPDATE condicional (PDF listo, manifiesto
sellado) no disparan post_save: se anuncian con señales propias tras el commit.
"""
import logging

from django.db import transaction
from django.db.models.signals import post_save, pre_save
from django.dispatch import Signal, receiver

from .models import Inspeccion

logger = logging.getLogger(__name__)

# Argumentos: inspeccion (recargada de la base de datos tras el commit) y, en
# manifiesto_sellado, raiz (la que se selló: puede no ser ya la vigente)
pdf_listo = Signal()
manifiesto_sellado = Signal()


def enviar_al_confirmar(senal, inspeccion_id, **datos):
    """Envía la señal tras el commit sin propagar errores: quien llama ya guardó
    el cambio (PDF listo, sello) y un fallo al avisar no debe deshacerlo ni reintentarlo."""
    def enviar():
        try:
            inspeccion = Inspeccion.objects.select_related('propiedad').filter(pk=inspeccion_id).first()
            if inspeccion is None:
                logger.info('Inspección %s borrada antes de anunciar su cambio: no se avisa', inspeccion_id)
                return
            for receptor, resultado in senal.send_robust(sender=Inspeccion, inspeccion=inspeccion, **datos):
                if isinstance(resultado, Exception):
                    logger.error('El receptor %r falló con la inspección %s', receptor, inspeccion_id,
                                 exc_info=resultado)
        except Exception:
            logger.exception('No se pudo anunciar el cambio de la inspección %s', inspeccion_id)

    transaction.on_commit(enviar)


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
