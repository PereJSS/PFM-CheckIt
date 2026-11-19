import logging
import re

from django.db import IntegrityError, transaction
from django.db.models import Count, F
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, extend_schema_view, inline_serializer
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from users.permissions import IsAdmin

from .models import PlantillaChecklist, Propiedad
from .serializers import NOMBRE_PLANTILLA_MAX, NOMBRE_REPETIDO, PlantillaChecklistSerializer, PropiedadSerializer


class PropiedadViewSet(viewsets.ModelViewSet):
    serializer_class = PropiedadSerializer
    # Las propiedades son del cliente (el administrador): un operario no lista, crea ni edita.
    permission_classes = [IsAuthenticated, IsAdmin]

    def get_queryset(self):  # type: ignore[override]
        return (
            Propiedad.objects.filter(owner=self.request.user)
            .select_related('plantilla_checklist')
            .order_by('nombre')
        )

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


logger = logging.getLogger(__name__)

_VERSION = re.compile(r'^[0-9]{1,9}$')


class _VersionVieja(Exception):
    def __init__(self, actual):
        self.actual = actual


# get_queryset depende de request.user y no puede resolver el tipo del parámetro
_id = [OpenApiParameter('id', OpenApiTypes.INT, OpenApiParameter.PATH)]
_403 = OpenApiResponse(description='Solo los administradores gestionan plantillas (un operario recibe 403)')
_404 = OpenApiResponse(description='La plantilla no existe o es de otro cliente')
_400 = OpenApiResponse(description='Datos no válidos (estructura, nombre repetido, falta version)')
_409 = OpenApiResponse(
    response=inline_serializer('PlantillaVersionVieja', {
        'error': serializers.CharField(), 'version': serializers.IntegerField(),
    }),
    description='Otra persona la cambió después de leerla: recárgala (version es la actual)',
)


@extend_schema_view(
    list=extend_schema(tags=['checklists'], responses={200: PlantillaChecklistSerializer(many=True), 403: _403}),
    create=extend_schema(tags=['checklists'], responses={201: PlantillaChecklistSerializer, 400: _400, 403: _403}),
    retrieve=extend_schema(tags=['checklists'], parameters=_id, responses={200: PlantillaChecklistSerializer, 403: _403, 404: _404}),
    update=extend_schema(
        tags=['checklists'], parameters=_id, description='Exige `version` (la leída). Cada cambio la sube en 1.',
        responses={200: PlantillaChecklistSerializer, 400: _400, 403: _403, 404: _404, 409: _409}),
    partial_update=extend_schema(
        tags=['checklists'], parameters=_id, description='Exige `version` (la leída). Cada cambio la sube en 1.',
        responses={200: PlantillaChecklistSerializer, 400: _400, 403: _403, 404: _404, 409: _409}),
    destroy=extend_schema(
        tags=['checklists'], parameters=_id,
        description='Las propiedades que la usaban pasan a la plantilla por defecto; las inspecciones ya creadas conservan su copia.',
        responses={204: None, 403: _403, 404: _404}),
    duplicar=extend_schema(
        tags=['checklists'], parameters=_id, request=None,
        responses={201: PlantillaChecklistSerializer, 403: _403, 404: _404}),
)
class PlantillaChecklistViewSet(viewsets.ModelViewSet):
    """Plantillas de checklist del cliente (#19). Cada inspección guarda una copia fija al crearse."""

    serializer_class = PlantillaChecklistSerializer
    permission_classes = [IsAuthenticated, IsAdmin]
    # Solo JSON: en multipart un booleano ausente cuenta como false y un PUT desmarcaría
    # la plantilla por defecto sin pedirlo (y la estructura anidada no cabe en un formulario)
    parser_classes = [JSONParser]

    def get_queryset(self):  # type: ignore[override]
        return (
            PlantillaChecklist.objects.filter(owner=self.request.user)
            .annotate(propiedades_count=Count('propiedades'))
            .order_by('nombre')
        )

    def _desmarcar_defecto(self, excepto=None):
        # Todas las del cliente bloqueadas en orden de pk: dos peticiones que marcan A y B
        # a la vez no se cruzan (interbloqueo). La restricción única parcial no es
        # diferible: primero se desmarca la anterior
        list(
            PlantillaChecklist.objects.select_for_update().filter(owner=self.request.user)
            .order_by('pk').values_list('pk', flat=True)
        )
        PlantillaChecklist.objects.filter(owner=self.request.user, por_defecto=True).exclude(pk=excepto).update(
            por_defecto=False,
        )

    def _en_transaccion(self, hacer):
        # La validación del nombre es check-then-act: la BD decide y se traduce a 400
        try:
            with transaction.atomic():
                return hacer()
        except IntegrityError as e:
            # El nombre de la restricción sale en el mensaje de Postgres y de SQLite
            if 'plantilla_un_defecto_por_owner' in str(e):
                raise serializers.ValidationError(
                    {'por_defecto': ['Otra plantilla se marcó por defecto a la vez: inténtalo de nuevo.']}
                )
            if 'plantilla_nombre_ci_unico_por_owner' in str(e):
                raise serializers.ValidationError({'nombre': [NOMBRE_REPETIDO]})
            logger.exception("Error de integridad inesperado al guardar una plantilla de checklist")
            raise

    def perform_create(self, serializer):
        def crear():
            if serializer.validated_data.get('por_defecto'):
                self._desmarcar_defecto()
            serializer.save(owner=self.request.user)
        self._en_transaccion(crear)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        version = request.data.get('version')
        if isinstance(version, str) and _VERSION.match(version):
            version = int(version)
        if isinstance(version, bool) or not isinstance(version, int):
            raise serializers.ValidationError({'version': ['Indica la versión que estás editando (un entero).']})
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        campos = dict(serializer.validated_data)

        def actualizar():
            if campos.get('por_defecto'):
                self._desmarcar_defecto(excepto=instance.pk)
            # Solo si nadie la cambió desde que se leyó: comprobar y escribir en un único UPDATE
            cambiadas = PlantillaChecklist.objects.filter(pk=instance.pk, version=version).update(
                **campos, version=F('version') + 1, actualizada=timezone.now(),
            )
            if not cambiadas:
                raise _VersionVieja(
                    PlantillaChecklist.objects.filter(pk=instance.pk).values_list('version', flat=True).first()
                )

        try:
            self._en_transaccion(actualizar)
        except _VersionVieja as vieja:
            if vieja.actual is None:
                return Response({'error': 'La plantilla ya no existe.'}, status=status.HTTP_404_NOT_FOUND)
            return Response(
                {'error': 'La plantilla ha cambiado desde que la abriste: recárgala antes de guardar.',
                 'version': vieja.actual},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(self.get_serializer(self.get_queryset().get(pk=instance.pk)).data)

    @action(detail=True, methods=['post'])
    def duplicar(self, request, pk=None):
        original = self.get_object()
        usados = {n.casefold() for n in PlantillaChecklist.objects.filter(owner=request.user).values_list('nombre', flat=True)}
        n = 1
        while True:
            sufijo = ' (copia)' if n == 1 else f' (copia {n})'
            nombre = original.nombre[:NOMBRE_PLANTILLA_MAX - len(sufijo)] + sufijo
            if nombre.casefold() not in usados:
                break
            n += 1
        copia = self._en_transaccion(lambda: PlantillaChecklist.objects.create(
            owner=request.user, nombre=nombre, descripcion=original.descripcion, estructura=original.estructura,
        ))
        return Response(self.get_serializer(copia).data, status=status.HTTP_201_CREATED)
