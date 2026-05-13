from django.db import models

# Create your models here.
class Propiedad(models.Model):
    nombre = models.CharField(max_length=200, verbose_name="Nombre de la propiedad")
    direccion = models.TextField(verbose_name="Dirección")
    descripcion = models.TextField(blank=True, null=True, verbose_name="Descripción")
    creado_en = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.nombre} - {self.direccion}"