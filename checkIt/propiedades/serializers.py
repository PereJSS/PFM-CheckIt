from django.core.exceptions import ValidationError as DjangoValidationError
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from core.textos import motivo_texto_corto_no_valido, normalizar_texto_corto
from inspecciones.models import motivo_descripcion_no_valida

from .models import PlantillaChecklist, Propiedad, normalizar_estructura

NOMBRE_PLANTILLA_MAX = 100
NOMBRE_REPETIDO = 'Ya tienes una plantilla con ese nombre.'


class PlantillaEfectivaSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    nombre = serializers.CharField()
    origen = serializers.ChoiceField(choices=['propiedad', 'defecto'])


class PropiedadSerializer(serializers.ModelSerializer):
    # Solo plantillas del propio cliente: una ajena da «no existe», sin revelar que existe
    plantilla_checklist = serializers.PrimaryKeyRelatedField(
        queryset=PlantillaChecklist.objects.none(), allow_null=True, required=False,
    )
    plantilla_efectiva = serializers.SerializerMethodField()

    class Meta:
        model = Propiedad
        fields = (
            'id', 'nombre', 'direccion', 'descripcion', 'external_id', 'creado_en',
            'plantilla_checklist', 'plantilla_efectiva',
        )
        read_only_fields = ('id', 'creado_en')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get('request')
        usuario = getattr(request, 'user', None)
        if usuario is not None and usuario.is_authenticated:
            self.fields['plantilla_checklist'].queryset = PlantillaChecklist.objects.filter(owner=usuario)

    def _defecto(self, owner_id):
        # Una consulta por cliente aunque el listado tenga muchas propiedades
        cache = self.context.setdefault('_plantilla_defecto', {})
        if owner_id not in cache:
            cache[owner_id] = PlantillaChecklist.objects.filter(owner_id=owner_id, por_defecto=True).first()
        return cache[owner_id]

    @extend_schema_field(PlantillaEfectivaSerializer(allow_null=True))
    def get_plantilla_efectiva(self, obj):
        """La que usarán las inspecciones nuevas (misma regla que inspecciones.services.checklist)."""
        plantilla = obj.plantilla_checklist
        origen = 'propiedad'
        if plantilla is None or plantilla.owner_id != obj.owner_id:
            plantilla = self._defecto(obj.owner_id) if obj.owner_id else None
            origen = 'defecto'
        if plantilla is None:
            return None
        return {'id': plantilla.pk, 'nombre': plantilla.nombre, 'origen': origen}

    def validate_external_id(self, value):
        value = (value or '').strip() or None
        if value is None:
            return None
        request = self.context.get('request')
        owner = getattr(request, 'user', None)
        duplicadas = Propiedad.objects.filter(owner=owner, external_id__iexact=value)
        if self.instance is not None:
            duplicadas = duplicadas.exclude(pk=self.instance.pk)
        if duplicadas.exists():
            raise serializers.ValidationError('Ya tienes otra propiedad con este ID externo.')
        return value


# Solo para documentar en OpenAPI la forma de `estructura`; la validación real es
# normalizar_estructura (límites, repetidos, caracteres)
class PuntoPlantillaSerializer(serializers.Serializer):
    texto = serializers.CharField(max_length=200)
    requiere_foto = serializers.BooleanField(default=False)


class EstanciaPlantillaSerializer(serializers.Serializer):
    nombre = serializers.CharField(max_length=60)
    puntos = PuntoPlantillaSerializer(many=True)


class EstructuraPlantillaSerializer(serializers.Serializer):
    estancias = EstanciaPlantillaSerializer(many=True)


@extend_schema_field(EstructuraPlantillaSerializer)
class EstructuraField(serializers.JSONField):
    pass


class PlantillaChecklistSerializer(serializers.ModelSerializer):
    estructura = EstructuraField()
    propiedades_count = serializers.SerializerMethodField()

    class Meta:
        model = PlantillaChecklist
        fields = (
            'id', 'nombre', 'descripcion', 'estructura', 'version', 'por_defecto',
            'creada', 'actualizada', 'propiedades_count',
        )
        # version la gestiona la vista: se envía en PUT/PATCH para detectar ediciones cruzadas
        read_only_fields = ('id', 'version', 'creada', 'actualizada')
        # La unicidad la decide la vista (con la BD como árbitro), no un validador genérico
        validators = []

    @extend_schema_field(serializers.IntegerField())
    def get_propiedades_count(self, obj):
        anotado = getattr(obj, 'propiedades_count', None)
        return obj.propiedades.count() if anotado is None else anotado

    def validate_nombre(self, value):
        nombre = normalizar_texto_corto(value)
        if not nombre:
            raise serializers.ValidationError('El nombre no puede estar vacío.')
        motivo = motivo_texto_corto_no_valido(nombre, NOMBRE_PLANTILLA_MAX, 'nombre')
        if motivo:
            raise serializers.ValidationError(motivo)
        request = self.context.get('request')
        repetidas = PlantillaChecklist.objects.filter(owner=getattr(request, 'user', None), nombre__iexact=nombre)
        if self.instance is not None:
            repetidas = repetidas.exclude(pk=self.instance.pk)
        if repetidas.exists():
            raise serializers.ValidationError(NOMBRE_REPETIDO)
        return nombre

    def validate_descripcion(self, value):
        texto = (value or '').strip()
        motivo = motivo_descripcion_no_valida(texto)
        if motivo:
            raise serializers.ValidationError(motivo.replace('descripcion', 'La descripción', 1))
        return texto

    def validate_estructura(self, value):
        try:
            return normalizar_estructura(value)
        except DjangoValidationError as e:
            raise serializers.ValidationError(e.messages)
