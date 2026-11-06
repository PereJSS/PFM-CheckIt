"""Lista lo que nadie gestiona tras separar los operarios por cliente (5 y 6 nov):

1. Operarios sin administrador: ningún cliente puede verlos ni asignarlos.
2. Propiedades sin dueño o cuyo dueño es un operario (creadas antes de exigir IsAdmin): no las ve
   ningún administrador.

Solo lee. Imprime la orden sugerida para la Shell cuando no hay duda (la misma regla que
la migración users/0003: un único dueño, y que sea un administrador activo); si la hay, pide
revisarlo a mano. No muestra emails ni direcciones, para no dejarlos en el log de Render.

Los nombres de propiedad los escribió un operario: se escapan los caracteres de control para
que un salto de línea no fabrique una «sugerencia» falsa que luego se copie en la Shell.
"""
import unicodedata

from django.core.management.base import BaseCommand
from django.db.models import Prefetch, Q

from inspecciones.models import Inspeccion
from propiedades.models import Propiedad
from users.models import User

LARGO_MAXIMO = 60


def _texto(valor):
    """Texto libre en una sola línea: control, separadores de línea y ANSI escapados."""
    limpio = ''.join(
        c.encode('unicode_escape').decode('ascii')
        if unicodedata.category(c)[0] == 'C' or unicodedata.category(c) in ('Zl', 'Zp')
        else c
        for c in str(valor)
    )
    return limpio if len(limpio) <= LARGO_MAXIMO else limpio[: LARGO_MAXIMO - 1] + '…'


def _usuario(u):
    marca = '' if u.is_active else ' [inactivo]'
    return f'{_texto(u.username)} (#{u.pk}){marca}'


def _ids(inspecciones):
    return ', '.join(f'#{i.pk}' for i in inspecciones) or 'ninguna'


def _admin_activo(u):
    return u is not None and u.role == User.Role.ADMIN and u.is_active


class Command(BaseCommand):
    help = 'Lista operarios sin administrador y propiedades sin dueño o de operarios (solo lectura).'

    def handle(self, *args, **options):
        n_operarios = self._operarios_sin_administrador()
        n_propiedades = self._propiedades_de_operarios()
        if n_operarios == 0 and n_propiedades == 0:
            self.stdout.write('Sin huérfanos.')
            return
        self.stdout.write(
            f'\nResumen: {n_operarios} operario(s) sin administrador, '
            f'{n_propiedades} propiedad(es) sin dueño o de operarios.\n'
            'Las órdenes, una a una y tras revisarlas, en `manage.py shell` con '
            '`from users.models import User; from propiedades.models import Propiedad`.'
        )

    def _operarios_sin_administrador(self):
        operarios = (
            User.objects.filter(role=User.Role.OPERARIO, administrador__isnull=True)
            .order_by('pk')
            .prefetch_related(
                Prefetch(
                    'inspecciones_asignadas',
                    queryset=Inspeccion.objects.select_related('propiedad__owner').order_by('pk'),
                )
            )
        )
        total = 0
        for operario in operarios:
            if total == 0:
                self.stdout.write('Operarios sin administrador:')
            total += 1
            inspecciones = list(operario.inspecciones_asignadas.all())
            self.stdout.write(f'- {_usuario(operario)}: inspecciones {_ids(inspecciones)}')
            if not inspecciones:
                self.stdout.write('    sin inspecciones: desactivarlo o asignarlo a mano.')
                continue
            duenos = {i.propiedad.owner_id: i.propiedad.owner for i in inspecciones}
            if None in duenos:
                self.stdout.write('    alguna propiedad no tiene dueño: revisar a mano.')
                continue
            if len(duenos) > 1:
                nombres = ', '.join(_usuario(d) for d in sorted(duenos.values(), key=lambda d: d.pk))
                self.stdout.write(f'    varios dueños ({nombres}): revisar a mano.')
                continue
            dueno = next(iter(duenos.values()))
            if not _admin_activo(dueno):
                self.stdout.write(
                    f'    el dueño {_usuario(dueno)} no es un administrador activo: revisar a mano.'
                )
                continue
            self.stdout.write(
                f'    sugerencia, asignar a {_usuario(dueno)} (antes administrador_id=None): '
                f'User.objects.filter(pk={operario.pk}).update(administrador_id={dueno.pk})'
            )
        return total

    def _propiedades_de_operarios(self):
        propiedades = (
            Propiedad.objects.filter(Q(owner__isnull=True) | Q(owner__role=User.Role.OPERARIO))
            .select_related('owner__administrador')
            .prefetch_related(
                Prefetch(
                    'inspecciones',
                    queryset=Inspeccion.objects.select_related('operario').order_by('pk'),
                )
            )
            .order_by('pk')
        )
        total = 0
        for propiedad in propiedades:
            if total == 0:
                self.stdout.write('Propiedades sin dueño o cuyo dueño es un operario:')
            total += 1
            inspecciones = list(propiedad.inspecciones.all())
            operario = propiedad.owner
            nombre = f'{_texto(propiedad.nombre)} (#{propiedad.pk})'
            if operario is None:
                self.stdout.write(
                    f'- {nombre}, sin dueño: inspecciones {_ids(inspecciones)}. Revisar a mano.'
                )
                continue
            self.stdout.write(
                f'- {nombre}, del operario {_usuario(operario)}: inspecciones {_ids(inspecciones)}'
            )
            candidato = operario.administrador
            if candidato is None:
                self.stdout.write('    el operario no tiene administrador: asignárselo primero.')
                continue
            if not _admin_activo(candidato):
                self.stdout.write(
                    f'    su administrador {_usuario(candidato)} no es un administrador activo: '
                    'revisar a mano.'
                )
                continue
            # Pasarla al candidato le enseñaría las inspecciones de otros: solo sin dudas
            ajenas = [
                i for i in inspecciones
                if i.operario is not None
                and i.operario_id != operario.pk
                and i.operario.administrador_id != candidato.pk
            ]
            if ajenas:
                self.stdout.write(
                    f'    inspecciones de operarios de otro cliente o sin administrador '
                    f'({_ids(ajenas)}): revisar a mano.'
                )
                continue
            self.stdout.write(
                f'    sugerencia, pasarla a {_usuario(candidato)} (antes owner_id={operario.pk}): '
                f'Propiedad.objects.filter(pk={propiedad.pk}).update(owner_id={candidato.pk})'
            )
        return total
