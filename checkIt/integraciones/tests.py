from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APITestCase

from inspecciones.models import Inspeccion
from propiedades.models import Propiedad
from users.models import User

from .models import ApiKey, WebhookEvent, hash_api_key
from .services import procesar_evento

WEBHOOK_URL = '/api/v1/webhooks/pms/'
API_KEYS_URL = '/api/v1/integraciones/api-keys/'
EVENTOS_URL = '/api/v1/integraciones/eventos/'


def _checkout(event_id='evt_1', property_id='LISTING-42', **extra):
    return {
        'event_id': event_id,
        'event_type': 'reservation.checkout',
        'data': {'property_id': property_id, 'reservation_id': 'R-1'},
        **extra,
    }


class BaseIntegracionTest(APITestCase):
    def setUp(self):
        cache.clear()  # el throttle guarda contadores en caché
        self.admin = User.objects.create_user(username='admin1', password='Pass1234!', role=User.Role.ADMIN)
        self.admin2 = User.objects.create_user(username='admin2', password='Pass1234!', role=User.Role.ADMIN)
        self.operario = User.objects.create_user(username='op', password='Pass1234!', role=User.Role.OPERARIO)
        self.prop = Propiedad.objects.create(
            owner=self.admin, nombre='Piso Centro', direccion='Calle 1', external_id='LISTING-42'
        )
        self.api_key, self.clave = ApiKey.generar(self.admin, 'Guesty')

    def _post_webhook(self, payload, clave=None):
        clave = self.clave if clave is None else clave
        return self.client.post(
            WEBHOOK_URL, payload, format='json', HTTP_AUTHORIZATION=f'Api-Key {clave}'
        )


class ApiKeyModelTest(TestCase):
    def test_generar_guarda_solo_hash(self):
        admin = User.objects.create_user(username='a', password='x', role=User.Role.ADMIN)
        api_key, clave = ApiKey.generar(admin, 'PMS')
        self.assertTrue(clave.startswith(f'ck_{api_key.prefijo}_'))
        self.assertEqual(api_key.hash_sha256, hash_api_key(clave))
        self.assertNotIn(clave, api_key.hash_sha256)
        self.assertNotIn(clave.split('_', 2)[2], str(api_key))


@patch('integraciones.tasks.procesar_evento_pms.apply_async')
class PmsWebhookAuthTest(BaseIntegracionTest):
    def test_sin_cabecera_devuelve_401(self, _delay):
        resp = self.client.post(WEBHOOK_URL, _checkout(), format='json')
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_clave_incorrecta_devuelve_401(self, _delay):
        falsa = f'ck_{self.api_key.prefijo}_otrosecreto'
        self.assertEqual(self._post_webhook(_checkout(), clave=falsa).status_code, 401)

    def test_clave_mal_formada_devuelve_401(self, _delay):
        self.assertEqual(self._post_webhook(_checkout(), clave='basura').status_code, 401)

    def test_clave_revocada_devuelve_401(self, _delay):
        self.api_key.revocada = True
        self.api_key.save()
        self.assertEqual(self._post_webhook(_checkout()).status_code, 401)

    def test_owner_inactivo_devuelve_401(self, _delay):
        self.admin.is_active = False
        self.admin.save()
        self.assertEqual(self._post_webhook(_checkout()).status_code, 401)

    def test_jwt_no_sirve_en_webhook(self, _delay):
        self.client.force_authenticate(user=None)
        from rest_framework_simplejwt.tokens import AccessToken
        token = AccessToken.for_user(self.admin)
        resp = self.client.post(WEBHOOK_URL, _checkout(), format='json', HTTP_AUTHORIZATION=f'Bearer {token}')
        self.assertEqual(resp.status_code, 401)

    def test_api_key_no_sirve_en_resto_de_api(self, _delay):
        resp = self.client.get('/api/v1/propiedades/', HTTP_AUTHORIZATION=f'Api-Key {self.clave}')
        self.assertEqual(resp.status_code, 401)

    def test_actualiza_ultimo_uso(self, _delay):
        self._post_webhook(_checkout())
        self.api_key.refresh_from_db()
        self.assertIsNotNone(self.api_key.ultimo_uso)

    def test_ultimo_uso_no_se_reescribe_en_cada_peticion(self, _delay):
        self._post_webhook(_checkout('a'))
        self.api_key.refresh_from_db()
        primero = self.api_key.ultimo_uso
        self._post_webhook(_checkout('b'))
        self.api_key.refresh_from_db()
        self.assertEqual(self.api_key.ultimo_uso, primero)


