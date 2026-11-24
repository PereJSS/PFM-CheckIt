from django.contrib import admin

from .models import Invitacion


@admin.register(Invitacion)
class InvitacionAdmin(admin.ModelAdmin):
    """Solo lectura: se crean con `manage.py crear_invitacion`. Borrar una la revoca.
    La huella del token no se muestra."""
    list_display = ('id', 'email', 'creada_en', 'caduca_en', 'usada_en', 'usada_por')
    list_filter = ('usada_en',)
    search_fields = ('email',)
    fields = ('email', 'creada_en', 'caduca_en', 'usada_en', 'usada_por')
    readonly_fields = fields

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
