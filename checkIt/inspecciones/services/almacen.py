"""
PDF firmado en el storage de Django (S3/R2 en producción, disco local en
desarrollo). Inspeccion.pdf_ruta guarda el nombre dentro del storage.
"""
from django.core.exceptions import SuspiciousFileOperation
from django.core.files import File
from django.core.files.storage import default_storage


def nombre_pdf(inspeccion_id):
    return f'informes/Reclamacion_Fianza_{inspeccion_id}_Firmada.pdf'


def guardar_pdf(inspeccion_id, ruta_local):
    """Sube el PDF firmado y devuelve su nombre en el storage."""
    nombre = nombre_pdf(inspeccion_id)
    # El storage no sobrescribe (añadiría un sufijo): se borra la versión anterior
    if default_storage.exists(nombre):
        default_storage.delete(nombre)
    with open(ruta_local, 'rb') as f:
        return default_storage.save(nombre, File(f))


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
