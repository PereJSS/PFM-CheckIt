from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('inspecciones', '0009_evidencia_hash_idx'),
    ]

    operations = [
        migrations.AddField(
            model_name='inspeccion',
            name='clave_verificacion',
            field=models.CharField(editable=False, max_length=64, null=True),
        ),
    ]
