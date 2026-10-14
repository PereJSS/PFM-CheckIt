from rest_framework.permissions import BasePermission


def es_admin(usuario):
    # Robustez: admite roles legacy y flags de Django incluso si el método is_admin no existe.
    if not getattr(usuario, "is_authenticated", False):
        return False

    role = str(getattr(usuario, "role", "") or "").strip().upper()
    return (
        role in {"ADMINISTRADOR", "ADMIN"}
        or bool(getattr(usuario, "is_superuser", False))
        or bool(getattr(usuario, "is_staff", False))
    )


class IsAdmin(BasePermission):
    """Solo administradores autenticados (incluye roles legacy y flags de Django)."""
    message = "Sin permisos."

    def has_permission(self, request, view):
        return es_admin(request.user)