class PmsWebhookFlujoTest(BaseIntegracionTest):
    @patch('integraciones.tasks.procesar_evento_pms.apply_async')
    def test_evento_valido_se_guarda_y_encola(self, mock_delay):
        resp = self._post_webhook(_checkout())
        self.assertEqual(resp.status_code, status.HTTP_202_ACCEPTED)
        evento = WebhookEvent.objects.get()
        self.assertEqual(evento.owner, self.admin)
        self.assertEqual(evento.api_key, self.api_key)
        self.assertEqual(evento.estado, WebhookEvent.Estado.RECIBIDO)
        mock_delay.assert_called_once_with(args=[evento.pk], retry=False)
        self.assertFalse(resp.data['duplicado'])

    @patch('integraciones.tasks.procesar_evento_pms.apply_async')
    def test_evento_duplicado_ya_procesado_no_se_repite(self, mock_delay):
        self._post_webhook(_checkout())
        WebhookEvent.objects.update(estado=WebhookEvent.Estado.PROCESADO)
        resp = self._post_webhook(_checkout())
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data['duplicado'])
        self.assertEqual(WebhookEvent.objects.count(), 1)
        mock_delay.assert_called_once()

    @patch('integraciones.tasks.procesar_evento_pms.apply_async')
    def test_reenvio_de_evento_atascado_en_recibido_se_reencola(self, mock_delay):
        self._post_webhook(_checkout())
        resp = self._post_webhook(_checkout())
        self.assertEqual(resp.status_code, status.HTTP_202_ACCEPTED)
        self.assertTrue(resp.data['duplicado'])
        self.assertEqual(mock_delay.call_count, 2)
        self.assertEqual(WebhookEvent.objects.count(), 1)

    @patch('integraciones.tasks.procesar_evento_pms.apply_async')
    def test_reenvio_de_evento_en_error_se_reprocesa(self, mock_delay):
        mock_delay.side_effect = Exception('Redis caído')  # fuerza procesamiento en línea
        resp = self._post_webhook(_checkout(property_id='SIN-ENLAZAR'))
        self.assertEqual(resp.data['estado'], WebhookEvent.Estado.ERROR)
        # El admin enlaza la propiedad y el PMS reenvía el mismo evento
        self.prop.external_id = 'SIN-ENLAZAR'
        self.prop.save()
        resp = self._post_webhook(_checkout(property_id='SIN-ENLAZAR'))
        self.assertEqual(resp.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(resp.data['estado'], WebhookEvent.Estado.PROCESADO)
        self.assertEqual(Inspeccion.objects.filter(propiedad=self.prop).count(), 1)

    @patch('integraciones.tasks.procesar_evento_pms.apply_async')
    def test_mismo_event_id_en_otro_tenant_no_colisiona(self, _delay):
        Propiedad.objects.create(owner=self.admin2, nombre='Otra', direccion='C2', external_id='LISTING-42')
        _, clave2 = ApiKey.generar(self.admin2, 'PMS2')
        self._post_webhook(_checkout())
        resp = self._post_webhook(_checkout(), clave=clave2)
        self.assertEqual(resp.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(WebhookEvent.objects.count(), 2)

    @patch('integraciones.tasks.procesar_evento_pms.apply_async')
    def test_checkout_sin_property_id_devuelve_400(self, mock_delay):
        payload = {'event_id': 'e', 'event_type': 'reservation.checkout', 'data': {}}
        resp = self._post_webhook(payload)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(WebhookEvent.objects.count(), 0)
        mock_delay.assert_not_called()

    @patch('integraciones.tasks.procesar_evento_pms.apply_async')
    def test_sin_event_id_devuelve_400(self, _delay):
        resp = self._post_webhook({'event_type': 'reservation.checkout', 'data': {'property_id': 'x'}})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    @patch('integraciones.tasks.procesar_evento_pms.apply_async', side_effect=Exception('Redis caído'))
    def test_sin_broker_procesa_en_linea(self, _delay):
        resp = self._post_webhook(_checkout())
        self.assertEqual(resp.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(resp.data['estado'], WebhookEvent.Estado.PROCESADO)
        self.assertEqual(Inspeccion.objects.filter(propiedad=self.prop).count(), 1)

    @patch('integraciones.views.procesar_evento', side_effect=Exception('BD bloqueada'))
    @patch('integraciones.tasks.procesar_evento_pms.apply_async', side_effect=Exception('Redis caído'))
    def test_fallo_en_linea_marca_error_y_responde_202(self, _delay, _procesar):
        resp = self._post_webhook(_checkout())
        self.assertEqual(resp.status_code, status.HTTP_202_ACCEPTED)
        evento = WebhookEvent.objects.get()
        self.assertEqual(evento.estado, WebhookEvent.Estado.ERROR)
        self.assertIn('BD bloqueada', evento.error)

    @patch('integraciones.tasks.procesar_evento_pms.apply_async')
    def test_property_id_con_espacios_se_normaliza(self, _delay):
        self._post_webhook(_checkout(property_id='  LISTING-42 \n'))
        evento = WebhookEvent.objects.get()
        self.assertEqual(evento.payload['data']['property_id'], 'LISTING-42')
        self.assertEqual(procesar_evento(evento.pk).estado, WebhookEvent.Estado.PROCESADO)

    @patch('integraciones.tasks.procesar_evento_pms.apply_async')
    def test_property_id_numerico_se_acepta(self, _delay):
        self.assertEqual(self._post_webhook(_checkout(property_id=42)).status_code, 202)
        self.assertEqual(WebhookEvent.objects.get().payload['data']['property_id'], '42')

    @patch('integraciones.tasks.procesar_evento_pms.apply_async')
    def test_property_id_de_tipo_invalido_devuelve_400(self, _delay):
        for valor in ({'id': 42}, [1], True, '   '):
            with self.subTest(valor=valor):
                resp = self._post_webhook(_checkout(event_id=f'evt-{valor!r}', property_id=valor))
                self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(WebhookEvent.objects.count(), 0)

    @patch('integraciones.tasks.procesar_evento_pms.apply_async')
    def test_throttle_limita_peticiones(self, _delay):
        from rest_framework.settings import api_settings
        from rest_framework.throttling import ScopedRateThrottle
        rates = {**api_settings.DEFAULT_THROTTLE_RATES, 'webhooks': '2/min'}
        with patch.object(ScopedRateThrottle, 'THROTTLE_RATES', rates):
            self._post_webhook(_checkout('a'))
            self._post_webhook(_checkout('b'))
            resp = self._post_webhook(_checkout('c'))
        self.assertEqual(resp.status_code, status.HTTP_429_TOO_MANY_REQUESTS)


class ProcesarEventoTest(BaseIntegracionTest):
    def _evento(self, tipo='reservation.checkout', property_id='LISTING-42', owner=None):
        return WebhookEvent.objects.create(
            owner=owner or self.admin,
            event_id=f'evt-{WebhookEvent.objects.count()}',
            tipo=tipo,
            payload={'data': {'property_id': property_id}},
        )

    def test_checkout_crea_inspeccion_pendiente_sin_operario(self):
        evento = procesar_evento(self._evento().pk)
        self.assertEqual(evento.estado, WebhookEvent.Estado.PROCESADO)
        self.assertIsNotNone(evento.procesado)
        self.assertEqual(evento.inspeccion.propiedad, self.prop)
        self.assertIsNone(evento.inspeccion.operario)
        self.assertEqual(evento.inspeccion.estado, Inspeccion.Estado.PENDIENTE)

    def test_propiedad_desconocida_marca_error(self):
        evento = procesar_evento(self._evento(property_id='NO-EXISTE').pk)
        self.assertEqual(evento.estado, WebhookEvent.Estado.ERROR)
        self.assertIn('NO-EXISTE', evento.error)
        self.assertEqual(Inspeccion.objects.count(), 0)

    def test_no_usa_propiedad_de_otro_tenant(self):
        # admin2 envía el external_id de una propiedad de admin1: no debe tocarla
        evento = procesar_evento(self._evento(owner=self.admin2).pk)
        self.assertEqual(evento.estado, WebhookEvent.Estado.ERROR)
        self.assertEqual(Inspeccion.objects.count(), 0)

    def test_tipo_no_soportado_se_ignora(self):
        evento = procesar_evento(self._evento(tipo='reservation.created').pk)
        self.assertEqual(evento.estado, WebhookEvent.Estado.IGNORADO)
        self.assertEqual(Inspeccion.objects.count(), 0)

    def test_reprocesar_no_duplica_inspeccion(self):
        evento = self._evento()
        procesar_evento(evento.pk)
        procesar_evento(evento.pk)
        self.assertEqual(Inspeccion.objects.count(), 1)

    @patch('integraciones.services.procesar_evento', side_effect=Exception('BD caída'))
    def test_tarea_sin_reintentos_restantes_marca_error(self, _procesar):
        from .tasks import procesar_evento_pms
        evento = self._evento()
        procesar_evento_pms.apply(args=[evento.pk], retries=procesar_evento_pms.max_retries)
        evento.refresh_from_db()
        self.assertEqual(evento.estado, WebhookEvent.Estado.ERROR)
        self.assertIn('BD caída', evento.error)

    def test_tarea_celery_procesa_evento(self):
        from .tasks import procesar_evento_pms
        evento = self._evento()
        procesar_evento_pms.apply(args=[evento.pk])
        evento.refresh_from_db()
        self.assertEqual(evento.estado, WebhookEvent.Estado.PROCESADO)


class ApiKeyGestionTest(BaseIntegracionTest):
    def test_admin_crea_clave_y_la_ve_una_sola_vez(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.post(API_KEYS_URL, {'nombre': 'Hostaway'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertTrue(resp.data['clave'].startswith('ck_'))
        listado = self.client.get(API_KEYS_URL)
        self.assertEqual(len(listado.data), 2)
        for item in listado.data:
            self.assertNotIn('clave', item)
            self.assertNotIn('hash_sha256', item)

    def test_clave_creada_por_api_funciona_en_webhook(self):
        self.client.force_authenticate(user=self.admin)
        clave = self.client.post(API_KEYS_URL, {'nombre': 'X'}, format='json').data['clave']
        self.client.force_authenticate(user=None)
        with patch('integraciones.tasks.procesar_evento_pms.apply_async'):
            self.assertEqual(self._post_webhook(_checkout(), clave=clave).status_code, 202)

    def test_listado_solo_muestra_claves_propias(self):
        ApiKey.generar(self.admin2, 'Ajena')
        self.client.force_authenticate(user=self.admin)
        nombres = [k['nombre'] for k in self.client.get(API_KEYS_URL).data]
        self.assertEqual(nombres, ['Guesty'])

    def test_operario_no_gestiona_claves(self):
        self.client.force_authenticate(user=self.operario)
        self.assertEqual(self.client.get(API_KEYS_URL).status_code, 403)
        self.assertEqual(self.client.post(API_KEYS_URL, {'nombre': 'x'}, format='json').status_code, 403)

    def test_sin_nombre_devuelve_400(self):
        self.client.force_authenticate(user=self.admin)
        self.assertEqual(self.client.post(API_KEYS_URL, {}, format='json').status_code, 400)

    def test_revocar_clave_propia(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.delete(f'{API_KEYS_URL}{self.api_key.pk}/')
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.api_key.refresh_from_db()
        self.assertTrue(self.api_key.revocada)

    def test_revocar_clave_ajena_devuelve_404(self):
        self.client.force_authenticate(user=self.admin2)
        resp = self.client.delete(f'{API_KEYS_URL}{self.api_key.pk}/')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
        self.api_key.refresh_from_db()
        self.assertFalse(self.api_key.revocada)

    def test_eventos_solo_del_tenant(self):
        WebhookEvent.objects.create(owner=self.admin, event_id='mio', tipo='t')
        WebhookEvent.objects.create(owner=self.admin2, event_id='ajeno', tipo='t')
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get(EVENTOS_URL)
        self.assertEqual([e['event_id'] for e in resp.data], ['mio'])

    def test_operario_no_ve_eventos(self):
        self.client.force_authenticate(user=self.operario)
        self.assertEqual(self.client.get(EVENTOS_URL).status_code, 403)


# --- Webhooks salientes ---

import hashlib  # noqa: E402
import hmac  # noqa: E402
import io  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import socket  # noqa: E402
from datetime import timedelta  # noqa: E402
from uuid import uuid4  # noqa: E402

from celery.exceptions import SoftTimeLimitExceeded  # noqa: E402
from django.test import override_settings  # noqa: E402
from django.utils import timezone  # noqa: E402
from urllib3.response import HTTPResponse  # noqa: E402

from .models import EntregaWebhook, WebhookDestino  # noqa: E402
from .salientes import (  # noqa: E402
    UrlNoPermitida,
    _sesion,
    entregar,
    reencolar_atascadas,
    validar_esquema_url,
    validar_url_destino,
)

DESTINOS_URL = '/api/v1/integraciones/destinos/'
ENTREGAS_URL = '/api/v1/integraciones/entregas/'
IP_PUBLICA = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443))]
IP_PRIVADA = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('10.0.0.1', 443))]
URLOPEN = 'urllib3.connectionpool.HTTPConnectionPool.urlopen'


def _respuestas(*codigos):
    """side_effect para urlopen: devuelve los códigos en orden (o lanza si es excepción)."""
    pendientes = list(codigos)

    def _urlopen(pool, method, url, *args, **kwargs):
        siguiente = pendientes.pop(0) if len(pendientes) > 1 else pendientes[0]
        if isinstance(siguiente, BaseException) or (isinstance(siguiente, type) and issubclass(siguiente, BaseException)):
            raise siguiente
        return HTTPResponse(body=io.BytesIO(b'x' * 10), status=siguiente, headers={}, preload_content=False)
    return _urlopen


class BaseSalientesTest(BaseIntegracionTest):
    def setUp(self):
        super().setUp()
        self.destino = WebhookDestino.objects.create(owner=self.admin, url='https://pms.example.com/hook')
        self.inspeccion = Inspeccion.objects.create(propiedad=self.prop)

    def _completar(self, inspeccion=None):
        inspeccion = inspeccion or self.inspeccion
        inspeccion.estado = Inspeccion.Estado.COMPLETADA
        with self.captureOnCommitCallbacks(execute=True):
            inspeccion.save()


@patch('integraciones.salientes.socket.getaddrinfo', return_value=IP_PUBLICA)
class EntregaWebhookTest(BaseSalientesTest):
    def _entrega(self):
        with patch('integraciones.tasks.entregar_webhook.apply_async'):
            self._completar()
        return EntregaWebhook.objects.get()

    def test_firma_cabeceras_y_ip_fijada(self, _dns):
        entrega = self._entrega()
        with patch(URLOPEN, autospec=True, side_effect=_respuestas(200)) as mock_urlopen:
            self.assertEqual(entregar(entrega.pk), 'ENTREGADA')

        pool = mock_urlopen.call_args[0][0]
        kwargs = mock_urlopen.call_args[1]
        self.assertEqual(kwargs['method'], 'POST')
        # Conexión fijada a la IP validada, con SNI y verificación del hostname original
        self.assertEqual(pool.host, '93.184.216.34')
        self.assertEqual(pool.conn_kw.get('server_hostname'), 'pms.example.com')
        self.assertEqual(pool.assert_hostname, 'pms.example.com')
        self.assertFalse(kwargs['preload_content'])  # stream=True
        self.assertFalse(kwargs['redirect'])
        self.assertEqual(kwargs['timeout'].connect_timeout, 3)
        self.assertEqual(kwargs['timeout'].read_timeout, 5)

        cabeceras = kwargs['headers']
        cuerpo = kwargs['body']
        esperada = hmac.new(self.destino.secreto.encode(), cuerpo, hashlib.sha256).hexdigest()
        self.assertEqual(cabeceras['X-CheckIt-Signature'], f'sha256={esperada}')
        self.assertEqual(cabeceras['X-CheckIt-Event'], 'inspeccion.completada')
        self.assertEqual(cabeceras['X-CheckIt-Delivery'], str(entrega.entrega_id))
        self.assertEqual(cabeceras['Host'], 'pms.example.com')
        self.assertEqual(cabeceras['Connection'], 'close')

        payload = json.loads(cuerpo)
        self.assertEqual(payload['tipo'], 'inspeccion.completada')
        self.assertEqual(payload['event_id'], str(entrega.event_id))
        self.assertEqual(payload['data']['inspeccion_id'], self.inspeccion.pk)
        self.assertEqual(payload['data']['propiedad_id'], self.prop.pk)
        self.assertEqual(payload['data']['external_id'], 'LISTING-42')
        self.assertIn('fecha', payload)

        entrega.refresh_from_db()
        self.assertEqual(entrega.estado, EntregaWebhook.Estado.ENTREGADA)
        self.assertEqual(entrega.ultimo_codigo, 200)
        self.assertEqual(entrega.intentos, 1)
        self.assertIsNotNone(entrega.entregada)

    def test_proxy_de_entorno_ignorado(self, _dns):
        self.assertFalse(_sesion().trust_env)
        entrega = self._entrega()
        with patch.dict(os.environ, {'HTTPS_PROXY': 'http://10.0.0.1:3128', 'https_proxy': 'http://10.0.0.1:3128'}), \
                patch(URLOPEN, autospec=True, side_effect=_respuestas(200)) as mock_urlopen:
            entregar(entrega.pk)
        self.assertEqual(mock_urlopen.call_args[0][0].host, '93.184.216.34')

    def test_5xx_reintenta_hasta_el_maximo(self, _dns):
        from .tasks import entregar_webhook
        entrega = self._entrega()
        with patch(URLOPEN, autospec=True, side_effect=_respuestas(503)) as mock_urlopen:
            entregar_webhook.apply(args=[entrega.pk])  # en modo eager los reintentos se encadenan
        entrega.refresh_from_db()
        self.assertEqual(mock_urlopen.call_count, entregar_webhook.max_retries + 1)
        self.assertEqual(entrega.intentos, entregar_webhook.max_retries + 1)
        self.assertEqual(entrega.estado, EntregaWebhook.Estado.FALLIDA)
        self.assertEqual(entrega.ultimo_codigo, 503)

    def test_5xx_y_luego_exito(self, _dns):
        from .tasks import entregar_webhook
        entrega = self._entrega()
        with patch(URLOPEN, autospec=True, side_effect=_respuestas(500, 201)) as mock_urlopen:
            entregar_webhook.apply(args=[entrega.pk])
        entrega.refresh_from_db()
        self.assertEqual(mock_urlopen.call_count, 2)
        self.assertEqual(entrega.estado, EntregaWebhook.Estado.ENTREGADA)

    def test_backoff_exponencial(self, _dns):
        from celery.exceptions import Retry
        from .tasks import entregar_webhook
        entrega = self._entrega()
        with patch(URLOPEN, autospec=True, side_effect=_respuestas(502)), \
                patch.object(entregar_webhook, 'retry', side_effect=Retry()) as mock_retry:
            entregar_webhook.apply(args=[entrega.pk], retries=2)
        self.assertEqual(mock_retry.call_args[1]['countdown'], 30 * 4)

    def test_error_de_red_reintenta_sin_guardar_la_url(self, _dns):
        from celery.exceptions import Retry
        from .tasks import entregar_webhook
        entrega = self._entrega()
        with patch(URLOPEN, autospec=True, side_effect=_respuestas(OSError('pms.example.com caído'), 200)), \
                patch.object(entregar_webhook, 'retry', side_effect=Retry()):
            entregar_webhook.apply(args=[entrega.pk])
        entrega.refresh_from_db()
        self.assertEqual(entrega.estado, EntregaWebhook.Estado.PENDIENTE)
        self.assertEqual(entrega.error, 'Error de red: ConnectionError')
        self.assertNotIn('example.com', entrega.error)
        with patch(URLOPEN, autospec=True, side_effect=_respuestas(200)):
            entregar_webhook.apply(args=[entrega.pk])
        entrega.refresh_from_db()
        self.assertEqual(entrega.estado, EntregaWebhook.Estado.ENTREGADA)
        self.assertEqual(entrega.intentos, 2)

    def test_soft_time_limit_se_reintenta(self, _dns):
        entrega = self._entrega()
        with patch(URLOPEN, autospec=True, side_effect=_respuestas(SoftTimeLimitExceeded())):
            self.assertEqual(entregar(entrega.pk), 'REINTENTAR')
        entrega.refresh_from_db()
        self.assertEqual(entrega.estado, EntregaWebhook.Estado.PENDIENTE)
        self.assertEqual(entrega.intentos, 1)
        self.assertIn('Tiempo límite', entrega.error)

    def test_4xx_no_reintenta(self, _dns):
        from .tasks import entregar_webhook
        entrega = self._entrega()
        with patch(URLOPEN, autospec=True, side_effect=_respuestas(410)) as mock_urlopen:
            entregar_webhook.apply(args=[entrega.pk])
        entrega.refresh_from_db()
        self.assertEqual(mock_urlopen.call_count, 1)
        self.assertEqual(entrega.estado, EntregaWebhook.Estado.FALLIDA)
        self.assertEqual(entrega.ultimo_codigo, 410)

    def test_ssrf_dns_a_ip_privada_bloqueado(self, mock_dns):
        mock_dns.return_value = IP_PRIVADA
        entrega = self._entrega()
        with patch(URLOPEN, autospec=True) as mock_urlopen:
            self.assertEqual(entregar(entrega.pk), 'FALLIDA')
        mock_urlopen.assert_not_called()
        entrega.refresh_from_db()
        self.assertIn('URL bloqueada', entrega.error)

    def test_dns_rebinding_bloqueado_al_conectar(self, mock_dns):
        # Primera resolución (validación previa) pública; la de la conexión, privada
        mock_dns.side_effect = [IP_PUBLICA, IP_PRIVADA]
        entrega = self._entrega()
        with patch(URLOPEN, autospec=True) as mock_urlopen:
            self.assertEqual(entregar(entrega.pk), 'FALLIDA')
        mock_urlopen.assert_not_called()
        self.assertEqual(mock_dns.call_count, 2)
        entrega.refresh_from_db()
        self.assertEqual(entrega.estado, EntregaWebhook.Estado.FALLIDA)
        self.assertIn('10.0.0.1', entrega.error)

    def test_url_con_backslash_guardada_se_bloquea_al_entregar(self, _dns):
        entrega = self._entrega()
        WebhookDestino.objects.filter(pk=self.destino.pk).update(url='https://127.0.0.1\\@example.com/')
        with patch(URLOPEN, autospec=True) as mock_urlopen:
            self.assertEqual(entregar(entrega.pk), 'FALLIDA')
        mock_urlopen.assert_not_called()

    def test_destino_desactivado_no_envia(self, _dns):
        entrega = self._entrega()
        WebhookDestino.objects.filter(pk=self.destino.pk).update(activo=False)
        with patch(URLOPEN, autospec=True) as mock_urlopen:
            self.assertEqual(entregar(entrega.pk), 'FALLIDA')
        mock_urlopen.assert_not_called()

    def test_entrega_ya_cerrada_no_se_reenvia(self, _dns):
        entrega = self._entrega()
        with patch(URLOPEN, autospec=True, side_effect=_respuestas(200)) as mock_urlopen:
            entregar(entrega.pk)
            entregar(entrega.pk)
        self.assertEqual(mock_urlopen.call_count, 1)

    def test_resultado_tardio_no_pisa_una_entregada(self, _dns):
        entrega = self._entrega()

        def _otro_worker_entrega(pool, *args, **kwargs):
            EntregaWebhook.objects.filter(pk=entrega.pk).update(estado=EntregaWebhook.Estado.ENTREGADA)
            return HTTPResponse(body=io.BytesIO(b''), status=500, preload_content=False)

        with patch(URLOPEN, autospec=True, side_effect=_otro_worker_entrega):
            entregar(entrega.pk)
        entrega.refresh_from_db()
        self.assertEqual(entrega.estado, EntregaWebhook.Estado.ENTREGADA)

    def test_reserva_perdida_no_envia(self, _dns):
        from django.db.models.query import QuerySet
        entrega = self._entrega()
        first_original = QuerySet.first

        def _first_y_otro_worker_reserva(qs):
            # Otro worker reserva la entrega justo después de nuestra lectura
            obj = first_original(qs)
            EntregaWebhook.objects.filter(pk=entrega.pk).update(
                actualizada=timezone.now() + timedelta(seconds=1), intentos=1)
            return obj

        with patch.object(QuerySet, 'first', _first_y_otro_worker_reserva), \
                patch(URLOPEN, autospec=True) as mock_urlopen:
            entregar(entrega.pk)
        mock_urlopen.assert_not_called()
        entrega.refresh_from_db()
        self.assertEqual(entrega.intentos, 1)


class SsrfTest(TestCase):
    def test_ips_no_permitidas(self):
        for ip in ('127.0.0.1', '10.1.2.3', '192.168.1.1', '172.16.0.1', '169.254.169.254', '::1', '0.0.0.0',
                   '::ffff:127.0.0.1', '100.64.0.1', '64:ff9b::a00:1', '64:ff9b::808:808', '2002:a00:1::1',
                   '2001:0:4136:e378:8000:63bf:3fff:fdd2', 'fe80::1', 'fc00::1'):
            with self.subTest(ip=ip):
                host = f'[{ip}]' if ':' in ip else ip
                with self.assertRaises(UrlNoPermitida):
                    validar_url_destino(f'https://{host}/hook')

    def test_diferencias_de_parser_y_userinfo(self):
        for url in (
            'https://127.0.0.1\\@example.com/',
            'https://169.254.169.254\\@x.com/',
            'https://good.com:443@127.0.0.1/',
            'https://a@10.0.0.1/',
            'https://user:pass@pms.example.com/',
            'https://pms.example.com /hook',
            'https://pms.example.com/\nhook',
            'https://pms.exa\tmple.com/',
        ):
            with self.subTest(url=url), self.assertRaises(UrlNoPermitida):
                validar_esquema_url(url)

    def test_http_y_localhost_rechazados(self):
        for url in ('http://pms.example.com/hook', 'https://localhost/hook', 'ftp://pms.example.com/'):
            with self.subTest(url=url), self.assertRaises(UrlNoPermitida):
                validar_url_destino(url)

    @override_settings(DEBUG=True)
    def test_debug_no_relaja_la_proteccion(self):
        with self.assertRaises(UrlNoPermitida):
            validar_url_destino('http://127.0.0.1/hook')

    @patch('integraciones.salientes.PERMITIR_LOCAL', True)
    def test_permitir_local_admite_http_y_local(self):
        validar_url_destino('http://localhost:9000/hook')
        validar_url_destino('http://127.0.0.1/hook')

    @patch('integraciones.salientes.socket.getaddrinfo', return_value=IP_PUBLICA + IP_PRIVADA)
    def test_basta_una_ip_privada_entre_varias(self, _dns):
        with self.assertRaises(UrlNoPermitida):
            validar_url_destino('https://pms.example.com/hook')


@patch('integraciones.tasks.entregar_webhook.apply_async')
class SenalesSalientesTest(BaseSalientesTest):
    def test_completar_emite_y_encola_al_confirmar(self, mock_encolar):
        self._completar()
        entrega = EntregaWebhook.objects.get()
        self.assertEqual(entrega.evento, 'inspeccion.completada')
        self.assertEqual(entrega.destino, self.destino)
        self.assertEqual(entrega.inspeccion, self.inspeccion)
        mock_encolar.assert_called_once_with(args=[entrega.pk], retry=False)

    def test_no_encola_hasta_confirmar_transaccion(self, mock_encolar):
        self.inspeccion.estado = Inspeccion.Estado.COMPLETADA
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            self.inspeccion.save()
        mock_encolar.assert_not_called()
        self.assertEqual(len(callbacks), 1)

    def test_solo_dispara_en_la_transicion(self, mock_encolar):
        self._completar()
        # Guardados posteriores ya en COMPLETADA no reemiten
        with self.captureOnCommitCallbacks(execute=True):
            self.inspeccion.save()
            self.inspeccion.operario = self.operario
            self.inspeccion.save(update_fields=['operario'])
        self.assertEqual(EntregaWebhook.objects.count(), 1)
        self.assertEqual(mock_encolar.call_count, 1)

    def test_otros_cambios_de_estado_no_emiten(self, mock_encolar):
        self.inspeccion.estado = Inspeccion.Estado.EN_PROGRESO
        with self.captureOnCommitCallbacks(execute=True):
            self.inspeccion.save()
        self.assertEqual(EntregaWebhook.objects.count(), 0)

    def test_pdf_listo_emite_una_vez(self, mock_encolar):
        self.inspeccion.pdf_estado = Inspeccion.PdfEstado.GENERANDO
        self.inspeccion.save(update_fields=['pdf_estado'])
        self.inspeccion.pdf_estado = Inspeccion.PdfEstado.LISTO
        self.inspeccion.pdf_ruta = 'tmp/x.pdf'
        with self.captureOnCommitCallbacks(execute=True):
            self.inspeccion.save(update_fields=['pdf_estado', 'pdf_ruta'])
            self.inspeccion.save(update_fields=['pdf_estado', 'pdf_ruta'])
        entrega = EntregaWebhook.objects.get()
        self.assertEqual(entrega.evento, 'pdf.listo')
        self.assertNotIn('pdf_url', entrega.payload['data'])

    def test_respeta_eventos_suscritos_y_destinos_inactivos(self, mock_encolar):
        self.destino.eventos = ['pdf.listo']
        self.destino.save()
        WebhookDestino.objects.create(owner=self.admin, url='https://b.example.com', activo=False)
        self._completar()
        self.assertEqual(EntregaWebhook.objects.count(), 0)

    def test_varios_destinos_comparten_event_id(self, mock_encolar):
        WebhookDestino.objects.create(owner=self.admin, url='https://b.example.com')
        self._completar()
        entregas = list(EntregaWebhook.objects.all())
        self.assertEqual(len(entregas), 2)
        self.assertEqual(entregas[0].event_id, entregas[1].event_id)
        self.assertNotEqual(entregas[0].entrega_id, entregas[1].entrega_id)

    def test_no_notifica_a_destinos_de_otro_tenant(self, mock_encolar):
        ajeno = WebhookDestino.objects.create(owner=self.admin2, url='https://ajeno.example.com')
        self._completar()
        self.assertFalse(EntregaWebhook.objects.filter(destino=ajeno).exists())
        self.assertEqual(EntregaWebhook.objects.count(), 1)

    def test_fallo_al_emitir_no_rompe_el_guardado(self, mock_encolar):
        with patch('integraciones.salientes.emitir_evento', side_effect=Exception('boom')):
            self._completar()
        self.inspeccion.refresh_from_db()
        self.assertEqual(self.inspeccion.estado, Inspeccion.Estado.COMPLETADA)

    def test_sin_broker_queda_pendiente(self, mock_encolar):
        mock_encolar.side_effect = Exception('Redis caído')
        self._completar()
        self.assertEqual(EntregaWebhook.objects.get().estado, EntregaWebhook.Estado.PENDIENTE)

    def test_completar_via_api_emite(self, mock_encolar):
        self.inspeccion.operario = self.operario
        self.inspeccion.save()
        self.client.force_authenticate(user=self.operario)
        with self.captureOnCommitCallbacks(execute=True):
            resp = self.client.patch(
                f'/api/v1/inspecciones/{self.inspeccion.pk}/', {'estado': 'COMPLETADA'}, format='json'
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(EntregaWebhook.objects.count(), 1)
        mock_encolar.assert_called_once()


@patch('integraciones.tasks.entregar_webhook.apply_async')
class AtascadasTest(BaseSalientesTest):
    def _entrega(self, hace_minutos, estado=EntregaWebhook.Estado.PENDIENTE):
        entrega = EntregaWebhook.objects.create(destino=self.destino, evento='pdf.listo', event_id=uuid4(),
                                                estado=estado)
        EntregaWebhook.objects.filter(pk=entrega.pk).update(
            actualizada=timezone.now() - timedelta(minutes=hace_minutos))
        return entrega

    def test_tarea_periodica_reencola_solo_las_atascadas(self, mock_encolar):
        from .tasks import reencolar_entregas_atascadas
        vieja = self._entrega(30)
        self._entrega(2)
        self._entrega(30, estado=EntregaWebhook.Estado.FALLIDA)
        self.assertEqual(reencolar_entregas_atascadas.apply().get(), 1)
        mock_encolar.assert_called_once_with(args=[vieja.pk], retry=False)
        # Ya reclamada: una segunda pasada inmediata no la duplica
        self.assertEqual(reencolar_atascadas(), 0)

    def test_reintento_manual_de_pendiente(self, mock_encolar):
        reciente = self._entrega(2)
        vieja = self._entrega(30)
        self.client.force_authenticate(user=self.admin)
        self.assertEqual(self.client.post(f'{ENTREGAS_URL}{reciente.pk}/reintentar/').status_code, 409)
        with self.captureOnCommitCallbacks(execute=True):
            resp = self.client.post(f'{ENTREGAS_URL}{vieja.pk}/reintentar/')
        self.assertEqual(resp.status_code, status.HTTP_202_ACCEPTED)
        mock_encolar.assert_called_once_with(args=[vieja.pk], retry=False)


class WebhookDestinoApiTest(BaseIntegracionTest):
    def test_crear_muestra_secreto_una_sola_vez(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.post(DESTINOS_URL, {'url': 'https://pms.example.com/hook'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertTrue(resp.data['secreto'].startswith('whsec_'))
        self.assertEqual(resp.data['eventos'], ['inspeccion.completada', 'pdf.listo'])
        destino = WebhookDestino.objects.get()
        self.assertEqual(destino.owner, self.admin)
        self.assertEqual(destino.secreto, resp.data['secreto'])
        self.assertEqual(resp.data['secreto_prefijo'], f'whsec_…{destino.secreto[-4:]}')
        for r in (self.client.get(DESTINOS_URL).data[0], self.client.get(f'{DESTINOS_URL}{destino.pk}/').data):
            self.assertNotIn('secreto', r)
            self.assertNotIn(destino.secreto, json.dumps(r))

    def test_validaciones_de_url_y_eventos(self):
        self.client.force_authenticate(user=self.admin)
        for datos in (
            {'url': 'http://pms.example.com/hook'},
            {'url': 'https://127.0.0.1/hook'},
            {'url': 'https://169.254.169.254/latest'},
            {'url': 'https://localhost/hook'},
            {'url': 'https://127.0.0.1\\@example.com/'},
            {'url': 'https://good.com:443@127.0.0.1/'},
            {'url': 'https://a@10.0.0.1/'},
            {'url': 'https://pms.example.com', 'eventos': []},
            {'url': 'https://pms.example.com', 'eventos': ['otro.evento']},
        ):
            with self.subTest(datos=datos):
                resp = self.client.post(DESTINOS_URL, datos, format='json')
                self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(WebhookDestino.objects.count(), 0)

    @patch('integraciones.views.MAX_DESTINOS', 2)
    def test_maximo_de_destinos_activos(self):
        self.client.force_authenticate(user=self.admin)
        for i in range(2):
            self.assertEqual(self.client.post(DESTINOS_URL, {'url': f'https://p{i}.example.com'}, format='json').status_code, 201)
        resp = self.client.post(DESTINOS_URL, {'url': 'https://p3.example.com'}, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('máximo de 2', resp.data['detail'])
        # Inactivos no cuentan, pero reactivar uno sí se comprueba
        resp = self.client.post(DESTINOS_URL, {'url': 'https://p3.example.com', 'activo': False}, format='json')
        self.assertEqual(resp.status_code, 201)
        url = f"{DESTINOS_URL}{resp.data['id']}/"
        self.assertEqual(self.client.patch(url, {'activo': True}, format='json').status_code, 400)
        self.assertEqual(self.client.patch(url, {'nombre': 'x'}, format='json').status_code, 200)
        # Otro tenant tiene su propio cupo
        self.client.force_authenticate(user=self.admin2)
        self.assertEqual(self.client.post(DESTINOS_URL, {'url': 'https://q.example.com'}, format='json').status_code, 201)

    def test_rotar_secreto(self):
        destino = WebhookDestino.objects.create(owner=self.admin, url='https://a.example.com')
        anterior = destino.secreto
        self.client.force_authenticate(user=self.admin)
        resp = self.client.post(f'{DESTINOS_URL}{destino.pk}/rotar-secreto/')
        self.assertEqual(resp.status_code, 200)
        destino.refresh_from_db()
        self.assertNotEqual(destino.secreto, anterior)
        self.assertEqual(resp.data['secreto'], destino.secreto)
        self.assertEqual(resp.data['secreto_prefijo'], f'whsec_…{destino.secreto[-4:]}')
        self.client.force_authenticate(user=self.admin2)
        self.assertEqual(self.client.post(f'{DESTINOS_URL}{destino.pk}/rotar-secreto/').status_code, 404)

    def test_editar_y_borrar_destino_propio(self):
        destino = WebhookDestino.objects.create(owner=self.admin, url='https://a.example.com')
        self.client.force_authenticate(user=self.admin)
        resp = self.client.patch(f'{DESTINOS_URL}{destino.pk}/', {'activo': False, 'eventos': ['pdf.listo']}, format='json')
        self.assertEqual(resp.status_code, 200)
        destino.refresh_from_db()
        self.assertFalse(destino.activo)
        self.assertEqual(destino.eventos, ['pdf.listo'])
        resp = self.client.patch(f'{DESTINOS_URL}{destino.pk}/', {'url': 'https://10.0.0.1/'}, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self.client.delete(f'{DESTINOS_URL}{destino.pk}/').status_code, 204)
        self.assertFalse(WebhookDestino.objects.exists())

    def test_destino_ajeno_devuelve_404(self):
        ajeno = WebhookDestino.objects.create(owner=self.admin2, url='https://ajeno.example.com')
        self.client.force_authenticate(user=self.admin)
        url = f'{DESTINOS_URL}{ajeno.pk}/'
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.patch(url, {'activo': False}, format='json').status_code, 404)
        self.assertEqual(self.client.delete(url).status_code, 404)
        ajeno.refresh_from_db()
        self.assertTrue(ajeno.activo)
        self.assertEqual(self.client.get(DESTINOS_URL).data, [])

    def test_operario_no_gestiona_destinos(self):
        self.client.force_authenticate(user=self.operario)
        self.assertEqual(self.client.get(DESTINOS_URL).status_code, 403)
        self.assertEqual(self.client.get(ENTREGAS_URL).status_code, 403)

    def test_entregas_solo_del_tenant_y_filtros(self):
        mio = WebhookDestino.objects.create(owner=self.admin, url='https://a.example.com')
        ajeno = WebhookDestino.objects.create(owner=self.admin2, url='https://b.example.com')
        e1 = EntregaWebhook.objects.create(destino=mio, evento='pdf.listo', event_id=uuid4())
        EntregaWebhook.objects.create(destino=mio, evento='pdf.listo', event_id=uuid4(),
                                      estado=EntregaWebhook.Estado.FALLIDA)
        e_ajena = EntregaWebhook.objects.create(destino=ajeno, evento='pdf.listo', event_id=uuid4(),
                                                estado=EntregaWebhook.Estado.FALLIDA)
        self.client.force_authenticate(user=self.admin)
        self.assertEqual(len(self.client.get(ENTREGAS_URL).data), 2)
        resp = self.client.get(ENTREGAS_URL, {'estado': 'PENDIENTE'})
        self.assertEqual([e['id'] for e in resp.data], [e1.pk])
        self.assertEqual(self.client.get(ENTREGAS_URL, {'destino': ajeno.pk}).data, [])
        self.assertEqual(self.client.get(ENTREGAS_URL, {'estado': 'X'}).status_code, 400)
        self.assertEqual(self.client.get(ENTREGAS_URL, {'destino': 'x'}).status_code, 400)
        url = f'{ENTREGAS_URL}{e_ajena.pk}/reintentar/'
        self.assertEqual(self.client.post(url).status_code, 404)

    @patch('integraciones.tasks.entregar_webhook.apply_async')
    def test_reintentar_entrega_fallida(self, mock_encolar):
        destino = WebhookDestino.objects.create(owner=self.admin, url='https://a.example.com')
        entrega = EntregaWebhook.objects.create(destino=destino, evento='pdf.listo', event_id=uuid4(),
                                                estado=EntregaWebhook.Estado.FALLIDA)
        self.client.force_authenticate(user=self.admin)
        url = f'{ENTREGAS_URL}{entrega.pk}/reintentar/'
        with self.captureOnCommitCallbacks(execute=True):
            resp = self.client.post(url)
        self.assertEqual(resp.status_code, status.HTTP_202_ACCEPTED)
        entrega.refresh_from_db()
        self.assertEqual(entrega.estado, EntregaWebhook.Estado.PENDIENTE)
        mock_encolar.assert_called_once_with(args=[entrega.pk], retry=False)
        # Ahora es PENDIENTE reciente: no se puede reintentar de nuevo
        self.assertEqual(self.client.post(url).status_code, status.HTTP_409_CONFLICT)
