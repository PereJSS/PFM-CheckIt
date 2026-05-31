from django.db import models
from django.contrib.auth.models import AbstractUser

class User(AbstractUser):
    """
    Model personalizado de usuario que extiende de AbstractUser para incluir un campo de rol.
    """
    class Role(models.TextChoices):
        ADMIN = 'ADMINISTRADOR', 'Administrador'
        OPERARIO = 'OPERARIO', 'Operario'  # <--- ¡IMPORTANTE! Ahora tiene la misma indentación que ADMIN

    role = models.CharField(
        max_length=20, 
        choices=Role.choices, 
        default=Role.OPERARIO, # Ahora sí lo encontrará dentro de Role
        verbose_name="Rol del usuario"
    )

    def is_admin(self):
        role = (self.role or "").strip().upper()
        return (
            role in {"ADMINISTRADOR", "ADMIN"}
            or self.is_superuser
            or self.is_staff
        )

    def is_operator(self):
        return self.role == self.Role.OPERARIO

    def role_display(self) -> str:
        roles = {str(valor): etiqueta for valor, etiqueta in self.Role.choices}
        return roles.get(self.role, self.role)

    def __str__(self):
        return f"{self.username} - {self.role_display()}"