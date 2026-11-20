import logging

from celery import shared_task
from django.utils import timezone
from rest_framework_simplejwt.token_blacklist.models import OutstandingToken

logger = logging.getLogger(__name__)

PURGA_LOTE = 5000


@shared_task(name='users.tasks.purgar_tokens_caducados', ignore_result=True)
def purgar_tokens_caducados():
    """Borra los refresh tokens caducados (y con ellos sus entradas de la lista negra):
    un refresh caducado ya no se acepta, así que anotarlo no protege nada. Por lotes: la
    cascada obliga a Django a cargar las filas y, tras semanas sin worker, serían muchas."""
    ahora = timezone.now()
    borrados = 0
    while True:
        lote = list(
            OutstandingToken.objects.filter(expires_at__lte=ahora).values_list('pk', flat=True)[:PURGA_LOTE]
        )
        if not lote:
            break
        borrados += OutstandingToken.objects.filter(pk__in=lote).delete()[0]
    logger.info('Refresh tokens caducados purgados: %s filas', borrados)
    return borrados
