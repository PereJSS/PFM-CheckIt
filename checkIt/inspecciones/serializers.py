from rest_framework import serializers
from drf_spectacular.utils import extend_schema_field

from .models import Inspeccion, Evidencia
from .services.checklist import resumen

# Serializador para las inspecciones que se usará en la API, incluyendo las evidencias asociadas

class ChecklistResumenSerializer(serializers.Serializer):
    puntos = serializers.IntegerField()
    ok = serializers.IntegerField()
    danos = serializers.IntegerField()
    no_aplica = serializers.IntegerField()
    sin_marcar = serializers.IntegerField()


class EvidenciaSerializer(serializers.ModelSerializer):
    class Meta:
        model = Evidencia
        fields = '__all__'

class InspeccionSerializer(serializers.ModelSerializer):
    evidencias = EvidenciaSerializer(many=True, read_only=True)
    propiedad_nombre = serializers.CharField(source='propiedad.nombre', read_only=True)
    operario_nombre = serializers.SerializerMethodField(read_only=True)
    checklist_resumen = serializers.SerializerMethodField()

    class Meta:
        model = Inspeccion
        # El manifiesto completo y los tokens van en el PDF y en el paquete probatorio, no en cada listado
        exclude = (
            'manifiesto', 'manifiesto_tsa_token', 'manifiesto_tsa_ultimo_intento', 'manifiestos_anteriores',
            'clave_verificacion',
        )
        # Solo los escribe el servidor: claim-report abre lo que diga pdf_ruta
        read_only_fields = ('pdf_estado', 'pdf_ruta')

    @extend_schema_field(ChecklistResumenSerializer(allow_null=True))
    def get_checklist_resumen(self, obj):
        # En el listado viene anotado (sin consultas por fila); en el resto, se calcula
        tiene = getattr(obj, 'ck_tiene', None)
        if tiene is None:
            return resumen(obj.pk)
        if not tiene:
            return None
        puntos, ok, danos, no_aplica = obj.ck_puntos, obj.ck_ok, obj.ck_danos, obj.ck_no_aplica
        return {
            'puntos': puntos, 'ok': ok, 'danos': danos, 'no_aplica': no_aplica,
            'sin_marcar': puntos - ok - danos - no_aplica,
        }

    def get_operario_nombre(self, obj):
        if obj.operario:
            return obj.operario.get_full_name() or obj.operario.username
        return None