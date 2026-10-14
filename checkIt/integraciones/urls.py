from django.urls import path

from .views import (
    ApiKeyListCreateView,
    ApiKeyRevokeView,
    EntregaWebhookListView,
    EntregaWebhookReintentarView,
    PmsWebhookView,
    WebhookDestinoDetailView,
    WebhookDestinoListCreateView,
    WebhookDestinoRotarSecretoView,
    WebhookEventListView,
)

urlpatterns = [
    path('webhooks/pms/', PmsWebhookView.as_view(), name='webhook-pms'),
    path('integraciones/api-keys/', ApiKeyListCreateView.as_view(), name='api-keys'),
    path('integraciones/api-keys/<int:pk>/', ApiKeyRevokeView.as_view(), name='api-key-revoke'),
    path('integraciones/eventos/', WebhookEventListView.as_view(), name='webhook-eventos'),
    path('integraciones/destinos/', WebhookDestinoListCreateView.as_view(), name='webhook-destinos'),
    path('integraciones/destinos/<int:pk>/', WebhookDestinoDetailView.as_view(), name='webhook-destino'),
    path(
        'integraciones/destinos/<int:pk>/rotar-secreto/',
        WebhookDestinoRotarSecretoView.as_view(),
        name='webhook-destino-rotar-secreto',
    ),
    path('integraciones/entregas/', EntregaWebhookListView.as_view(), name='webhook-entregas'),
    path(
        'integraciones/entregas/<int:pk>/reintentar/',
        EntregaWebhookReintentarView.as_view(),
        name='webhook-entrega-reintentar',
    ),
]
