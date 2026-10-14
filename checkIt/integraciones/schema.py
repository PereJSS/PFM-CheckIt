"""
Documentación OpenAPI (drf-spectacular) de integraciones.

Vive aparte para no tocar las vistas: registra el esquema de seguridad Api-Key y
anota las vistas con extend_schema_view sin cambiar su comportamiento. Se importa
desde core/urls.py.
"""
from drf_spectacular.extensions import OpenApiAuthenticationExtension
from drf_spectacular.utils import (
    OpenApiParameter, OpenApiResponse, extend_schema, extend_schema_view, inline_serializer,
)
from rest_framework import serializers

from .serializers import (
    ApiKeySerializer, EntregaWebhookSerializer, PmsWebhookSerializer, WebhookDestinoSerializer,
    WebhookEventListSerializer,
)
from .views import (
    ApiKeyListCreateView, ApiKeyRevokeView, EntregaWebhookListView, EntregaWebhookReintentarView,
    PmsWebhookView, WebhookDestinoDetailView, WebhookDestinoListCreateView,
    WebhookDestinoRotarSecretoView, WebhookEventListView,
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


# --- Webhooks salientes (destinos y entregas) ---

_DestinoConSecreto = inline_serializer('WebhookDestinoConSecreto', {
    **WebhookDestinoSerializer().get_fields(),
    'secreto': serializers.CharField(help_text='Solo se muestra en esta respuesta.'),
})
_ErrorDetalle = inline_serializer('ErrorDetalle', {'detail': serializers.CharField()})
_ErrorConflicto = inline_serializer('ErrorConflicto', {'error': serializers.CharField()})

extend_schema_view(
    get=extend_schema(summary='Listar destinos de webhooks salientes',
                      responses=WebhookDestinoSerializer(many=True)),
    post=extend_schema(summary='Crear destino de webhooks salientes',
                       request=WebhookDestinoSerializer,
                       responses={201: _DestinoConSecreto, 400: _ErrorDetalle}),
)(WebhookDestinoListCreateView)

extend_schema_view(
    get=extend_schema(summary='Detalle de destino', responses=WebhookDestinoSerializer),
    patch=extend_schema(summary='Editar destino', request=WebhookDestinoSerializer,
                        responses={200: WebhookDestinoSerializer, 400: _ErrorDetalle}),
    put=extend_schema(summary='Editar destino (equivale a PATCH)',
                      request=WebhookDestinoSerializer,
                      responses={200: WebhookDestinoSerializer, 400: _ErrorDetalle}),
    delete=extend_schema(summary='Eliminar destino', request=None,
                         responses={204: OpenApiResponse(description='Eliminado')}),
)(WebhookDestinoDetailView)

extend_schema_view(post=extend_schema(
    summary='Rotar el secreto de firma del destino', request=None,
    responses={200: _DestinoConSecreto},
))(WebhookDestinoRotarSecretoView)

extend_schema_view(get=extend_schema(
    summary='Últimas entregas de webhooks salientes',
    parameters=[
        OpenApiParameter('destino', int, description='Filtra por id de destino.'),
        OpenApiParameter('estado', str, enum=['PENDIENTE', 'ENTREGADA', 'FALLIDA']),
    ],
    responses=EntregaWebhookSerializer(many=True),
))(EntregaWebhookListView)

extend_schema_view(post=extend_schema(
    summary='Reintentar entrega fallida o atascada', request=None,
    responses={202: OpenApiResponse(description='Reencolada'), 409: _ErrorConflicto},
))(EntregaWebhookReintentarView)
