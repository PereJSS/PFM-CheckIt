

import os
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings

from core.salud import CLAVE_LATIDO
from kombu import Connection
from kombu.exceptions import OperationalError
from rest_framework.test import APITestCase as _APITestCase


class BrokerRedisTest(SimpleTestCase):
    """Los tests usan un broker en memoria y nunca cargan el transporte de Redis. Si kombu y
    redis-py son incompatibles, Celery no arranca en producción y la web sigue como si nada."""

    def test_el_transporte_de_redis_acepta_la_version_de_redis_py(self):
        conexion = Connection(
            'redis://127.0.0.1:1/0',
            transport_options={'socket_timeout': 1, 'socket_connect_timeout': 1},
        )
        self.addCleanup(conexion.release)
        # El puerto está cerrado: debe llegar a intentar conectar, no fallar antes por versiones.
        with self.assertRaises(OperationalError):
            conexion.ensure_connection(max_retries=1, interval_start=0)


class OpenApiDocsTest(_APITestCase):
    """La documentación es pública: la consumen los integradores de PMS."""

    def test_schema_public_and_declares_api_key_scheme(self):
        resp = self.client.get('/api/v1/schema/?format=json')
        self.assertEqual(resp.status_code, 200)
        schemes = resp.json()['components']['securitySchemes']
        self.assertEqual(schemes['ApiKeyAuth']['name'], 'Authorization')
        self.assertIn('ApiKeyAuth', str(resp.json()['paths']['/api/v1/webhooks/pms/']['post']['security']))

    def test_swagger_and_redoc_public(self):
        self.assertEqual(self.client.get('/api/v1/docs/').status_code, 200)
        self.assertEqual(self.client.get('/api/v1/redoc/').status_code, 200)

    def test_claim_report_documenta_sus_respuestas(self):
        paths = self.client.get('/api/v1/schema/?format=json').json()['paths']
        informe = paths['/api/v1/inspecciones/{id}/claim-report/']['get']['responses']
        self.assertEqual(set(informe), {'200', '202', '403', '404', '503'})
        self.assertIn('application/pdf', informe['200']['content'])
        estado = paths['/api/v1/inspecciones/{id}/claim-report-status/']['get']['responses']
        self.assertEqual(set(estado), {'200', '403', '404'})
        propiedades = self.client.get('/api/v1/schema/?format=json').json()['components']['schemas']
        self.assertEqual(set(propiedades['EstadoPdf']['properties']), {'estado', 'pdf_listo'})

    def test_propiedades_documenta_el_403_de_los_operarios(self):
        # Un operario recibe 403 en todas las operaciones: el integrador debe saberlo
        paths = self.client.get('/api/v1/schema/?format=json').json()['paths']
        rutas = {k: v for k, v in paths.items() if k.startswith('/api/v1/propiedades/')}
        self.assertEqual(len(rutas), 2)
        for ruta, metodos in rutas.items():
            for metodo, operacion in metodos.items():
                with self.subTest(ruta=ruta, metodo=metodo):
                    self.assertIn('403', operacion['responses'])
                    # Las respuestas de éxito siguen documentadas con su esquema
                    exito = {'get': '200', 'post': '201', 'put': '200', 'patch': '200', 'delete': '204'}[metodo]
                    self.assertIn(exito, operacion['responses'])


# Los tests de salud no deben tocar un Redis real aunque haya REDIS_URL en el entorno
CACHE_LOCAL = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}


class SaludWebTest(TestCase):
    """/healthz es la ruta que vigila Render: si falla 60 s seguidos reinicia la instancia, y
    en un deploy no da tráfico a la versión nueva. Solo debe decir que Django responde."""

    def test_responde_200_sin_tocar_la_base_de_datos(self):
        with self.assertNumQueries(0):
            resp = self.client.get('/healthz')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content, b'ok')

    def test_responde_aunque_el_host_no_este_en_allowed_hosts(self):
        # Render puede llamar con la IP interna de la instancia como Host
        resp = self.client.get('/healthz', HTTP_HOST='10.0.0.5:10000')
        self.assertEqual(resp.status_code, 200)

    def test_solo_admite_get_y_head(self):
        self.assertEqual(self.client.head('/healthz').status_code, 200)
        self.assertEqual(self.client.post('/healthz').status_code, 405)

    def test_cabeceras_sin_cache_y_nosniff(self):
        resp = self.client.get('/healthz')
        self.assertIn('no-store', resp['Cache-Control'])
        self.assertEqual(resp['X-Content-Type-Options'], 'nosniff')


