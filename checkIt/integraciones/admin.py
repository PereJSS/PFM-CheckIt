from django.contrib import admin

from .models import ApiKey, EntregaWebhook, WebhookDestino, WebhookEvent


@admin.register(ApiKey)
class ApiKeyAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'prefijo', 'owner', 'creada', 'ultimo_uso', 'revocada')
    readonly_fields = ('prefijo', 'hash_sha256', 'creada', 'ultimo_uso')


@admin.register(WebhookEvent)
class WebhookEventAdmin(admin.ModelAdmin):
    list_display = ('event_id', 'tipo', 'proveedor', 'owner', 'estado', 'recibido')
    list_filter = ('estado', 'tipo', 'proveedor')


@admin.register(WebhookDestino)
class WebhookDestinoAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'url', 'owner', 'activo', 'creado')
    list_filter = ('activo',)
    exclude = ('secreto',)


@admin.register(EntregaWebhook)
class EntregaWebhookAdmin(admin.ModelAdmin):
    list_display = ('evento', 'destino', 'estado', 'intentos', 'ultimo_codigo', 'creada')
    list_filter = ('estado', 'evento')
    readonly_fields = ('entrega_id', 'event_id', 'payload', 'creada', 'actualizada', 'entregada')
