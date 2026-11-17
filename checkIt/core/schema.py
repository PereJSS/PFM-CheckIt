"""
Anotaciones OpenAPI que no se pueden poner en su sitio sin tocar código ajeno.
Solo metadatos de esquema: no cambian el comportamiento de ninguna vista.
Se importa desde core/urls.py.
"""
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, extend_schema_field, extend_schema_view

from inspecciones.serializers import InspeccionSerializer
from propiedades.serializers import PropiedadSerializer
from propiedades.views import PropiedadViewSet

extend_schema_field(OpenApiTypes.STR)(InspeccionSerializer.get_operario_nombre)

# get_queryset depende de request.user y no puede resolver el tipo del parámetro
_id = [OpenApiParameter('id', OpenApiTypes.INT, OpenApiParameter.PATH)]
# Al declarar responses se sustituyen las automáticas: van también las de éxito
_403 = OpenApiResponse(description='Solo los administradores gestionan propiedades (un operario recibe 403)')
_404 = OpenApiResponse(description='La propiedad no existe o es de otro cliente')
_400 = OpenApiResponse(description='Datos no válidos (p. ej. external_id repetido)')
extend_schema_view(
    list=extend_schema(responses={200: PropiedadSerializer(many=True), 403: _403}),
    create=extend_schema(responses={201: PropiedadSerializer, 400: _400, 403: _403}),
    retrieve=extend_schema(parameters=_id, responses={200: PropiedadSerializer, 403: _403, 404: _404}),
    update=extend_schema(parameters=_id, responses={200: PropiedadSerializer, 400: _400, 403: _403, 404: _404}),
    partial_update=extend_schema(
        parameters=_id, responses={200: PropiedadSerializer, 400: _400, 403: _403, 404: _404}),
    destroy=extend_schema(parameters=_id, responses={204: None, 403: _403, 404: _404}),
)(PropiedadViewSet)
