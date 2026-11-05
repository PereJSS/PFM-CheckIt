"""
PDF firmado en el storage de Django (S3/R2 en producción, disco local en
desarrollo). Inspeccion.pdf_ruta guarda el nombre dentro del storage.
"""
import datetime
import logging
import secrets

from django.core.exceptions import SuspiciousFileOperation
from django.core.files import File
from django.core.files.storage import default_storage
from django.utils import timezone

logger = logging.getLogger(__name__)


def nombre_pdf(inspeccion_id):
    """Un nombre nuevo para cada versión del PDF. El bucket está bloqueado (WORM): un
    objeto no se puede borrar ni sobrescribir, así que nunca se reutiliza un nombre."""
    marca = timezone.now().astimezone(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    inspeccion_id = int(inspeccion_id)  # nunca una ruta: solo la PK
    return f'informes/{inspeccion_id}/Reclamacion_Fianza_{inspeccion_id}_{marca}_{secrets.token_hex(4)}_Firmada.pdf'


def guardar_pdf(inspeccion_id, ruta_local):
    """Sube el PDF firmado como una versión nueva y devuelve su nombre en el storage.
    Las versiones anteriores se conservan: no se borra nada."""
    nombre = nombre_pdf(inspeccion_id)
    with open(ruta_local, 'rb') as f:
        guardado = default_storage.save(nombre, File(f))
    # Si la tarea descarta luego esta versión, queda huérfana en el bucket (WORM): así se puede rastrear
    logger.info('PDF de la inspección %s subido como %s', inspeccion_id, guardado)
    return guardado


def abrir_pdf(inspeccion):
    """El PDF abierto en binario, o None si no existe (p. ej. una ruta local de un disco ya borrado)."""
    nombre = inspeccion.pdf_ruta
    if not nombre:
        return None
    try:
        if not default_storage.exists(nombre):
            return None
    except SuspiciousFileOperation:
        # Ruta absoluta de antes de usar el storage
        return None
    return default_storage.open(nombre, 'rb')
