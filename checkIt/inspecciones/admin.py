from django.contrib import admin

# Register your models here.


from .models import Inspeccion, Evidencia

admin.site.register(Inspeccion)
admin.site.register(Evidencia)