"""
Manifiesto Merkle por inspección.

Al completarse la inspección se congela, en la misma transacción, la lista
ordenada de hashes de sus evidencias, y después se sella con la TSA del perfil
``manifiesto`` solo la raíz del árbol: un sello por inspección en vez de uno
por foto. Cada foto se puede probar después con su ruta de inclusión (ver
services/merkle.py).

Mientras la inspección siga completada, el manifiesto no se recalcula: el
reintento sella exactamente la raíz congelada. Si se reabre, el manifiesto
sellado se archiva en ``manifiestos_anteriores`` y el siguiente completado
congela uno nuevo.
"""
import base64
import logging
import re

from django.db import transaction
from django.utils import timezone

from ..models import Inspeccion
from . import merkle
from .tsa import PERFIL_MANIFIESTO, solicitar_sello_tiempo

logger = logging.getLogger(__name__)

VERSION_MANIFIESTO = 1
_HASH_HEX = re.compile(r'^[0-9a-f]{64}$')

_CAMPOS_VACIOS = {
    'manifiesto': None,
    'manifiesto_raiz': None,
    'manifiesto_tsa_token': None,
    'manifiesto_tsa_timestamp': None,
    'manifiesto_tsa_url': '',
    'manifiesto_tsa_cualificada': False,
    'manifiesto_tsa_verificado': False,
    'manifiesto_tsa_ultimo_intento': None,
    # El PDF del completado anterior no describe la inspección reabierta
    'pdf_estado': Inspeccion.PdfEstado.PENDIENTE,
}


def construir_manifiesto(inspeccion):
    """Manifiesto canónico: hojas ordenadas por id de evidencia y raíz RFC 6962 en hex."""
    hojas = []
    for evidencia_id, hash_sha256 in inspeccion.evidencias.order_by('pk').values_list('pk', 'hash_sha256'):
        hash_txt = (hash_sha256 or '').strip().lower()
        if _HASH_HEX.match(hash_txt):
            hojas.append({'evidencia': evidencia_id, 'sha256': hash_txt})
        else:
            # La subida ya rechaza hashes mal formados: esto solo afecta a datos antiguos
            logger.warning(
                'Evidencia %s sin hash SHA-256 válido: queda fuera del manifiesto de la inspección %s',
                evidencia_id, inspeccion.pk,
            )
    raiz = merkle.raiz([bytes.fromhex(h['sha256']) for h in hojas]).hex()
    return {
        'version': VERSION_MANIFIESTO,
        'inspeccion': inspeccion.pk,
        'algoritmo': 'sha256',
        'arbol': 'RFC6962-SHA256',
        'hojas': hojas,
        'raiz': raiz,
    }


def congelar_manifiesto(inspeccion_id):
    """Congela el manifiesto si aún no lo está. Sin red: se llama dentro de la transacción del completado.

    Bloquea la fila de la inspección para que no entren evidencias mientras se leen
    (upload_evidence toma el mismo bloqueo).
    """
    with transaction.atomic():
        inspeccion = Inspeccion.objects.select_for_update().filter(pk=inspeccion_id).first()
        if inspeccion is None or inspeccion.manifiesto_raiz:
            return
        manifiesto = construir_manifiesto(inspeccion)
        Inspeccion.objects.filter(pk=inspeccion_id, manifiesto_raiz__isnull=True).update(
            manifiesto=manifiesto, manifiesto_raiz=manifiesto['raiz'],
        )


def reabrir_manifiesto(inspeccion_id):
    """Al salir de COMPLETADA: archiva el manifiesto sellado (si lo hay) y deja los campos vacíos."""
    with transaction.atomic():
        inspeccion = Inspeccion.objects.select_for_update().filter(pk=inspeccion_id).first()
        if inspeccion is None or not inspeccion.manifiesto_raiz:
            return
        anteriores = list(inspeccion.manifiestos_anteriores or [])
        if inspeccion.manifiesto_tsa_token:
            ts = inspeccion.manifiesto_tsa_timestamp
            anteriores.append({
                'manifiesto': inspeccion.manifiesto,
                'raiz': inspeccion.manifiesto_raiz,
                'tsa_token': base64.b64encode(bytes(inspeccion.manifiesto_tsa_token)).decode('ascii'),
                'tsa_timestamp': ts.isoformat() if ts else None,
                'tsa_url': inspeccion.manifiesto_tsa_url,
                'tsa_cualificada': inspeccion.manifiesto_tsa_cualificada,
                'tsa_verificado': inspeccion.manifiesto_tsa_verificado,
                'archivado': timezone.now().isoformat(),
            })
        Inspeccion.objects.filter(pk=inspeccion_id).update(manifiestos_anteriores=anteriores, **_CAMPOS_VACIOS)


def sellar_manifiesto(inspeccion_id):
    """Congela (si hace falta) y sella el manifiesto. Devuelve True si queda sellado.

    Idempotente y seguro entre procesos: el sello se guarda con un UPDATE
    condicional, así que nunca se pisa uno ya puesto ni se sella una raíz que
    ya no es la vigente. La llamada a la TSA va fuera de cualquier transacción.
    """
    inspeccion = Inspeccion.objects.filter(pk=inspeccion_id).first()
    if inspeccion is None or inspeccion.estado != Inspeccion.Estado.COMPLETADA:
        return False
    if inspeccion.manifiesto_tsa_token:
        return True

    # Inspecciones completadas sin pasar por la señal (p. ej. con queryset.update())
    if not inspeccion.manifiesto_raiz:
        congelar_manifiesto(inspeccion_id)
    pendientes = Inspeccion.objects.filter(pk=inspeccion_id)
    raiz = pendientes.values_list('manifiesto_raiz', flat=True).first()
    if not raiz:
        return False

    pendientes.update(manifiesto_tsa_ultimo_intento=timezone.now())
    datos = solicitar_sello_tiempo(raiz, perfil=PERFIL_MANIFIESTO)
    if not datos or not datos.get('token'):
        logger.warning('Manifiesto de la inspección %s sin sellar (TSA no disponible)', inspeccion_id)
        return False

    from ..signals import anunciar, manifiesto_sellado

    # El aviso va en la transacción del sello: si el worker muere tras el commit,
    # el evento ya está en el outbox de webhooks
    with transaction.atomic():
        sellada = pendientes.filter(
            estado=Inspeccion.Estado.COMPLETADA, manifiesto_raiz=raiz, manifiesto_tsa_token__isnull=True,
        ).update(
            manifiesto_tsa_token=datos['token'],
            manifiesto_tsa_timestamp=datos.get('timestamp'),
            manifiesto_tsa_url=datos.get('url') or '',
            manifiesto_tsa_cualificada=bool(datos.get('cualificada')),
            manifiesto_tsa_verificado=bool(datos.get('verificado')),
        )
        if sellada:
            anunciar(manifiesto_sellado, inspeccion_id, raiz=raiz)
    return pendientes.filter(manifiesto_raiz=raiz, manifiesto_tsa_token__isnull=False).exists()
