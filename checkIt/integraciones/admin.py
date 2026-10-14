from django.contrib import admin

from .models import ApiKey, WebhookEvent


@admin.register(ApiKey)
class ApiKeyAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'prefijo', 'owner', 'creada', 'ultimo_uso', 'revocada')
    readonly_fields = ('prefijo', 'hash_sha256', 'creada', 'ultimo_uso')


@admin.register(WebhookEvent)
class WebhookEventAdmin(admin.ModelAdmin):
    list_display = ('event_id', 'tipo', 'proveedor', 'owner', 'estado', 'recibido')
    list_filter = ('estado', 'tipo', 'proveedor')
