from rest_framework import serializers
from .models import Propiedad

# Serializador para la propiedad que se usará en la API
class PropiedadSerializer(serializers.ModelSerializer):
    class Meta:
        model = Propiedad
        fields = '__all__'