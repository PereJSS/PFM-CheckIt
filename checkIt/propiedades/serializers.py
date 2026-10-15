from rest_framework import serializers
from .models import Propiedad


class PropiedadSerializer(serializers.ModelSerializer):
    class Meta:
        model = Propiedad
        fields = ('id', 'nombre', 'direccion', 'descripcion', 'external_id', 'creado_en')
        read_only_fields = ('id', 'creado_en')

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