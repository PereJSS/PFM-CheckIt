from django.db import models
from django.db.models.functions import Lower
from django.conf import settings


class Propiedad(models.Model):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='propiedades',
        verbose_name="Propietario (admin)",
    )
    nombre = models.CharField(max_length=200, verbose_name="Nombre de la propiedad")
    direccion = models.TextField(verbose_name="Dirección")
    descripcion = models.TextField(blank=True, null=True, verbose_name="Descripción")
    # Identificador de la propiedad en el PMS del cliente (Guesty, Hostaway...),
    # usado para enlazar los webhooks entrantes con la propiedad de CheckIt
    external_id = models.CharField(
        max_length=100, blank=True, null=True, verbose_name="ID externo (PMS)"
    )
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            # Sin distinguir mayúsculas: el webhook busca con iexact y no puede haber ambigüedad
            models.UniqueConstraint(
                'owner',
                Lower('external_id'),
                condition=models.Q(external_id__isnull=False),
                name='propiedad_external_id_ci_unico_por_owner',
            ),
        ]

    def __str__(self):
        return f"{self.nombre} - {self.direccion}"