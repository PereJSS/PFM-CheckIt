from django.db import models

# Create your models here.
from propiedades.models import Propiedad
from django.conf import settings

class Inspeccion(models.Model):
    class Estado(models.TextChoices):
        PENDIENTE = 'PENDIENTE', 'Pendiente'
        EN_PROGRESO = 'EN_PROGRESO', 'En progreso'
        COMPLETADA = 'COMPLETADA', 'Completada'

    class PdfEstado(models.TextChoices):
        PENDIENTE = 'PENDIENTE', 'Pendiente'
        GENERANDO = 'GENERANDO', 'Generando'
        LISTO = 'LISTO', 'Listo'
        ERROR = 'ERROR', 'Error'

    # toda inspeccion está asociada a una propiedad y a un operario (usuario)
    propiedad = models.ForeignKey(Propiedad, on_delete=models.CASCADE, related_name="inspecciones")
    operario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="inspecciones_asignadas")

    # Datos de la inspección
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    estado = models.CharField(max_length=20, choices=Estado.choices, default=Estado.PENDIENTE)
    pdf_estado = models.CharField(max_length=10, choices=PdfEstado.choices, default=PdfEstado.PENDIENTE)
    pdf_ruta = models.CharField(max_length=500, blank=True, null=True)

    # Manifiesto Merkle: se congela al completar la inspección y se sella solo su raíz
    manifiesto = models.JSONField(blank=True, null=True, editable=False)
    manifiesto_raiz = models.CharField(max_length=64, blank=True, null=True, editable=False, verbose_name="Raíz Merkle")
    manifiesto_tsa_token = models.BinaryField(blank=True, null=True, editable=False, verbose_name="Token TSA del manifiesto")
    manifiesto_tsa_timestamp = models.DateTimeField(blank=True, null=True, editable=False)
    manifiesto_tsa_url = models.CharField(max_length=500, blank=True, default='', editable=False)
    manifiesto_tsa_cualificada = models.BooleanField(default=False, editable=False)
    manifiesto_tsa_verificado = models.BooleanField(default=False, editable=False)
    manifiesto_tsa_ultimo_intento = models.DateTimeField(blank=True, null=True, editable=False)
    # Manifiestos sellados de completados anteriores (si se reabrió la inspección): tienen valor probatorio
    manifiestos_anteriores = models.JSONField(default=list, blank=True, editable=False)

    class Meta:
        indexes = [
            models.Index(
                fields=['manifiesto_tsa_ultimo_intento', 'id'],
                name='inspeccion_manif_pend_idx',
                condition=models.Q(estado='COMPLETADA', manifiesto_tsa_token__isnull=True),
            ),
        ]

    def __str__(self) -> str:
        return f"Inspección {self.pk} - {self.propiedad.nombre} asignada a {self.operario.username if self.operario else 'Sin operario'} - Estado: {self.estado_display()}"

    def estado_display(self) -> str:
        estados = {str(valor): etiqueta for valor, etiqueta in self.Estado.choices}
        return estados.get(self.estado, self.estado)

# Modelo para almacenar evidencias (fotos) de las inspecciones
class Evidencia(models.Model):
    inspeccion = models.ForeignKey(Inspeccion, on_delete=models.CASCADE, related_name="evidencias")
    foto = models.ImageField(upload_to="evidencias/")
    descripcion = models.TextField(blank=True, null=True)
    fecha_captura = models.DateTimeField(auto_now_add=True)
    hash_sha256 = models.CharField(max_length=64, blank=True, null=True, verbose_name="Hash SHA-256")
    tsa_timestamp = models.DateTimeField(blank=True, null=True, verbose_name="Timestamp TSA")
    tsa_token = models.BinaryField(blank=True, null=True, verbose_name="Token TSA (.tsr)")
    # Último intento de re-sellado (tarea periódica): rota la cola de pendientes
    tsa_ultimo_intento = models.DateTimeField(blank=True, null=True, editable=False)

    class Meta:
        indexes = [
            # Solo las pendientes de sello: el índice no crece con las ya selladas
            models.Index(
                fields=['tsa_ultimo_intento', 'id'],
                name='evidencia_tsa_pendiente_idx',
                condition=models.Q(tsa_token__isnull=True),
            ),
        ]

    def __str__(self):
        return f"Evidencia de {self.inspeccion} tomada por {self.inspeccion.operario.username if self.inspeccion.operario else 'Sin operario'} el {self.fecha_captura.strftime('%Y-%m-%d %H:%M:%S')}"
    