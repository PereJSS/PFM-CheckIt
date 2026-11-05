"""Liga cada operario anterior a su administrador cuando no hay duda: todas sus
inspecciones son de propiedades de un único administrador. Si no tiene inspecciones o
las tiene de varios, se queda sin administrador y nadie puede asignarlo hasta que se le
ponga a mano (no se adivina a qué cliente pertenece)."""
from django.db import migrations


def rellenar(apps, schema_editor):
    User = apps.get_model('users', 'User')
    Inspeccion = apps.get_model('inspecciones', 'Inspeccion')
    for operario in User.objects.filter(role='OPERARIO', administrador__isnull=True):
        duenos = set(
            Inspeccion.objects.filter(operario=operario).values_list('propiedad__owner', flat=True).distinct()
        )
        if len(duenos) != 1:
            continue
        dueno = duenos.pop()
        if User.objects.filter(pk=dueno, role='ADMINISTRADOR').exists():
            User.objects.filter(pk=operario.pk).update(administrador=dueno)


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0002_operario_administrador'),
        # Solo hace falta Inspeccion.operario (desde 0001): depender de la última arrastraría
        # todo inspecciones a quien construya el estado de users
        ('inspecciones', '0001_initial'),
        # Propiedad.owner llega en propiedades 0002: sin ella, el estado podría no tenerlo
        ('propiedades', '0002_propiedad_owner'),
    ]

    operations = [
        migrations.RunPython(rellenar, migrations.RunPython.noop),
    ]
