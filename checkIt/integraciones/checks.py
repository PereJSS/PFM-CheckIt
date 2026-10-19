from django.conf import settings
from django.core import checks


@checks.register(checks.Tags.security, deploy=False)
def check_permitir_local(app_configs, **kwargs):
    # Leído en tiempo de ejecución para que los tests puedan parchearlo
    from . import salientes

    if salientes.PERMITIR_LOCAL and not settings.DEBUG:
        return [checks.Warning(
            'WEBHOOK_SALIENTE_PERMITIR_LOCAL=True con DEBUG=False: los webhooks salientes '
            'admiten http y destinos locales o privados (sin protección SSRF).',
            hint='Quita WEBHOOK_SALIENTE_PERMITIR_LOCAL del entorno de producción.',
            id='integraciones.W001',
        )]
    return []
