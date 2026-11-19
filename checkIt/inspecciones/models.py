import os
import secrets
import unicodedata
import uuid

from django.core.exceptions import ValidationError
from django.db import models

# Create your models here.
from propiedades.models import Propiedad
from django.conf import settings

def generar_clave_verificacion():
    return secrets.token_hex(32)


class Inspeccion(models.Model):
    # Los escribe solo services/manifiesto.py, con UPDATE: save() completo no los toca
    CAMPOS_SOLO_UPDATE = frozenset({
        'manifiesto', 'manifiesto_raiz', 'manifiesto_tsa_token', 'manifiesto_tsa_timestamp',
        'manifiesto_tsa_url', 'manifiesto_tsa_cualificada', 'manifiesto_tsa_verificado',
        'manifiesto_tsa_ultimo_intento', 'manifiestos_anteriores',
    })

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
    # Raíz del manifiesto que lleva impresa el PDF: el paquete probatorio solo lo usa si es la vigente
    pdf_manifiesto_raiz = models.CharField(max_length=64, blank=True, null=True, editable=False)
    # Inicio del último intento de generar el PDF: un GENERANDO antiguo es de un worker muerto
    pdf_generando_desde = models.DateTimeField(blank=True, null=True, editable=False)

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
    # Llave del verificador público. Es aleatoria y no sale de las fotos, a diferencia de la raíz,
    # que se calcula con ellas: sin ella, tener una foto no enseña nada. Llega con el acta.
    clave_verificacion = models.CharField(max_length=64, unique=True, default=generar_clave_verificacion, editable=False)

    class Meta:
        indexes = [
            models.Index(
                fields=['manifiesto_tsa_ultimo_intento', 'id'],
                name='inspeccion_manif_pend_idx',
                condition=models.Q(estado='COMPLETADA', manifiesto_tsa_token__isnull=True),
            ),
        ]

    def save(self, force_insert=False, force_update=False, using=None, update_fields=None):
        # Un save() completo con una instancia leída antes del sello borraría el token.
        # Solo protege el manifiesto: pdf_* aún se escribe aquí con el valor de la instancia.
        if not self._state.adding and update_fields is None and not force_insert:
            update_fields = [
                f.name for f in self._meta.concrete_fields
                if not f.primary_key and f.name not in self.CAMPOS_SOLO_UPDATE
            ]
        super().save(force_insert=force_insert, force_update=force_update, using=using, update_fields=update_fields)

    def __str__(self) -> str:
        return f"Inspección {self.pk} - {self.propiedad.nombre} asignada a {self.operario.username if self.operario else 'Sin operario'} - Estado: {self.estado_display()}"

    def estado_display(self) -> str:
        estados = {str(valor): etiqueta for valor, etiqueta in self.Estado.choices}
        return estados.get(self.estado, self.estado)


# Extensión por tipo de imagen admitido (la vista solo deja pasar estos content_type)
EXTENSION_POR_TIPO = {
    'image/jpeg': '.jpg',
    'image/png': '.png',
    'image/webp': '.webp',
    'image/heic': '.heic',
    'image/heif': '.heif',
}


def ruta_evidencia(instance, filename):
    """Nombre de la foto en el storage, elegido por el servidor: el bucket está bloqueado
    (WORM) y escribir sobre un nombre existente falla. Un UUID no depende de exists()
    (HEAD y PUT no son atómicos) ni del nombre que manda el cliente.

    La extensión (la usa el paquete probatorio) sale del content_type ya validado, nunca
    del nombre del cliente: nadie la valida, y una larga o como .html no debe llegar al bucket.
    Sin content_type (p. ej. seed_demo) se acepta solo una extensión de imagen conocida."""
    if instance.inspeccion_id is None:
        raise ValueError('La evidencia necesita su inspección antes de guardar la foto')
    try:
        tipo = str(getattr(instance.foto.file, 'content_type', '') or '').lower()
    except ValueError:  # FieldFile sin fichero asociado todavía
        tipo = ''
    extension = EXTENSION_POR_TIPO.get(tipo)
    if extension is None:
        propia = os.path.splitext(filename)[1].lower()
        conocidas = {'.jpeg': '.jpg', **{e: e for e in EXTENSION_POR_TIPO.values()}}
        extension = conocidas.get(propia, '')
    return f'evidencias/{int(instance.inspeccion_id)}/{uuid.uuid4().hex}{extension}'


