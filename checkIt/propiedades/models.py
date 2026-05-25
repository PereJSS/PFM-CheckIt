from django.db import models
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
    creado_en = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.nombre} - {self.direccion}"