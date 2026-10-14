"""
Documentación OpenAPI (drf-spectacular) de integraciones.

Vive aparte para no tocar las vistas: registra el esquema de seguridad Api-Key y
anota las vistas con extend_schema_view sin cambiar su comportamiento. Se importa
desde core/urls.py.
"""
from drf_spectacular.extensions import OpenApiAuthenticationExtension
from drf_spectacular.utils import (
    OpenApiResponse, extend_schema, extend_schema_view, inline_serializer,
)
from rest_framework import serializers

from .serializers import ApiKeySerializer, PmsWebhookSerializer, WebhookEventListSerializer
from .views import (
    ApiKeyListCreateView, ApiKeyRevokeView, PmsWebhookView, WebhookEventListView,
)


class ApiKeyAuthenticationScheme(OpenApiAuthenticationExtension):
    target_class = 'integraciones.authentication.ApiKeyAuthentication'
    name = 'ApiKeyAuth'

    def get_security_definition(self, auto_schema):
        return {
            'type': 'apiKey',
            'in': 'header',
            'name': 'Authorization',
            'description': (
                'Escribe `Api-Key ck_<prefijo>_<secreto>` (con el prefijo "Api-Key " '
                'incluido). La clave se genera en /api/v1/integraciones/api-keys/.'
            ),
        }


_WebhookAck = inline_serializer('WebhookAck', {
    'id': serializers.IntegerField(),
    'event_id': serializers.CharField(),
    'estado': serializers.CharField(),
    'duplicado': serializers.BooleanField(),
})
_ApiKeyCreada = inline_serializer('ApiKeyCreada', {
    'id': serializers.IntegerField(),
    'nombre': serializers.CharField(),
    'prefijo': serializers.CharField(),
    'creada': serializers.DateTimeField(),
    'ultimo_uso': serializers.DateTimeField(allow_null=True),
    'revocada': serializers.BooleanField(),
    'clave': serializers.CharField(help_text='Solo se muestra en esta respuesta.'),
})

extend_schema_view(post=extend_schema(
    summary='Recibir evento de un PMS',
    request=PmsWebhookSerializer,
    responses={202: _WebhookAck, 200: _WebhookAck},
))(PmsWebhookView)

extend_schema_view(
    get=extend_schema(summary='Listar API Keys', responses=ApiKeySerializer(many=True)),
    post=extend_schema(summary='Crear API Key', request=ApiKeySerializer,
                       responses={201: _ApiKeyCreada}),
)(ApiKeyListCreateView)

extend_schema_view(delete=extend_schema(
    summary='Revocar API Key', request=None,
    responses={204: OpenApiResponse(description='Revocada')},
))(ApiKeyRevokeView)

extend_schema_view(get=extend_schema(
    summary='Últimos eventos recibidos', responses=WebhookEventListSerializer(many=True),
))(WebhookEventListView)
