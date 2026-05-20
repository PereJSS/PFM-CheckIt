from django.db import models

# Create your models here.
from propiedades.models import Propiedad
from django.conf import settings

class Inspeccion(models.Model):
    class Estado(models.TextChoices):
        PENDIENTE = 'PENDIENTE', 'Pendiente'
        EN_PROGRESO = 'EN_PROGRESO', 'En progreso'
        COMPLETADA = 'COMPLETADA', 'Completada'

    # toda inspeccion está asociada a una propiedad y a un operario (usuario)
    propiedad = models.ForeignKey(Propiedad, on_delete=models.CASCADE, related_name="inspecciones")
    operario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="inspecciones_asignadas")
    
    # Datos de la inspección
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    estado = models.CharField(max_length=20, choices=Estado.choices, default=Estado.PENDIENTE)

    def __str__(self):
        return f"Inspección {self.id} - {self.propiedad.nombre} asignada a {self.operario.username if self.operario else 'Sin operario'} - Estado: {self.get_estado_display()}"

# Modelo para almacenar evidencias (fotos) de las inspecciones
class Evidencia(models.Model):
    inspeccion = models.ForeignKey(Inspeccion, on_delete=models.CASCADE, related_name="evidencias")
    foto = models.ImageField(upload_to="evidencias/")
    descripcion = models.TextField(blank=True, null=True)
    fecha_captura = models.DateTimeField(auto_now_add=True)
    hash_sha256 = models.CharField(max_length=64, blank=True, null=True, verbose_name="Hash SHA-256")
    tsa_timestamp = models.DateTimeField(blank=True, null=True, verbose_name="Timestamp TSA")
    tsa_token = models.BinaryField(blank=True, null=True, verbose_name="Token TSA (.tsr)")

    def __str__(self):
        return f"Evidencia de {self.inspeccion} tomada por {self.inspeccion.operario.username if self.inspeccion.operario else 'Sin operario'} el {self.fecha_captura.strftime('%Y-%m-%d %H:%M:%S')}"
    