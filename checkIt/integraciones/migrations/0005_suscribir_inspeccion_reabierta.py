from django.db import migrations

REABIERTA = 'inspeccion.reabierta'
# Quien recibe la raíz (completada o sellado) necesita saber cuándo deja de valer
GUARDAN_LA_RAIZ = {'inspeccion.completada', 'manifiesto.sellado'}


def suscribir_reabierta(apps, schema_editor):
    WebhookDestino = apps.get_model('integraciones', 'WebhookDestino')
    for pk, eventos in WebhookDestino.objects.values_list('pk', 'eventos').iterator():
        eventos = list(eventos or [])
        if REABIERTA in eventos or not GUARDAN_LA_RAIZ & set(eventos):
            continue
        # UPDATE solo de `eventos`: no toca ultima_secuencia
        WebhookDestino.objects.filter(pk=pk).update(eventos=eventos + [REABIERTA])


class Migration(migrations.Migration):

    dependencies = [
        ('integraciones', '0004_secuencia_entregas'),
    ]

    operations = [
        # Sin vuelta atrás: quitar el evento borraría también las suscripciones hechas a mano
        migrations.RunPython(suscribir_reabierta, migrations.RunPython.noop),
    ]
