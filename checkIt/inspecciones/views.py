from django.shortcuts import render

# Create your views here.
from rest_framework import viewsets
from .models import Inspeccion
from .serializers import InspeccionSerializer

class InspeccionViewSet(viewsets.ModelViewSet):
    queryset = Inspeccion.objects.all()
    serializer_inspeccion = InspeccionSerializer

    #Un operario solo ve sus propias inspecciones
    def get_queryset(self):
        usuario = self.request.user
        if usuario.es_administrador():
            return Inspeccion.objects.all()
        return Inspeccion.objects.filter(operario=usuario)