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
        return self.role == self.Role.ADMIN

    def is_operator(self):
        return self.role == self.Role.OPERARIO

    def __str__(self):
        return f"{self.username} - {self.get_role_display()}"