import logging

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from inspecciones.models import Inspeccion
from inspecciones.services.checklist import instanciar_checklist, plantilla_efectiva
from propiedades.models import Propiedad

from .models import WebhookEvent

EVENTO_CHECKOUT = 'reservation.checkout'

logger = logging.getLogger(__name__)


def _procesar_checkout(evento):
    # Reserva finalizada -> inspección pendiente (sin operario, el admin la asigna)
    external_id = (evento.payload.get('data') or {}).get('property_id')
    propiedad = Propiedad.objects.filter(owner=evento.owner, external_id__iexact=external_id).first()
    if propiedad is None:
        evento.estado = WebhookEvent.Estado.ERROR
        evento.error = f"No existe ninguna propiedad con external_id '{external_id}'."
        return
    evento.inspeccion = Inspeccion.objects.create(propiedad=propiedad, operario=None)
    # La plantilla de la propiedad (o la por defecto del cliente); sin ninguna, como hasta ahora.
    # Una plantilla guardada sin validar no hace perder el check-out: la inspección se crea
    # sin checklist, se avisa en el evento y el admin la aplica tras corregirla
    try:
        with transaction.atomic():
            instanciar_checklist(evento.inspeccion)
    except ValidationError as e:
        plantilla = plantilla_efectiva(propiedad)
        nombre = plantilla.nombre if plantilla else '?'
        logger.error(
            "Checkout %s: inspección %s creada sin checklist, la plantilla %s no es válida: %s",
            evento.pk, evento.inspeccion.pk, getattr(plantilla, 'pk', None), e.messages,
        )
        evento.error = (
            f"Inspección creada sin checklist: la plantilla «{nombre}» no es válida "
            f"({'; '.join(e.messages)}). Corrígela y aplícala desde la inspección."
        )[:1000]
    evento.estado = WebhookEvent.Estado.PROCESADO


HANDLERS = {
    EVENTO_CHECKOUT: _procesar_checkout,
}


def procesar_evento(evento_id):
    """
    Procesa un WebhookEvent en estado RECIBIDO. Idempotente: el bloqueo de fila
    evita que dos workers creen dos inspecciones para el mismo evento.
    """
    with transaction.atomic():
        evento = WebhookEvent.objects.select_for_update().get(pk=evento_id)
        if evento.estado != WebhookEvent.Estado.RECIBIDO:
            return evento

        handler = HANDLERS.get(evento.tipo)
        if handler is None:
            evento.estado = WebhookEvent.Estado.IGNORADO
        else:
            handler(evento)

        evento.procesado = timezone.now()
        evento.save(update_fields=['estado', 'error', 'inspeccion', 'procesado'])
        return evento


def marcar_error(evento_id, mensaje):
    # Solo si sigue pendiente: no pisa un evento que otro worker ya completó
    WebhookEvent.objects.filter(pk=evento_id, estado=WebhookEvent.Estado.RECIBIDO).update(
        estado=WebhookEvent.Estado.ERROR,
        error=mensaje[:1000],
        procesado=timezone.now(),
    )


def reabrir_para_reproceso(evento):
    """
    Un reenvío del PMS de un evento en ERROR lo devuelve a RECIBIDO para que se
    reintente (p. ej. tras enlazar el external_id que faltaba). Devuelve True si
    el evento queda pendiente de procesar.
    """
    if evento.estado == WebhookEvent.Estado.ERROR:
        WebhookEvent.objects.filter(pk=evento.pk, estado=WebhookEvent.Estado.ERROR).update(
            estado=WebhookEvent.Estado.RECIBIDO, error='', procesado=None,
        )
        evento.refresh_from_db()
    return evento.estado == WebhookEvent.Estado.RECIBIDO
