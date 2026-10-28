import os
from datetime import datetime, timedelta, timezone

from django.conf import settings
from django.core import checks

from .services.tsa import PERFIL_EVIDENCIAS, PERFIL_MANIFIESTO, _prefijo, validar_cert_tsa

CLAVE_POR_DEFECTO = 'certs/signing.key'
AVISO_CADUCIDAD = timedelta(days=30)
MAX_CERT_TSA = 1_000_000  # un certificado ocupa unos KB; evita leer algo enorme por error


@checks.register(checks.Tags.security, deploy=False)
def check_clave_firma_por_defecto(app_configs, **kwargs):
    # Leído en tiempo de ejecución, igual que pdf_signer, para que los tests puedan parchear el entorno
    if settings.DEBUG or os.getenv('PDF_SIGNING_PKCS12_PATH'):
        return []
    if os.path.normpath(os.getenv('PDF_SIGNING_KEY_PATH', CLAVE_POR_DEFECTO)) != os.path.normpath(CLAVE_POR_DEFECTO):
        return []
    return [checks.Warning(
        'La firma de los PDF usa la clave por defecto certs/signing.key con DEBUG=False.',
        hint='Configura el sello de empresa con PDF_SIGNING_PKCS12_PATH (o PDF_SIGNING_KEY_PATH) '
             'fuera del repositorio. La clave que estuvo versionada debe considerarse comprometida.',
        id='inspecciones.W001',
    )]


@checks.register(checks.Tags.security, deploy=False)
def check_certificados_tsa(app_configs, **kwargs):
    """Error si un archivo de la TSA configurado no sirve: solicitar_sello_tiempo rechazaría
    todos los sellos (un Error para el Pre-Deploy, que corre migrate). De los archivos de
    mTLS solo se comprueba que se puedan leer; la pareja certificado-clave no se valida."""
    prefijos = {_prefijo(PERFIL_EVIDENCIAS), _prefijo(PERFIL_MANIFIESTO)}
    avisos = []
    for prefijo in sorted(prefijos):
        for campo in ('CERT', 'CERT_CLIENTE', 'CLAVE_CLIENTE'):
            nombre = f'{prefijo}_{campo}'
            ruta = getattr(settings, nombre, '')
            if not ruta:
                continue
            try:
                problema, caduca = _revisar_archivo_tsa(ruta, es_cert_tsa=campo == 'CERT')
            except Exception as e:
                # Un check que lanza aborta cualquier manage.py: mejor un error legible
                problema, caduca = f'{ruta}: {type(e).__name__}: {e}', None
            if problema:
                avisos.append(checks.Error(
                    f'{nombre} no sirve ({problema}): no se sellará ninguna evidencia ni manifiesto.',
                    hint=f'Revisa {nombre}: en Render, un Secret File se monta en /etc/secrets/<nombre exacto>.',
                    id='inspecciones.E002',
                ))
            elif caduca is not None and caduca - datetime.now(timezone.utc) < AVISO_CADUCIDAD:
                avisos.append(checks.Warning(
                    f'{nombre} caduca el {caduca:%Y-%m-%d}: a partir de entonces se rechazarán todos los sellos.',
                    hint='Sustituye el certificado de la TSA antes de esa fecha.',
                    id='inspecciones.W002',
                ))
    return avisos


def _revisar_archivo_tsa(ruta, es_cert_tsa):
    """Devuelve (problema o None, caducidad del certificado de la TSA o None)."""
    if not os.path.isfile(ruta):
        return f'{ruta} no existe o no es un archivo', None
    if not es_cert_tsa:
        # La clave privada no se lee: basta con que el proceso pueda abrirla
        return (None if os.access(ruta, os.R_OK) else f'{ruta} no se puede leer'), None
    with open(ruta, 'rb') as f:
        contenido = f.read(MAX_CERT_TSA)
    try:
        cert = validar_cert_tsa(contenido)
    except ValueError as e:
        # Lo que no es un certificado X.509 también llega como ValueError
        return f'{ruta}: {e}', None
    if cert.not_valid_after_utc < datetime.now(timezone.utc):
        return f'{ruta}: el certificado caducó el {cert.not_valid_after_utc:%Y-%m-%d}', None
    return None, cert.not_valid_after_utc
