from django.db import IntegrityError, transaction
from rest_framework import serializers, viewsets
from rest_framework.permissions import IsAuthenticated
from .models import Propiedad
from .serializers import PropiedadSerializer


class PropiedadViewSet(viewsets.ModelViewSet):
    serializer_class = PropiedadSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):  # type: ignore[override]
        return Propiedad.objects.filter(owner=self.request.user).order_by('nombre')

    def _guardar(self, serializer, **kwargs):
        # La validación de external_id es check-then-act: dos peticiones simultáneas
        # pueden pasarla; el índice único de BD decide y lo traducimos a 400.
        try:
            with transaction.atomic():
                serializer.save(**kwargs)
        except IntegrityError:
            raise serializers.ValidationError(
                {'external_id': ['Ya tienes otra propiedad con este ID externo.']}
            )

    def perform_create(self, serializer):
        self._guardar(serializer, owner=self.request.user)

    def perform_update(self, serializer):
        self._guardar(serializer)