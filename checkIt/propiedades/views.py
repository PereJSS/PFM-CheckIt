from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from .models import Propiedad
from .serializers import PropiedadSerializer


class PropiedadViewSet(viewsets.ModelViewSet):
    serializer_class = PropiedadSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):  # type: ignore[override]
        return Propiedad.objects.filter(owner=self.request.user).order_by('nombre')

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)