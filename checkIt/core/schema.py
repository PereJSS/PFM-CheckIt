"""
Anotaciones OpenAPI que no se pueden poner en su sitio sin tocar código ajeno.
Solo metadatos de esquema: no cambian el comportamiento de ninguna vista.
Se importa desde core/urls.py.
"""
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_field, extend_schema_view

from inspecciones.serializers import InspeccionSerializer
from propiedades.views import PropiedadViewSet

extend_schema_field(OpenApiTypes.STR)(InspeccionSerializer.get_operario_nombre)

# get_queryset depende de request.user y no puede resolver el tipo del parámetro
_id = [OpenApiParameter('id', OpenApiTypes.INT, OpenApiParameter.PATH)]
extend_schema_view(
    retrieve=extend_schema(parameters=_id),
    update=extend_schema(parameters=_id),
    partial_update=extend_schema(parameters=_id),
    destroy=extend_schema(parameters=_id),
)(PropiedadViewSet)
