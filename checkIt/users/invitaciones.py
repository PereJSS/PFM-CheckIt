"""Invitaciones de alta: el token en claro solo sale una vez, en el enlace."""
import hashlib
import secrets
from datetime import timedelta

from django.utils import timezone

from .models import Invitacion

DIAS_POR_DEFECTO = 7


def huella(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def crear(email: str = '', dias: int = DIAS_POR_DEFECTO) -> tuple[Invitacion, str]:
    """Crea la invitación y devuelve también el token en claro (no se puede recuperar después)."""
    token = secrets.token_urlsafe(32)
    invitacion = Invitacion.objects.create(
        token_hash=huella(token),
        email=email.strip().lower(),
        caduca_en=timezone.now() + timedelta(days=dias),
    )
    return invitacion, token


def libres():
    """Invitaciones que aún se pueden usar."""
    return Invitacion.objects.filter(usada_en__isnull=True, caduca_en__gt=timezone.now())


def vigente(token) -> Invitacion | None:
    if not isinstance(token, str) or not token:
        return None
    return libres().filter(token_hash=huella(token)).first()
