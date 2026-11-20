from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework import status
from rest_framework.throttling import ScopedRateThrottle
from rest_framework_simplejwt.serializers import TokenBlacklistSerializer
from rest_framework_simplejwt.views import TokenBlacklistView, TokenObtainPairView, TokenRefreshView
from typing import cast
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers
from .throttles import LoginUsuarioThrottle
from .models import User
from .serializers import AdminRegisterSerializer, LoginSerializer, OperarioCreateSerializer, RefrescoUsuarioActivoSerializer


# Límite de peticiones de los endpoints de sesión: 429 con Retry-After (segundos)
_429 = OpenApiResponse(
    response=inline_serializer('DemasiadasPeticiones', {'detail': serializers.CharField()}),
    description='Demasiadas peticiones. Reintentar pasados los segundos de Retry-After.',
)
_RETRY_AFTER = OpenApiParameter(
    'Retry-After', OpenApiTypes.INT, OpenApiParameter.HEADER, response=[429],
    description='Segundos que faltan para poder reintentar.',
)
_401_TOKEN = OpenApiResponse(description='Refresh token no válido, caducado, ya usado o de un usuario dado de baja')


@extend_schema(
    responses={200: LoginSerializer, 401: OpenApiResponse(description='Credenciales incorrectas'), 429: _429},
    parameters=[_RETRY_AFTER],
)
class ThrottledTokenObtainPairView(TokenObtainPairView):
    """Login JWT con límite de peticiones (scope 'auth') contra fuerza bruta."""
    throttle_classes = [ScopedRateThrottle, LoginUsuarioThrottle]
    throttle_scope = 'auth_login'


@extend_schema(
    responses={200: RefrescoUsuarioActivoSerializer, 401: _401_TOKEN, 429: _429},
    parameters=[_RETRY_AFTER],
)
class ThrottledTokenRefreshView(TokenRefreshView):
    """Devuelve un access token y un refresh token nuevos; el refresh enviado queda
    anulado (rotación): guarda siempre el que llega en la respuesta. Con demasiadas
    peticiones desde la misma IP responde 429 con la cabecera Retry-After."""
    # Sin límite se pueden probar refresh tokens filtrados sin freno. Cuota propia
    # para no bloquear a quien acaba de fallar el login.
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'auth_refresh'


@extend_schema(
    request=TokenBlacklistSerializer,
    responses={
        200: OpenApiResponse(description='Sesión cerrada: el refresh queda anulado'),
        400: OpenApiResponse(description='Falta el refresh token'),
        401: OpenApiResponse(description='Refresh token no válido, caducado o ya anulado'),
        429: _429,
    },
    parameters=[_RETRY_AFTER],
)
class ThrottledLogoutView(TokenBlacklistView):
    """Cierra la sesión en el servidor: anula el refresh token enviado. No exige el access
    token (puede haber caducado); el refresh es la credencial. El access sigue valiendo
    hasta que caduca."""
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'auth_logout'


class RegisterAdminView(APIView):
    """Registro público para crear un usuario administrador."""
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'auth_register'

    @extend_schema(request=AdminRegisterSerializer, responses={201: inline_serializer('RegistroOk', {
        'detail': serializers.CharField(), 'username': serializers.CharField()})})
    def post(self, request):
        serializer = AdminRegisterSerializer(data=request.data)
        if serializer.is_valid():
            user = cast(User, serializer.save())
            return Response(
                {"detail": "Cuenta creada correctamente.", "username": user.username},
                status=status.HTTP_201_CREATED,
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses=inline_serializer('Me', {'id': serializers.IntegerField(), 'username': serializers.CharField(), 'email': serializers.CharField(), 'role': serializers.CharField()}))
    def get(self, request):
        user = request.user
        return Response({
            "id": user.pk,
            "username": user.username,
            "email": user.email,
            "role": user.role,
        })


class OperariosView(APIView):
    """Lista y crea usuarios operario. Solo accesible para admins."""
    permission_classes = [IsAuthenticated]

    @extend_schema(responses=inline_serializer('OperarioItem', {'id': serializers.IntegerField(), 'username': serializers.CharField(), 'email': serializers.CharField(), 'nombre': serializers.CharField()}, many=True))
    def get(self, request):
        if not request.user.is_admin():
            return Response({"error": "Sin permisos."}, status=403)
        # Solo los suyos: los de otros clientes no se ven (ni sus emails)
        operarios = User.objects.filter(
            role=User.Role.OPERARIO, administrador=request.user, is_active=True,
        ).order_by("first_name", "username")
        data = [
            {
                "id": u.pk,
                "username": u.username,
                "email": u.email,
                "nombre": u.get_full_name() or u.username,
            }
            for u in operarios
        ]
        return Response(data)

    @extend_schema(request=OperarioCreateSerializer, responses={201: inline_serializer('OperarioCreado', {'id': serializers.IntegerField(), 'username': serializers.CharField(), 'nombre': serializers.CharField()})})
    def post(self, request):
        if not request.user.is_admin():
            return Response({"error": "Sin permisos."}, status=403)
        serializer = OperarioCreateSerializer(data=request.data)
        if serializer.is_valid():
            user = cast(User, serializer.save(administrador=request.user))
            return Response(
                {"id": user.pk, "username": user.username, "nombre": user.get_full_name() or user.username},
                status=status.HTTP_201_CREATED,
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class OperarioDetailView(APIView):
    """Da de baja a un operario (lo desactiva, no lo borra). Solo accesible para admins."""
    permission_classes = [IsAuthenticated]

    @extend_schema(request=None, responses={204: None})
    def delete(self, request, pk):
        if not request.user.is_admin():
            return Response({"error": "Sin permisos."}, status=403)
        try:
            # Uno ajeno da 404 como uno inexistente: no se revela que existe
            user = User.objects.get(
                pk=pk, role=User.Role.OPERARIO, administrador=request.user, is_active=True,
            )
        except User.DoesNotExist:
            return Response({"error": "Operario no encontrado."}, status=404)
        # Baja, no borrado: sus inspecciones conservan quién las hizo (PDF y paquete probatorio).
        # Inactivo no inicia sesión, sus tokens dejan de valer y no puede refrescarlos
        user.is_active = False
        user.save(update_fields=['is_active'])
        return Response(status=status.HTTP_204_NO_CONTENT)
