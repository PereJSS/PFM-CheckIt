import os

from django.conf import settings
from django.core import checks

CLAVE_POR_DEFECTO = 'certs/signing.key'


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
