from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework import status
from rest_framework.throttling import ScopedRateThrottle
from rest_framework_simplejwt.views import TokenObtainPairView
from typing import cast
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers
from .throttles import LoginUsuarioThrottle
from .models import User
from .serializers import AdminRegisterSerializer, OperarioCreateSerializer


class ThrottledTokenObtainPairView(TokenObtainPairView):
    """Login JWT con límite de peticiones (scope 'auth') contra fuerza bruta."""
    throttle_classes = [ScopedRateThrottle, LoginUsuarioThrottle]
    throttle_scope = 'auth_login'


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
        operarios = User.objects.filter(role=User.Role.OPERARIO).order_by("first_name", "username")
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
            user = cast(User, serializer.save())
            return Response(
                {"id": user.pk, "username": user.username, "nombre": user.get_full_name() or user.username},
                status=status.HTTP_201_CREATED,
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class OperarioDetailView(APIView):
    """Elimina un operario. Solo accesible para admins."""
    permission_classes = [IsAuthenticated]

    @extend_schema(request=None, responses={204: None})
    def delete(self, request, pk):
        if not request.user.is_admin():
            return Response({"error": "Sin permisos."}, status=403)
        try:
            user = User.objects.get(pk=pk, role=User.Role.OPERARIO)
        except User.DoesNotExist:
            return Response({"error": "Operario no encontrado."}, status=404)
        user.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
