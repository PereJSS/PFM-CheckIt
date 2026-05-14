from django.shortcuts import render

# Create your views here.
from rest_framework import viewsets
from .models import Propiedad
from .serializers import PropiedadSerializer

class PropiedadViewSet(viewsets.ModelViewSet):
    queryset = Propiedad.objects.all()
    serializer_propiedad = PropiedadSerializer