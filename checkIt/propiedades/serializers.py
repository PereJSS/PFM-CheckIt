from rest_framework import serializers
from .models import Propiedad


class PropiedadSerializer(serializers.ModelSerializer):
    class Meta:
        model = Propiedad
        fields = ('id', 'nombre', 'direccion', 'descripcion', 'creado_en')
        read_only_fields = ('id', 'creado_en')