# Largo máximo de la estancia que declara el operario (p. ej. «Dormitorio 2»)
ESTANCIA_MAX = 60
# Largo máximo de la descripción de cada foto (unas 150–200 palabras)
DESCRIPCION_MAX = 1000

# Saltos que se admiten en la descripción (\r solo llega del textarea del admin;
# la API lo convierte en \n)
_SALTOS_DESCRIPCION = frozenset('\n\r\t')
# Formato que sí escribe un teclado: ZWNJ y ZWJ (emojis compuestos). El resto de Cf
# (dirección del texto, ancho cero, etiquetas Unicode…) invierte o esconde texto
_FORMATO_PERMITIDO = frozenset('\u200c\u200d')
# Cs: un sustituto suelto no se puede guardar en Postgres
_CATEGORIAS_PROHIBIDAS = frozenset({'Cc', 'Cf', 'Cs', 'Zl', 'Zp'})


def caracter_prohibido_en_descripcion(c):
    return (
        unicodedata.category(c) in _CATEGORIAS_PROHIBIDAS
        and c not in _SALTOS_DESCRIPCION
        and c not in _FORMATO_PERMITIDO
    )


def motivo_descripcion_no_valida(texto):
    """Por qué no vale una descripción, o None. La usan la API y full_clean (admin)."""
    if any(caracter_prohibido_en_descripcion(c) for c in texto):
        return "descripcion no puede contener caracteres de control, invisibles ni de dirección del texto."
    if len(texto) > DESCRIPCION_MAX:
        return f"descripcion no puede tener más de {DESCRIPCION_MAX} caracteres."
    return None


def validar_descripcion(valor):
    motivo = motivo_descripcion_no_valida(valor or '')
    if motivo:
        raise ValidationError(motivo)


# Modelo para almacenar evidencias (fotos) de las inspecciones
class Evidencia(models.Model):
    inspeccion = models.ForeignKey(Inspeccion, on_delete=models.CASCADE, related_name="evidencias")
    foto = models.ImageField(upload_to=ruta_evidencia)
    descripcion = models.TextField(blank=True, null=True, validators=[validar_descripcion])
    # La declara el operario y queda fuera del manifiesto sellado, como la descripción.
    # Nullable: las fotos anteriores no la tienen, y las instancias viejas del deploy
    # insertan sin conocer la columna
    estancia = models.CharField(max_length=ESTANCIA_MAX, blank=True, null=True)
    # Pese al nombre, es la hora en que el servidor recibió la foto
    fecha_captura = models.DateTimeField(auto_now_add=True)
    # Hora de la foto según el dispositivo: la declara el cliente, nadie la certifica
    capturada_en = models.DateTimeField(blank=True, null=True, verbose_name="Capturada (según el dispositivo)")
    hash_sha256 = models.CharField(max_length=64, blank=True, null=True, verbose_name="Hash SHA-256")
    tsa_timestamp = models.DateTimeField(blank=True, null=True, verbose_name="Timestamp TSA")
    tsa_token = models.BinaryField(blank=True, null=True, verbose_name="Token TSA (.tsr)")
    # Último intento de re-sellado (tarea periódica): rota la cola de pendientes
    tsa_ultimo_intento = models.DateTimeField(blank=True, null=True, editable=False)

    def avisos_hora(self):
        """Incoherencias de la hora que declara el dispositivo con las que sí controla CheckIt."""
        if not self.capturada_en:
            return []
        avisos = []
        if self.capturada_en < self.inspeccion.fecha_creacion:
            avisos.append('anterior al inicio de la inspección')
        if self.fecha_captura and self.capturada_en > self.fecha_captura:
            avisos.append('posterior a la recepción')
        return avisos

    class Meta:
        indexes = [
            # El verificador público busca por hash
            models.Index(fields=['hash_sha256'], name='evidencia_hash_idx'),
            # Solo las pendientes de sello: el índice no crece con las ya selladas
            models.Index(
                fields=['tsa_ultimo_intento', 'id'],
                name='evidencia_tsa_pendiente_idx',
                condition=models.Q(tsa_token__isnull=True),
            ),
        ]

    def __str__(self):
        return f"Evidencia de {self.inspeccion} tomada por {self.inspeccion.operario.username if self.inspeccion.operario else 'Sin operario'} el {self.fecha_captura.strftime('%Y-%m-%d %H:%M:%S')}"
    