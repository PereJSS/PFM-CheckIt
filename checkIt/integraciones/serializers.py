from rest_framework import serializers

from .models import ApiKey, WebhookEvent
from .services import EVENTO_CHECKOUT


class ApiKeySerializer(serializers.ModelSerializer):
    class Meta:
        model = ApiKey
        fields = ('id', 'nombre', 'prefijo', 'creada', 'ultimo_uso', 'revocada')
        read_only_fields = ('id', 'prefijo', 'creada', 'ultimo_uso', 'revocada')


class WebhookEventListSerializer(serializers.ModelSerializer):
    class Meta:
        model = WebhookEvent
        fields = ('id', 'proveedor', 'event_id', 'tipo', 'estado', 'error', 'inspeccion', 'recibido', 'procesado')


class PmsWebhookSerializer(serializers.Serializer):
    """
    Formato genérico de evento PMS:
    {"event_id": "evt_123", "event_type": "reservation.checkout",
     "provider": "guesty", "data": {"property_id": "LISTING-42", ...}}
    """
    event_id = serializers.CharField(max_length=100)
    event_type = serializers.CharField(max_length=64)
    provider = serializers.CharField(max_length=50, required=False, default='generico')
    data = serializers.DictField(required=False, default=dict)

    def validate(self, attrs):
        if attrs['event_type'] == EVENTO_CHECKOUT:
            property_id = attrs['data'].get('property_id')
            # bool es subclase de int: se excluye explícitamente
            if isinstance(property_id, bool) or not isinstance(property_id, (str, int)):
                property_id = None
            property_id = str(property_id).strip() if property_id is not None else ''
            if not property_id:
                raise serializers.ValidationError(
                    {'data': {'property_id': 'Obligatorio (texto o número) para reservation.checkout.'}}
                )
            # Misma normalización que PropiedadSerializer.validate_external_id
            attrs['data']['property_id'] = property_id
        return attrs
