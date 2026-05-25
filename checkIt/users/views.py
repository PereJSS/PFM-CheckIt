from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework import status
from .models import User
from .serializers import AdminRegisterSerializer, OperarioCreateSerializer


class RegisterAdminView(APIView):
    """Registro público para crear un usuario administrador."""
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = AdminRegisterSerializer(data=request.data)
        if serializer.is_valid():
            user = serializer.save()
            return Response(
                {"detail": "Cuenta creada correctamente.", "username": user.username},
                status=status.HTTP_201_CREATED,
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        return Response({
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "role": user.role,
        })


class OperariosView(APIView):
    """Lista y crea usuarios operario. Solo accesible para admins."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not request.user.is_admin():
            return Response({"error": "Sin permisos."}, status=403)
        operarios = User.objects.filter(role=User.Role.OPERARIO).order_by("first_name", "username")
        data = [
            {
                "id": u.id,
                "username": u.username,
                "email": u.email,
                "nombre": u.get_full_name() or u.username,
            }
            for u in operarios
        ]
        return Response(data)

    def post(self, request):
        if not request.user.is_admin():
            return Response({"error": "Sin permisos."}, status=403)
        serializer = OperarioCreateSerializer(data=request.data)
        if serializer.is_valid():
            user = serializer.save()
            return Response(
                {"id": user.id, "username": user.username, "nombre": user.get_full_name() or user.username},
                status=status.HTTP_201_CREATED,
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class OperarioDetailView(APIView):
    """Elimina un operario. Solo accesible para admins."""
    permission_classes = [IsAuthenticated]

    def delete(self, request, pk):
        if not request.user.is_admin():
            return Response({"error": "Sin permisos."}, status=403)
        try:
            user = User.objects.get(pk=pk, role=User.Role.OPERARIO)
        except User.DoesNotExist:
            return Response({"error": "Operario no encontrado."}, status=404)
        user.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
