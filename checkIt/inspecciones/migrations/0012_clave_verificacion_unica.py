from importlib import import_module

from django.db import migrations, models

import inspecciones.models


def rellenar_claves(apps, schema_editor):
    import_module('inspecciones.migrations.0011_rellenar_clave_verificacion').rellenar_claves(apps, schema_editor)


class Migration(migrations.Migration):
    # El código viejo sigue creando inspecciones durante el deploy, sin clave. Se rellena otra
    # vez justo antes, y sin transacción global: si entra otra fila y falla, el reintento la
    # rellena. Con 0011 como único relleno, el deploy quedaría bloqueado para siempre.
    atomic = False

    dependencies = [
        ('inspecciones', '0011_rellenar_clave_verificacion'),
    ]

    operations = [
        migrations.RunPython(rellenar_claves, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='inspeccion',
            name='clave_verificacion',
            field=models.CharField(default=inspecciones.models.generar_clave_verificacion, editable=False, max_length=64, unique=True),
        ),
    ]
