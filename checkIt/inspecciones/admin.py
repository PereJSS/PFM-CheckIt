from django import forms
from django.contrib import admin

from .models import ChecklistInspeccion, Evidencia, Inspeccion
from .services.checklist import faltas_para_completar


class InspeccionAdminForm(forms.ModelForm):
    class Meta:
        model = Inspeccion
        fields = '__all__'

    def clean(self):
        datos = super().clean()
        # La misma regla que la API: con checklist, no se completa con puntos pendientes.
        # Sin el bloqueo de la API: la ventana hasta el guardado es mínima y el admin, de uso raro
        completando = (
            self.instance.pk
            and datos.get('estado') == Inspeccion.Estado.COMPLETADA
            and self.instance.estado != Inspeccion.Estado.COMPLETADA
        )
        if completando:
            faltas = faltas_para_completar(self.instance.pk)
            if faltas:
                detalle = '; '.join(
                    f"{f['estancia']} · {f['texto']} ({'sin marcar' if f['motivo'] == 'sin_marcar' else 'falta foto'})"
                    for f in faltas[:5]
                )
                resto = f' y {len(faltas) - 5} más' if len(faltas) > 5 else ''
                self.add_error('estado', f'Faltan puntos del checklist: {detalle}{resto}.')
        return datos


@admin.register(Inspeccion)
class InspeccionAdmin(admin.ModelAdmin):
    form = InspeccionAdminForm


admin.site.register(Evidencia)
@admin.register(ChecklistInspeccion)
class ChecklistInspeccionAdmin(admin.ModelAdmin):
    """Solo consulta: es la copia fija de la plantilla; se cambia con la API (POST checklist)."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