@override_settings(CACHES=CACHE_LOCAL)
class SaludCeleryTest(SimpleTestCase):
    """/healthz/celery lo consulta un monitor externo que avisa por correo. Un latido reciente
    prueba que el beat programa, el broker reparte y el worker ejecuta."""

    def setUp(self):
        cache.delete(CLAVE_LATIDO)
        self.addCleanup(cache.delete, CLAVE_LATIDO)

    def _get(self):
        resp = self.client.get('/healthz/celery')
        return resp, resp.json()

    def test_sin_latido_da_503(self):
        resp, cuerpo = self._get()
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(cuerpo, {'celery': 'sin_latido', 'segundos_desde_latido': None})

    def test_latido_reciente_da_200(self):
        cache.set(CLAVE_LATIDO, time.time() - 30, timeout=None)
        resp, cuerpo = self._get()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(cuerpo['celery'], 'ok')
        self.assertTrue(29 <= cuerpo['segundos_desde_latido'] <= 35)

    def test_latido_atrasado_da_503(self):
        cache.set(CLAVE_LATIDO, time.time() - settings.SALUD_LATIDO_MAX_SEG - 5, timeout=None)
        resp, cuerpo = self._get()
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(cuerpo['celery'], 'atrasado')

    def test_el_limite_es_configurable(self):
        cache.set(CLAVE_LATIDO, time.time() - 100, timeout=None)
        with self.settings(SALUD_LATIDO_MAX_SEG=60):
            self.assertEqual(self._get()[0].status_code, 503)

    def test_si_la_cache_falla_da_503_y_registra_el_tipo_de_error(self):
        with mock.patch('core.salud.cache.get', side_effect=TimeoutError()), \
                self.assertLogs('core.salud', level='WARNING') as logs:
            resp, cuerpo = self._get()
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(cuerpo, {'celery': 'cache_no_responde', 'segundos_desde_latido': None})
        # Una excepción sin mensaje no debe dar una línea vacía
        self.assertIn('TimeoutError', '\n'.join(logs.output))

    def test_un_valor_extrano_en_la_cache_no_da_500(self):
        cache.set(CLAVE_LATIDO, 'basura', timeout=None)
        resp, cuerpo = self._get()
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(cuerpo['celery'], 'sin_latido')

    def test_cabeceras_sin_cache_y_nosniff(self):
        resp = self._get()[0]
        self.assertIn('no-store', resp['Cache-Control'])
        self.assertEqual(resp['X-Content-Type-Options'], 'nosniff')

    def test_no_admite_post(self):
        self.assertEqual(self.client.post('/healthz/celery').status_code, 405)

    def test_no_aparece_en_openapi(self):
        paths = self.client.get('/api/v1/schema/?format=json').json()['paths']
        self.assertFalse([p for p in paths if 'healthz' in p])


@override_settings(CACHES=CACHE_LOCAL)
class LatidoCeleryTest(TestCase):

    def setUp(self):
        cache.delete(CLAVE_LATIDO)
        self.addCleanup(cache.delete, CLAVE_LATIDO)

    def test_el_worker_de_core_ejecuta_el_latido(self):
        import core
        from core.celery import app
        # `celery -A core` carga esta app, y con ella la tarea
        self.assertIs(core.celery_app, app)
        antes = time.time()
        self.assertTrue(app.tasks['core.latido'].apply().successful())
        self.assertTrue(antes <= cache.get(CLAVE_LATIDO) <= time.time())

    def test_sin_base_de_datos_no_hay_latido(self):
        # Un worker que no llega a la BD no sella ni genera PDF: que no diga que está bien
        from django.db import OperationalError
        from core.celery import app
        with mock.patch('django.db.connection.ensure_connection', side_effect=OperationalError('bd caída')):
            self.assertTrue(app.tasks['core.latido'].apply().failed())
        self.assertIsNone(cache.get(CLAVE_LATIDO))

    def test_todas_las_tareas_del_beat_existen(self):
        from core.celery import app
        app.loader.import_default_modules()
        for nombre, entrada in settings.CELERY_BEAT_SCHEDULE.items():
            self.assertIn(entrada['task'], app.tasks, nombre)

    def test_el_beat_programa_el_latido_con_caducidad(self):
        entrada = settings.CELERY_BEAT_SCHEDULE['latido-celery']
        self.assertEqual(entrada['task'], 'core.latido')
        self.assertEqual(entrada['schedule'], float(settings.SALUD_LATIDO_SEG))
        self.assertEqual(entrada['options']['expires'], settings.SALUD_LATIDO_SEG)

    def test_el_limite_aguanta_una_tarea_larga_del_pool_solo(self):
        # --pool=solo: un PDF de hasta 300 s retrasa el latido; no debe dar un 503 falso
        self.assertGreaterEqual(settings.SALUD_LATIDO_MAX_SEG, 300 + 2 * settings.SALUD_LATIDO_SEG)

    def test_worker_ready_deja_la_marca_que_espera_start_sh(self):
        from celery.signals import worker_ready
        marca = Path(tempfile.mkdtemp()) / 'listo'
        self.addCleanup(shutil.rmtree, marca.parent, True)
        with mock.patch.dict(os.environ, {'CHECKIT_CELERY_LISTO': str(marca)}):
            worker_ready.send(sender=None)
        self.assertTrue(marca.exists())

    def test_sin_la_variable_worker_ready_no_escribe_nada(self):
        from celery.signals import worker_ready
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop('CHECKIT_CELERY_LISTO', None)
            worker_ready.send(sender=None)  # no debe fallar


