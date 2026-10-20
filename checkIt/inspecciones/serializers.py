from rest_framework import serializers
from .models import Inspeccion, Evidencia

# Serializador para las inspecciones que se usará en la API, incluyendo las evidencias asociadas

class EvidenciaSerializer(serializers.ModelSerializer):
    class Meta:
        model = Evidencia
        fields = '__all__'

class InspeccionSerializer(serializers.ModelSerializer):
    evidencias = EvidenciaSerializer(many=True, read_only=True)
    propiedad_nombre = serializers.CharField(source='propiedad.nombre', read_only=True)
    operario_nombre = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Inspeccion
        # El manifiesto completo y los tokens van en el PDF y en el paquete probatorio, no en cada listado
        exclude = ('manifiesto', 'manifiesto_tsa_token', 'manifiesto_tsa_ultimo_intento', 'manifiestos_anteriores')

    def get_operario_nombre(self, obj):
        if obj.operario:
            return obj.operario.get_full_name() or obj.operario.username
        return None