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
    # Solo operarios: el administrador (cliente) que lo dio de alta. Solo ese administrador
    # lo ve, lo borra y se lo asigna. Sin él (operarios anteriores), nadie puede asignarlo.
    # SET_NULL: borrar al administrador no borra la cuenta ni el rastro de sus inspecciones
    administrador = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='operarios',
        limit_choices_to={'role': 'ADMINISTRADOR'},
        verbose_name="Administrador del operario",
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