class StartShTest(SimpleTestCase):
    """start.sh arranca Celery y, cuando está listo, gunicorn. Si uno termina, el otro también y
    el script sale con error: Render reinicia la instancia en vez de servir la web sin Celery.
    Si Celery muere antes de estar listo, gunicorn no llega a arrancar: en un deploy, Render
    lo da por fallido y mantiene la versión anterior."""

    SCRIPT = Path(settings.BASE_DIR) / 'start.sh'

    def setUp(self):
        if shutil.which('bash') is None:
            self.skipTest('sin bash')
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)

    def _falso(self, nombre, salida=None, listo=True):
        """Proceso falso que apunta su pid; si `listo`, deja la marca de Celery listo; si se
        indica `salida`, termina con ese código, y si no, queda vivo."""
        ruta = self.dir / nombre
        cuerpo = f'echo $$ > "{self.dir / (nombre + ".pid")}"\n'
        if listo:
            cuerpo += 'touch "$CHECKIT_CELERY_LISTO"\n'
        cuerpo += f'sleep 0.3\nexit {salida}\n' if salida is not None else 'exec sleep 30\n'
        ruta.write_text('#!/bin/sh\n' + cuerpo)
        ruta.chmod(0o755)
        return str(ruta)

    def _lanzar(self, celery, web, espera='60'):
        env = {**os.environ, 'CHECKIT_CMD_CELERY': celery, 'CHECKIT_CMD_WEB': web,
               'CHECKIT_ESPERA_CELERY_SEG': espera}
        # Grupo de procesos propio: si un test falla, se mata todo lo que lanzó el script
        proc = subprocess.Popen(['bash', str(self.SCRIPT)], env=env, start_new_session=True,
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)

        def limpiar():
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.communicate()
        self.addCleanup(limpiar)
        return proc

    def _terminar(self, proc):
        _, err = proc.communicate(timeout=10)
        return proc.returncode, err

    def _pid(self, nombre, plazo=5.0):
        fichero = self.dir / (nombre + '.pid')
        limite = time.monotonic() + plazo
        while time.monotonic() < limite:
            try:
                texto = fichero.read_text().strip()
            except FileNotFoundError:
                texto = ''
            if texto:
                return int(texto)
            time.sleep(0.05)
        self.fail(f'{nombre} no arrancó')

    def _vivo(self, pid):
        # El script hace `wait` antes de salir: sus hijos ya están recogidos, no quedan zombis
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        return True

    def test_si_celery_muere_antes_de_estar_listo_no_arranca_la_web(self):
        proc = self._lanzar(self._falso('celery', salida=3, listo=False), self._falso('web'))
        codigo, err = self._terminar(proc)
        self.assertEqual(codigo, 3)
        self.assertIn('antes de estar listo', err)
        self.assertFalse((self.dir / 'web.pid').exists())

    def test_si_celery_muere_despues_para_la_web_y_sale_con_su_codigo(self):
        proc = self._lanzar(self._falso('celery', salida=3), self._falso('web'))
        web = self._pid('web')
        codigo, err = self._terminar(proc)
        self.assertEqual(codigo, 3)
        self.assertIn('celery terminó', err)
        self.assertFalse(self._vivo(web))

    def test_si_la_web_sale_bien_igualmente_sale_con_error(self):
        proc = self._lanzar(self._falso('celery'), self._falso('web', salida=0))
        celery = self._pid('celery')
        codigo, err = self._terminar(proc)
        self.assertNotEqual(codigo, 0)
        self.assertIn('gunicorn terminó', err)
        self.assertFalse(self._vivo(celery))

    def test_si_celery_tarda_arranca_la_web_igualmente_y_avisa(self):
        # Redis caído: Celery reintenta sin morir. La web no debe quedar como rehén
        proc = self._lanzar(self._falso('celery', listo=False), self._falso('web'), espera='1')
        celery, web = self._pid('celery'), self._pid('web', plazo=8)
        proc.send_signal(signal.SIGTERM)
        codigo, err = self._terminar(proc)
        self.assertEqual(codigo, 0)
        self.assertIn('sin avisar de que está listo', err)
        self.assertFalse(self._vivo(celery))
        self.assertFalse(self._vivo(web))

    def test_sigterm_para_los_dos_y_sale_bien(self):
        proc = self._lanzar(self._falso('celery'), self._falso('web'))
        celery, web = self._pid('celery'), self._pid('web')
        proc.send_signal(signal.SIGTERM)
        codigo, _ = self._terminar(proc)
        self.assertEqual(codigo, 0)
        self.assertFalse(self._vivo(celery))
        self.assertFalse(self._vivo(web))

    def test_sigterm_mientras_espera_a_celery(self):
        proc = self._lanzar(self._falso('celery', listo=False), self._falso('web'))
        celery = self._pid('celery')
        proc.send_signal(signal.SIGTERM)
        codigo, _ = self._terminar(proc)
        self.assertEqual(codigo, 0)
        self.assertFalse(self._vivo(celery))
        self.assertFalse((self.dir / 'web.pid').exists())
