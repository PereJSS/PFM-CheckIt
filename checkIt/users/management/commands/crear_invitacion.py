"""Crea una invitación de alta de administrador e imprime su enlace UNA sola vez.

    python manage.py crear_invitacion [--email cliente@empresa.com] [--dias 7]

El token no se guarda (solo su huella sha256): si se pierde el enlace, se crea otra invitación y
se borra la anterior en /admin/. El enlace lleva el token tras `#`, que el navegador no envía al
servidor ni en el Referer. Mandarlo al cliente por un canal privado.
"""
import logging

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email

from users.invitaciones import DIAS_POR_DEFECTO, crear

DIAS_MAXIMOS = 30

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Crea una invitación de alta de administrador e imprime el enlace (una sola vez).'

    def add_arguments(self, parser):
        parser.add_argument('--email', default='', help='Solo podrá darse de alta con este email.')
        parser.add_argument('--dias', type=int, default=DIAS_POR_DEFECTO,
                            help=f'Días hasta que caduca (1-{DIAS_MAXIMOS}, por defecto {DIAS_POR_DEFECTO}).')

    def handle(self, *args, email, dias, **options):
        if not 1 <= dias <= DIAS_MAXIMOS:
            raise CommandError(f'--dias tiene que estar entre 1 y {DIAS_MAXIMOS}.')
        email = email.strip()
        if email:
            try:
                validate_email(email)
            except ValidationError:
                raise CommandError('--email no es un email válido.') from None
        invitacion, token = crear(email=email, dias=dias)
        logger.info('Invitación %s creada (nominativa: %s, caduca %s)', invitacion.pk,
                    bool(invitacion.email), invitacion.caduca_en.isoformat())
        enlace = f"{settings.FRONTEND_URL.rstrip('/')}/register#invitacion={token}"
        self.stdout.write(f'Invitación {invitacion.pk} creada; caduca el {invitacion.caduca_en:%Y-%m-%d %H:%M} UTC.')
        if invitacion.email:
            self.stdout.write(f'Solo vale para {invitacion.email}.')
        self.stdout.write('Enlace (no se volverá a mostrar; si se pierde, borra esta invitación en /admin/ y crea otra):')
        self.stdout.write(enlace)
