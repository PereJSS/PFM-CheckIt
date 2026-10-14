import hashlib
import secrets

from django.conf import settings
from django.db import models

API_KEY_PREFIX = 'ck'


def hash_api_key(clave: str) -> str:
    return hashlib.sha256(clave.encode('utf-8')).hexdigest()


class ApiKey(models.Model):
    """
    Clave para que un sistema externo (PMS/ERP) llame a CheckIt en nombre de un admin.
    Solo se guarda el hash SHA-256: la clave en claro se muestra una única vez al crearla.
    Alcance actual: únicamente el endpoint de webhooks.
    """
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='api_keys',
        verbose_name="Propietario (admin)",
    )
    nombre = models.CharField(max_length=100, verbose_name="Nombre descriptivo")
    prefijo = models.CharField(max_length=8, unique=True, editable=False)
    hash_sha256 = models.CharField(max_length=64, unique=True, editable=False)
    creada = models.DateTimeField(auto_now_add=True)
    ultimo_uso = models.DateTimeField(blank=True, null=True)
    revocada = models.BooleanField(default=False)

    @classmethod
    def generar(cls, owner, nombre):
        # Formato: ck_<prefijo>_<secreto>. El prefijo permite localizar la clave sin
        # recorrer todos los hashes; el secreto aporta 256 bits de entropía.
        prefijo = secrets.token_hex(4)
        while cls.objects.filter(prefijo=prefijo).exists():
            prefijo = secrets.token_hex(4)
        clave = f"{API_KEY_PREFIX}_{prefijo}_{secrets.token_urlsafe(32)}"
        api_key = cls.objects.create(
            owner=owner,
            nombre=nombre,
            prefijo=prefijo,
            hash_sha256=hash_api_key(clave),
        )
        return api_key, clave

    def __str__(self):
        estado = 'revocada' if self.revocada else 'activa'
        return f"{self.nombre} ({API_KEY_PREFIX}_{self.prefijo}_…) - {estado}"


class WebhookEvent(models.Model):
    """
    Registro de cada evento recibido de un PMS. Sirve de idempotencia
    (owner + proveedor + event_id únicos) y de traza de auditoría.
    """
    class Estado(models.TextChoices):
        RECIBIDO = 'RECIBIDO', 'Recibido'
        PROCESADO = 'PROCESADO', 'Procesado'
        IGNORADO = 'IGNORADO', 'Ignorado'
        ERROR = 'ERROR', 'Error'

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='webhook_events',
    )
    api_key = models.ForeignKey(ApiKey, on_delete=models.SET_NULL, null=True, related_name='eventos')
    proveedor = models.CharField(max_length=50, default='generico')
    event_id = models.CharField(max_length=100)
    tipo = models.CharField(max_length=64)
    payload = models.JSONField(default=dict)
    estado = models.CharField(max_length=10, choices=Estado.choices, default=Estado.RECIBIDO)
    error = models.TextField(blank=True, default='')
    inspeccion = models.ForeignKey(
        'inspecciones.Inspeccion',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='webhook_events',
    )
    recibido = models.DateTimeField(auto_now_add=True)
    procesado = models.DateTimeField(blank=True, null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['owner', 'proveedor', 'event_id'],
                name='webhook_event_unico_por_owner',
            ),
        ]

    def __str__(self):
        return f"{self.proveedor}:{self.event_id} ({self.tipo}) - {self.estado}"
