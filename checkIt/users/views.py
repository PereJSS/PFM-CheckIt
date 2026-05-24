from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from .models import User


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
    """Devuelve la lista de usuarios con rol OPERARIO. Solo accesible para admins."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not request.user.is_admin():
            return Response({"error": "Sin permisos."}, status=403)
        operarios = User.objects.filter(role=User.Role.OPERARIO).order_by("first_name", "username")
        data = [
            {
                "id": u.id,
                "username": u.username,
                "nombre": u.get_full_name() or u.username,
            }
            for u in operarios
        ]
        return Response(data)
