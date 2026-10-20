from django.apps import AppConfig


class InspeccionesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'inspecciones'

    def ready(self):
        from . import checks, signals  # noqa: F401  (system checks y ciclo de vida del manifiesto)
