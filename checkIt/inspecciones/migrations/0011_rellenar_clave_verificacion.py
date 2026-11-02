import secrets

from django.db import migrations


def rellenar_claves(apps, schema_editor):
    Inspeccion = apps.get_model('inspecciones', 'Inspeccion')
    # Lista y no iterator(): el cursor de servidor falla tras un pgbouncer en modo transacción
    for pk in list(Inspeccion.objects.filter(clave_verificacion__isnull=True).values_list('pk', flat=True)):
        Inspeccion.objects.filter(pk=pk).update(clave_verificacion=secrets.token_hex(32))


class Migration(migrations.Migration):

    dependencies = [
        ('inspecciones', '0010_inspeccion_clave_verificacion'),
    ]

    operations = [
        migrations.RunPython(rellenar_claves, migrations.RunPython.noop),
    ]
