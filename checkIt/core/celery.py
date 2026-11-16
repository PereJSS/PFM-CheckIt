import os
from pathlib import Path

from celery import Celery
from celery.signals import worker_ready

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')

app = Celery('checkIt')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()


@app.task(name='core.latido', ignore_result=True)
def latido():
    """Deja constancia de que el worker ejecuta lo que el beat programa (ver core/salud.py).
    Antes comprueba la BD: sin ella no sella ni genera PDF, y no debe parecer sano."""
    from django.db import connection

    from core.salud import registrar_latido

    connection.ensure_connection()
    registrar_latido()


@worker_ready.connect
def avisar_worker_listo(**kwargs):
    """start.sh espera a esta marca para arrancar gunicorn (ver start.sh)."""
    marca = os.environ.get('CHECKIT_CELERY_LISTO')
    if marca:
        Path(marca).touch()
