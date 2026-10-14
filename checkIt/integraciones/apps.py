from django.apps import AppConfig


class IntegracionesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'integraciones'

    def ready(self):
        from . import signals  # noqa: F401  (conecta los receptores de webhooks salientes)
