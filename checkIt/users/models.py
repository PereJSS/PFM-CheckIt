from django.core.exceptions import ValidationError
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
        verbose_name="Administrador del operario",
    )

    class Meta(AbstractUser.Meta):
        constraints = [
            # Regla de una fila: la BD la garantiza también ante un update() en la Shell. Que el
            # apuntado sea un administrador compara dos filas: solo lo comprueba clean()
            models.CheckConstraint(
                check=(
                    models.Q(administrador__isnull=True)
                    | (models.Q(role='OPERARIO') & ~models.Q(administrador=models.F('id')))
                ),
                name='users_administrador_solo_en_operarios',
            ),
        ]

    def clean(self):
        super().clean()
        if self.administrador_id is None:
            return
        if self.role != self.Role.OPERARIO:
            raise ValidationError({'administrador': 'Solo un operario tiene administrador.'})
        if self.pk is not None and self.administrador_id == self.pk:
            raise ValidationError({'administrador': 'Un operario no puede ser su propio administrador.'})
        if self.administrador.role != self.Role.ADMIN:
            raise ValidationError({'administrador': 'El administrador debe ser un usuario administrador.'})

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


class Invitacion(models.Model):
    """Enlace de un solo uso para darse de alta como administrador (cliente). Solo la crea el
    superusuario (`manage.py crear_invitacion`). El token en claro solo existe en el enlace;
    aquí queda su huella sha256. Si lleva email, el alta tiene que usar ese email."""
    token_hash = models.CharField(max_length=64, unique=True, editable=False)
    email = models.EmailField(blank=True, verbose_name='Email del invitado')
    creada_en = models.DateTimeField(auto_now_add=True)
    caduca_en = models.DateTimeField()
    usada_en = models.DateTimeField(null=True, blank=True)
    usada_por = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )

    class Meta:
        verbose_name = 'invitación'
        verbose_name_plural = 'invitaciones'
        ordering = ['-creada_en']

    def __str__(self):
        return f'Invitación {self.pk} ({self.email or "sin email"})'


class RotacionRefresh(models.Model):
    """De qué refresh nació cada uno al rotar. Si un refresh ya rotado vuelve cuando su sucesor
    también se ha rotado, dos clientes tienen la misma cadena: posible robo. Se borra en cascada
    con la purga diaria de los refresh caducados."""
    padre = models.OneToOneField(
        'token_blacklist.OutstandingToken', on_delete=models.CASCADE, related_name='rotacion',
    )
    hijo = models.OneToOneField(
        'token_blacklist.OutstandingToken', on_delete=models.CASCADE, related_name='origen',
    )
    creado_en = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.padre_id} → {self.hijo_id}'
