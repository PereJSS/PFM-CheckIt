from rest_framework import serializers
from .models import Inspeccion, Evidencia

# Serializador para las inspecciones que se usará en la API, incluyendo las evidencias asociadas

class EvidenciaSerializer(serializers.ModelSerializer):
    class Meta:
        model = Evidencia
        fields = '__all__'

class InspeccionSerializer(serializers.ModelSerializer):
    evidencias = EvidenciaSerializer(many=True, read_only=True) 

    class Meta:
        model = Inspeccion
        fields = '__all__'