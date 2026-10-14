from django.urls import path

from .views import ApiKeyListCreateView, ApiKeyRevokeView, PmsWebhookView, WebhookEventListView

urlpatterns = [
    path('webhooks/pms/', PmsWebhookView.as_view(), name='webhook-pms'),
    path('integraciones/api-keys/', ApiKeyListCreateView.as_view(), name='api-keys'),
    path('integraciones/api-keys/<int:pk>/', ApiKeyRevokeView.as_view(), name='api-key-revoke'),
    path('integraciones/eventos/', WebhookEventListView.as_view(), name='webhook-eventos'),
]
