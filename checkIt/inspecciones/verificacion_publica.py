"""
Verificador público: GET /api/v1/verificacion/<clave>/<sha256>/, sin cuenta. Hacen falta
la foto y la clave de verificación que llega con el acta; la respuesta no lleva datos del cliente.
"""
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from .services.manifiesto import _HASH_HEX
from .services.verificacion import buscar


class SelloIndividualSerializer(serializers.Serializer):
    token = serializers.CharField(help_text='TimeStampToken RFC 3161 en base64 (DER).')
    timestamp = serializers.DateTimeField(allow_null=True)


class SelloManifiestoSerializer(SelloIndividualSerializer):
    url = serializers.CharField(allow_blank=True)
    cualificada = serializers.BooleanField(
        help_text='Si la TSA estaba configurada como cualificada al sellar. No sustituye a validar el sello.',
    )
    verificado = serializers.BooleanField(help_text='Si CheckIt validó la firma del sello al recibirlo.')


class ManifiestoVerificacionSerializer(serializers.Serializer):
    vigente = serializers.BooleanField(help_text='False si la inspección se reabrió y este manifiesto se archivó.')
    version = serializers.IntegerField(allow_null=True)
    arbol = serializers.CharField(allow_null=True)
    raiz = serializers.CharField()
    indice = serializers.IntegerField()
    total = serializers.IntegerField()
    prueba = serializers.ListField(child=serializers.CharField(), help_text='Ruta de inclusión RFC 6962 en hex.')
    sello = SelloManifiestoSerializer(allow_null=True)


class VerificacionSerializer(serializers.Serializer):
    hash = serializers.CharField()
    manifiestos = ManifiestoVerificacionSerializer(
        many=True, help_text='Del más antiguo al vigente: el primer sello es el que prueba la anterioridad.',
    )
    sello_individual = SelloIndividualSerializer(allow_null=True)
    anomalias = serializers.IntegerField(
        help_text='Datos guardados que no se pudieron leer o no cuadran. Con 0, una lista de manifiestos '
                  'vacía significa que la foto aún no ha entrado en ninguno.',
    )
    truncado = serializers.BooleanField(help_text='True si hay más manifiestos de los devueltos.')


class VerificacionPublicaView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'verificacion'

    @extend_schema(
        summary='Prueba de inclusión y sellos de una foto, con la clave de verificación del acta',
        parameters=[
            OpenApiParameter('clave', str, OpenApiParameter.PATH, description='Clave de verificación del acta, en hex.'),
            OpenApiParameter('sha256', str, OpenApiParameter.PATH, description='SHA-256 de la foto, en hex.'),
        ],
        responses={
            200: VerificacionSerializer,
            400: OpenApiResponse(description='La clave o la foto no tienen 64 caracteres hexadecimales'),
            404: OpenApiResponse(description='Clave desconocida, o foto que no es de esa inspección'),
        },
    )
    def get(self, request, clave, sha256):
        clave, hash_sha256 = clave.lower(), sha256.lower()
        if not (_HASH_HEX.fullmatch(clave) and _HASH_HEX.fullmatch(hash_sha256)):
            return Response(
                {"error": "La clave y la foto deben tener 64 caracteres hexadecimales."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        resultado = buscar(clave, hash_sha256)
        if resultado is None:
            return Response({"error": "Sin prueba de esa foto con esa clave."}, status=status.HTTP_404_NOT_FOUND)
        return Response(resultado)

    def finalize_response(self, request, response, *args, **kwargs):
        # Cambia al reabrir o al sellar: una copia intermedia daría por vigente lo archivado,
        # o por inexistente una prueba que ya existe. También en los errores.
        response = super().finalize_response(request, response, *args, **kwargs)
        response['Cache-Control'] = 'no-store'
        return response
