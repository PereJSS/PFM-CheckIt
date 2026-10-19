from django.apps import AppConfig


class IntegracionesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'integraciones'

    def ready(self):
        from . import checks, signals  # noqa: F401  (system checks y receptores de webhooks salientes)
