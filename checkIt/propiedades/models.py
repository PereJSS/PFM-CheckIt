from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Lower

from core.textos import motivo_texto_corto_no_valido, normalizar_texto_corto

# Límites de una plantilla de checklist (#19): que el acta y el móvil sigan siendo usables
ESTANCIAS_MAX = 40
PUNTOS_POR_ESTANCIA_MAX = 40
PUNTOS_MAX = 400
NOMBRE_ESTANCIA_MAX = 60  # igual que inspecciones.models.ESTANCIA_MAX
TEXTO_PUNTO_MAX = 200


def _texto(valor, maximo, campo):
    if not isinstance(valor, str):
        raise ValidationError(f"{campo} debe ser texto.")
    texto = normalizar_texto_corto(valor)
    if not texto:
        raise ValidationError(f"{campo} no puede estar vacío.")
    motivo = motivo_texto_corto_no_valido(texto, maximo, campo)
    if motivo:
        raise ValidationError(motivo)
    return texto


def _claves(objeto, permitidas, donde):
    if not isinstance(objeto, dict):
        raise ValidationError(f"{donde} debe ser un objeto.")
    sobrantes = set(objeto) - permitidas
    if sobrantes:
        raise ValidationError(f"{donde} tiene claves desconocidas: {', '.join(sorted(map(str, sobrantes)))}.")


def _lista(valor, minimo, maximo, campo):
    if not isinstance(valor, list) or not minimo <= len(valor) <= maximo:
        raise ValidationError(f"{campo} debe tener entre {minimo} y {maximo} elementos.")
    return valor


def normalizar_estructura(estructura):
    """Valida y normaliza {"estancias": [{"nombre", "puntos": [{"texto", "requiere_foto"}]}]}.

    Devuelve la estructura limpia (NFC, sin espacios, requiere_foto explícito) o lanza
    ValidationError. Los nombres de estancia y los puntos de una estancia no se repiten
    (sin distinguir mayúsculas): en el acta serían indistinguibles."""
    _claves(estructura, {'estancias'}, 'La plantilla')
    estancias = _lista(estructura.get('estancias'), 1, ESTANCIAS_MAX, 'estancias')
    limpias, nombres, total = [], set(), 0
    for estancia in estancias:
        _claves(estancia, {'nombre', 'puntos'}, 'Cada estancia')
        nombre = _texto(estancia.get('nombre'), NOMBRE_ESTANCIA_MAX, 'nombre de la estancia')
        if nombre.casefold() in nombres:
            raise ValidationError(f"La estancia «{nombre}» está repetida.")
        nombres.add(nombre.casefold())
        puntos = _lista(estancia.get('puntos'), 1, PUNTOS_POR_ESTANCIA_MAX, f'puntos de «{nombre}»')
        textos, limpios = set(), []
        for punto in puntos:
            _claves(punto, {'texto', 'requiere_foto'}, 'Cada punto')
            texto = _texto(punto.get('texto'), TEXTO_PUNTO_MAX, 'texto del punto')
            if texto.casefold() in textos:
                raise ValidationError(f"El punto «{texto}» está repetido en «{nombre}».")
            textos.add(texto.casefold())
            requiere_foto = punto.get('requiere_foto', False)
            if not isinstance(requiere_foto, bool):
                raise ValidationError("requiere_foto debe ser verdadero o falso.")
            limpios.append({'texto': texto, 'requiere_foto': requiere_foto})
        total += len(limpios)
        limpias.append({'nombre': nombre, 'puntos': limpios})
    if total > PUNTOS_MAX:
        raise ValidationError(f"Una plantilla no puede tener más de {PUNTOS_MAX} puntos.")
    return {'estancias': limpias}


def validar_estructura(estructura):
    normalizar_estructura(estructura)


class PlantillaChecklist(models.Model):
    """Checklist de un cliente: estancias y puntos a revisar. Cada inspección guarda una
    copia fija al crearse (inspecciones.ChecklistInspeccion); editarla no la cambia."""

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='plantillas_checklist',
    )
    nombre = models.CharField(max_length=100)
    descripcion = models.TextField(blank=True, default='')
    estructura = models.JSONField(validators=[validar_estructura])
    # Sube con cada cambio de estructura: el editor la reenvía y un 409 evita pisar cambios
    version = models.PositiveIntegerField(default=1)
    por_defecto = models.BooleanField(default=False)
    creada = models.DateTimeField(auto_now_add=True)
    actualizada = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['nombre']
        constraints = [
            models.UniqueConstraint('owner', Lower('nombre'), name='plantilla_nombre_ci_unico_por_owner'),
            models.UniqueConstraint(
                fields=['owner'], condition=models.Q(por_defecto=True), name='plantilla_un_defecto_por_owner',
            ),
        ]

    def __str__(self):
        return self.nombre


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
    # Sin plantilla se usa la por defecto del cliente; sin ninguna, inspección sin checklist
    plantilla_checklist = models.ForeignKey(
        PlantillaChecklist, on_delete=models.SET_NULL, null=True, blank=True, related_name='propiedades',
    )

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