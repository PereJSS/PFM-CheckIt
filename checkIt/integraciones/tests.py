from unittest.mock import patch

from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
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

    def test_external_id_no_distingue_mayusculas(self):
        # La propiedad se guardó como 'LISTING-42'; el PMS la envía en minúsculas
        evento = procesar_evento(self._evento(property_id='listing-42').pk)
        self.assertEqual(evento.estado, WebhookEvent.Estado.PROCESADO)
        self.assertEqual(evento.inspeccion.propiedad, self.prop)

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

    def test_filtrar_claves_revocadas(self):
        revocada, _ = ApiKey.generar(self.admin, 'Vieja')
        ApiKey.objects.filter(pk=revocada.pk).update(revocada=True)
        self.client.force_authenticate(user=self.admin)
        self.assertEqual([k['nombre'] for k in self.client.get(API_KEYS_URL, {'revocadas': '0'}).data], ['Guesty'])
        self.assertEqual([k['nombre'] for k in self.client.get(API_KEYS_URL, {'revocadas': '1'}).data], ['Vieja'])
        self.assertEqual(len(self.client.get(API_KEYS_URL).data), 2)
        self.assertEqual(self.client.get(API_KEYS_URL, {'revocadas': 'x'}).status_code, 400)

    def test_eliminar_clave_revocada_conserva_sus_eventos(self):
        evento = WebhookEvent.objects.create(owner=self.admin, api_key=self.api_key, event_id='e', tipo='t')
        self.client.force_authenticate(user=self.admin)
        url = f'{API_KEYS_URL}{self.api_key.pk}/eliminar/'
        # Una clave activa no se borra: primero hay que revocarla
        self.assertEqual(self.client.delete(url).status_code, status.HTTP_409_CONFLICT)
        self.client.delete(f'{API_KEYS_URL}{self.api_key.pk}/')
        self.assertEqual(self.client.delete(url).status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(ApiKey.objects.filter(pk=self.api_key.pk).exists())
        evento.refresh_from_db()
        self.assertIsNone(evento.api_key)

    def test_eliminar_clave_ajena_devuelve_404(self):
        ApiKey.objects.filter(pk=self.api_key.pk).update(revocada=True)
        self.client.force_authenticate(user=self.admin2)
        self.assertEqual(self.client.delete(f'{API_KEYS_URL}{self.api_key.pk}/eliminar/').status_code, 404)
        self.assertTrue(ApiKey.objects.filter(pk=self.api_key.pk).exists())

    def test_eventos_solo_del_tenant(self):
        WebhookEvent.objects.create(owner=self.admin, event_id='mio', tipo='t')
        WebhookEvent.objects.create(owner=self.admin2, event_id='ajeno', tipo='t')
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get(EVENTOS_URL)
        self.assertEqual([e['event_id'] for e in resp.data], ['mio'])

    def test_operario_no_ve_eventos(self):
        self.client.force_authenticate(user=self.operario)
        self.assertEqual(self.client.get(EVENTOS_URL).status_code, 403)


class EventosApiTest(BaseIntegracionTest):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(user=self.admin)

    def _evento(self, event_id, **campos):
        campos.setdefault('tipo', 'reservation.checkout')
        return WebhookEvent.objects.create(owner=self.admin, event_id=event_id, **campos)

    def test_filtros_en_servidor(self):
        self._evento('a', estado=WebhookEvent.Estado.ERROR, proveedor='guesty')
        self._evento('b', estado=WebhookEvent.Estado.PROCESADO, proveedor='guesty')
        self._evento('c', estado=WebhookEvent.Estado.ERROR, proveedor='hostaway', tipo='otro')
        ids = lambda **q: [e['event_id'] for e in self.client.get(EVENTOS_URL, q).data]  # noqa: E731
        self.assertEqual(ids(estado='ERROR'), ['c', 'a'])
        self.assertEqual(ids(estado='ERROR', proveedor='guesty'), ['a'])
        self.assertEqual(ids(tipo='otro'), ['c'])
        self.assertEqual(ids(event_id='b'), ['b'])
        self.assertEqual(self.client.get(EVENTOS_URL, {'estado': 'NOPE'}).status_code, 400)

    def test_paginacion_con_antes(self):
        eventos = [self._evento(f'e{n}') for n in range(55)]
        primera = self.client.get(EVENTOS_URL).data
        self.assertEqual(len(primera), 50)
        self.assertEqual(primera[0]['id'], eventos[-1].pk)
        resto = self.client.get(EVENTOS_URL, {'antes': primera[-1]['id']}).data
        self.assertEqual([e['id'] for e in resto], [e.pk for e in reversed(eventos[:5])])
        self.assertEqual(self.client.get(EVENTOS_URL, {'antes': 'x'}).status_code, 400)

    def test_detalle_expone_payload_y_api_key(self):
        evento = self._evento('d', api_key=self.api_key, payload={'data': {'property_id': 'X'}})
        resp = self.client.get(f'{EVENTOS_URL}{evento.pk}/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['payload'], {'data': {'property_id': 'X'}})
        self.assertEqual(resp.data['api_key'], {'id': self.api_key.pk, 'nombre': 'Guesty',
                                                'prefijo': self.api_key.prefijo})
        # El listado sigue siendo ligero
        self.assertNotIn('payload', self.client.get(EVENTOS_URL).data[0])

    def test_detalle_ajeno_404(self):
        ajeno = WebhookEvent.objects.create(owner=self.admin2, event_id='x', tipo='t')
        self.assertEqual(self.client.get(f'{EVENTOS_URL}{ajeno.pk}/').status_code, 404)
        self.assertEqual(self.client.post(f'{EVENTOS_URL}{ajeno.pk}/reprocesar/').status_code, 404)

    @patch('integraciones.tasks.procesar_evento_pms.apply_async')
    def test_reprocesar_evento_en_error(self, mock_encolar):
        evento = self._evento('r', estado=WebhookEvent.Estado.ERROR, error='sin propiedad',
                              payload={'data': {'property_id': 'LISTING-42'}})
        with self.captureOnCommitCallbacks(execute=True):
            resp = self.client.post(f'{EVENTOS_URL}{evento.pk}/reprocesar/')
        self.assertEqual(resp.status_code, status.HTTP_202_ACCEPTED)
        mock_encolar.assert_called_once_with(args=[evento.pk], retry=False)
        evento.refresh_from_db()
        self.assertEqual(evento.estado, WebhookEvent.Estado.RECIBIDO)
        self.assertEqual(evento.error, '')

    @patch('integraciones.tasks.procesar_evento_pms.apply_async')
    def test_reprocesar_evento_resuelto_da_409(self, mock_encolar):
        evento = self._evento('p', estado=WebhookEvent.Estado.PROCESADO)
        self.assertEqual(self.client.post(f'{EVENTOS_URL}{evento.pk}/reprocesar/').status_code, 409)
        mock_encolar.assert_not_called()


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

from .models import EntregaWebhook, EventoSaliente, WebhookDestino  # noqa: E402
from .salientes import (  # noqa: E402
    MAX_INTENTOS_DESPACHO,
    UrlNoPermitida,
    _sesion,
    despachar_evento,
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
        with patch(URLOPEN, autospec=True) as mock_urlopen, \
                self.assertLogs('integraciones.salientes', level='WARNING') as logs:
            self.assertEqual(entregar(entrega.pk), 'FALLIDA')
        mock_urlopen.assert_not_called()
        self.assertEqual(mock_dns.call_count, 2)
        entrega.refresh_from_db()
        self.assertEqual(entrega.estado, EntregaWebhook.Estado.FALLIDA)
        # La IP interna va solo al log del servidor, nunca al error visible por el admin
        self.assertNotIn('10.0.0.1', entrega.error)
        self.assertTrue(any('10.0.0.1' in linea for linea in logs.output))

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
        # El webhook y el sellado del manifiesto Merkle (inspecciones.signals)
        self.assertEqual(len(callbacks), 2)

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
        self.inspeccion.pdf_ruta = default_storage.save('informes/x.pdf', ContentFile(b'%PDF-1.7 x'))
        with self.captureOnCommitCallbacks(execute=True):
            self.inspeccion.save(update_fields=['pdf_estado', 'pdf_ruta'])
            self.inspeccion.save(update_fields=['pdf_estado', 'pdf_ruta'])
        entrega = EntregaWebhook.objects.get()
        self.assertEqual(entrega.evento, 'pdf.listo')
        datos = entrega.payload['data']
        self.assertIn('/api/v1/informes/', datos['pdf_url'])
        self.assertIn('pdf_url_caduca', datos)
        # El enlace funciona sin autenticación
        ruta = datos['pdf_url'].split('/api/v1/', 1)[1]
        resp = self.client.get(f'/api/v1/{ruta}')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(b''.join(resp.streaming_content), b'%PDF-1.7 x')

    def _generar_pdf(self, firmar=None):
        from inspecciones.tasks import generar_pdf_async

        def firmar_por_defecto(entrada, salida):
            open(salida, 'wb').write(b'%PDF-1.7 x')

        with patch('inspecciones.services.pdf_generator.generar_informe_base',
                   side_effect=lambda insp, ruta: open(ruta, 'wb').close() or ruta), \
                patch('inspecciones.services.pdf_signer.firmar_pdf_reclamacion',
                      side_effect=firmar or firmar_por_defecto), \
                self.captureOnCommitCallbacks(execute=True):
            generar_pdf_async.apply(args=[self.inspeccion.pk])

    def test_la_tarea_del_pdf_emite_pdf_listo(self, mock_encolar):
        # Regresión: la tarea marca LISTO con un UPDATE condicional, que no dispara post_save
        self._generar_pdf()
        self.inspeccion.refresh_from_db()
        self.assertEqual(self.inspeccion.pdf_estado, Inspeccion.PdfEstado.LISTO)
        entrega = EntregaWebhook.objects.get()
        self.assertEqual(entrega.evento, 'pdf.listo')
        self.assertEqual(entrega.payload['data']['pdf_estado'], Inspeccion.PdfEstado.LISTO)
        self.assertIn('pdf_url', entrega.payload['data'])
        mock_encolar.assert_called_once_with(args=[entrega.pk], retry=False)

    def test_pdf_descartado_no_emite_pdf_listo(self, mock_encolar):
        def firmar_y_cambiar_raiz(entrada, salida):
            Inspeccion.objects.filter(pk=self.inspeccion.pk).update(manifiesto_raiz='ef' * 32)
            open(salida, 'wb').write(b'%PDF-1.7 x')

        self._generar_pdf(firmar_y_cambiar_raiz)
        self.assertFalse(EntregaWebhook.objects.filter(evento='pdf.listo').exists())

    def test_inspeccion_completada_sin_pdf_url(self, mock_encolar):
        self._completar()
        self.assertNotIn('pdf_url', EntregaWebhook.objects.get().payload['data'])

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
        self.assertEqual(resp.data['eventos'], ['inspeccion.completada', 'pdf.listo', 'manifiesto.sellado'])
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


@patch('integraciones.salientes.socket.getaddrinfo', return_value=IP_PUBLICA)
class AdaptadorIpFijadaTest(TestCase):
    """
    Guarda contra actualizaciones de `requests`: el anti-rebinding depende de que
    HTTPAdapter llame a build_connection_pool_key_attributes al abrir la conexión.
    Si deja de hacerlo, la conexión volvería a resolver el nombre por su cuenta.
    """

    def test_la_conexion_real_usa_la_ip_validada(self, mock_dns):
        with patch(URLOPEN, autospec=True, side_effect=_respuestas(200)) as mock_urlopen:
            with _sesion() as sesion:
                sesion.post('https://pms.example.com/hook', data=b'{}', timeout=1, allow_redirects=False)
        mock_dns.assert_called_once()
        pool = mock_urlopen.call_args.args[0]
        self.assertEqual(pool.host, '93.184.216.34', 'requests ya no llama al hook del adaptador')
        self.assertEqual(pool.conn_kw.get('server_hostname'), 'pms.example.com')
        self.assertEqual(pool.assert_hostname, 'pms.example.com')


class ChecksTest(TestCase):
    def _errores(self):
        from .checks import check_permitir_local
        return check_permitir_local(None)

    @override_settings(DEBUG=False)
    @patch('integraciones.salientes.PERMITIR_LOCAL', True)
    def test_avisa_si_permitir_local_en_produccion(self):
        avisos = self._errores()
        self.assertEqual([a.id for a in avisos], ['integraciones.W001'])

    @override_settings(DEBUG=True)
    @patch('integraciones.salientes.PERMITIR_LOCAL', True)
    def test_sin_aviso_en_desarrollo(self):
        self.assertEqual(self._errores(), [])

    @override_settings(DEBUG=False)
    def test_sin_aviso_por_defecto(self):
        self.assertEqual(self._errores(), [])


class ThrottleGestionTest(BaseIntegracionTest):
    def test_vistas_de_gestion_limitadas(self):
        from rest_framework.settings import api_settings
        from rest_framework.throttling import ScopedRateThrottle
        rates = {**api_settings.DEFAULT_THROTTLE_RATES, 'integraciones': '2/min'}
        self.client.force_authenticate(user=self.admin)
        for url in (API_KEYS_URL, EVENTOS_URL, DESTINOS_URL, ENTREGAS_URL):
            cache.clear()
            with self.subTest(url=url), patch.object(ScopedRateThrottle, 'THROTTLE_RATES', rates):
                self.client.get(url)
                self.client.get(url)
                self.assertEqual(self.client.get(url).status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_scope_configurado(self):
        from rest_framework.settings import api_settings
        self.assertIn('integraciones', api_settings.DEFAULT_THROTTLE_RATES)


class PurgaEntregasTest(BaseSalientesTest):
    def _entrega(self, dias, estado):
        entrega = EntregaWebhook.objects.create(destino=self.destino, evento='pdf.listo', event_id=uuid4(),
                                                estado=estado)
        EntregaWebhook.objects.filter(pk=entrega.pk).update(creada=timezone.now() - timedelta(days=dias))
        return entrega

    def test_borra_solo_cerradas_antiguas(self):
        from .salientes import purgar_entregas_antiguas
        E = EntregaWebhook.Estado
        viejas = [self._entrega(100, E.ENTREGADA), self._entrega(100, E.FALLIDA)]
        quedan = [self._entrega(100, E.PENDIENTE), self._entrega(10, E.ENTREGADA)]
        self.assertEqual(purgar_entregas_antiguas(dias=90, lote=1), 2)
        restantes = set(EntregaWebhook.objects.values_list('pk', flat=True))
        self.assertEqual(restantes, {e.pk for e in quedan})
        self.assertFalse(restantes & {e.pk for e in viejas})

    def test_tarea_periodica_registrada(self):
        from django.conf import settings
        from .tasks import purgar_entregas_webhook
        tareas = {c['task'] for c in settings.CELERY_BEAT_SCHEDULE.values()}
        self.assertIn('integraciones.tasks.purgar_entregas_webhook', tareas)
        self._entrega(200, EntregaWebhook.Estado.ENTREGADA)
        self.assertEqual(purgar_entregas_webhook.apply().get(), 1)


@patch('integraciones.tasks.entregar_webhook.apply_async')
class TestigoRaizTest(BaseSalientesTest):
    """#27: la raíz del manifiesto (y su sello) sale hacia el PMS, fuera del control de CheckIt."""

    SELLO = {
        'token': b'tst', 'url': 'https://tsa.example/tsr', 'cualificada': False, 'verificado': True,
    }

    def _sellar(self):
        from inspecciones.services.manifiesto import sellar_manifiesto
        datos = {**self.SELLO, 'timestamp': timezone.now()}
        with patch('inspecciones.services.manifiesto.solicitar_sello_tiempo', return_value=datos), \
                self.captureOnCommitCallbacks(execute=True):
            return sellar_manifiesto(self.inspeccion.pk)

    def test_completada_lleva_la_raiz_congelada_sin_sello(self, mock_encolar):
        self._completar()
        self.inspeccion.refresh_from_db()
        manifiesto = EntregaWebhook.objects.get().payload['data']['manifiesto']
        self.assertEqual(manifiesto['raiz'], self.inspeccion.manifiesto_raiz)
        self.assertEqual(manifiesto['hojas'], 0)
        self.assertIsNone(manifiesto['tsa_timestamp'])

    def test_sellar_emite_manifiesto_sellado_una_vez(self, mock_encolar):
        self._completar()
        self.assertTrue(self._sellar())
        self.assertTrue(self._sellar())  # ya sellado: no reemite
        entrega = EntregaWebhook.objects.get(evento='manifiesto.sellado')
        self.inspeccion.refresh_from_db()
        manifiesto = entrega.payload['data']['manifiesto']
        self.assertEqual(manifiesto['raiz'], self.inspeccion.manifiesto_raiz)
        self.assertEqual(manifiesto['tsa_timestamp'], self.inspeccion.manifiesto_tsa_timestamp.isoformat())
        self.assertEqual(manifiesto['tsa_url'], 'https://tsa.example/tsr')
        self.assertFalse(manifiesto['tsa_cualificada'])
        self.assertTrue(manifiesto['tsa_verificado'])

    def test_tsa_caida_no_emite(self, mock_encolar):
        from inspecciones.services.manifiesto import sellar_manifiesto
        self._completar()
        with patch('inspecciones.services.manifiesto.solicitar_sello_tiempo', return_value=None), \
                self.captureOnCommitCallbacks(execute=True):
            self.assertFalse(sellar_manifiesto(self.inspeccion.pk))
        self.assertFalse(EntregaWebhook.objects.filter(evento='manifiesto.sellado').exists())

    def test_respeta_la_suscripcion(self, mock_encolar):
        self.destino.eventos = ['pdf.listo']
        self.destino.save()
        self._completar()
        self._sellar()
        self.assertEqual(EntregaWebhook.objects.count(), 0)

    def test_sin_raiz_no_hay_bloque_manifiesto(self, mock_encolar):
        from .salientes import construir_payload
        datos = construir_payload('pdf.listo', self.inspeccion, uuid4())['data']
        self.assertNotIn('manifiesto', datos)

    def test_pdf_listo_dice_que_raiz_describe_el_pdf(self, mock_encolar):
        self._completar()
        self._sellar()
        Inspeccion.objects.filter(pk=self.inspeccion.pk).update(
            pdf_estado=Inspeccion.PdfEstado.GENERANDO, pdf_ruta=default_storage.save('informes/y.pdf', ContentFile(b'%PDF')),
        )
        self.inspeccion.refresh_from_db()
        Inspeccion.objects.filter(pk=self.inspeccion.pk).update(
            pdf_estado=Inspeccion.PdfEstado.LISTO, pdf_manifiesto_raiz=self.inspeccion.manifiesto_raiz,
        )
        from inspecciones.signals import enviar_al_confirmar, pdf_listo
        with self.captureOnCommitCallbacks(execute=True):
            enviar_al_confirmar(pdf_listo, self.inspeccion.pk)
        datos = EntregaWebhook.objects.get(evento='pdf.listo').payload['data']
        self.assertEqual(datos['pdf_manifiesto_raiz'], self.inspeccion.manifiesto_raiz)
        self.assertEqual(datos['manifiesto']['raiz'], self.inspeccion.manifiesto_raiz)

    def test_destino_nuevo_se_suscribe_al_evento(self, mock_encolar):
        self.assertIn('manifiesto.sellado', WebhookDestino.objects.create(
            owner=self.admin, url='https://pms.example.com/otro').eventos)

    def test_sellado_lleva_el_token_para_verificarlo_fuera(self, mock_encolar):
        import base64
        self._completar()
        self._sellar()
        manifiesto = EntregaWebhook.objects.get(evento='manifiesto.sellado').payload['data']['manifiesto']
        self.assertEqual(base64.b64decode(manifiesto['tsa_token']), b'tst')

    def test_reabierta_tras_sellar_no_emite(self, mock_encolar):
        from inspecciones.signals import enviar_al_confirmar, manifiesto_sellado
        self._completar()
        with self.captureOnCommitCallbacks(execute=True):
            enviar_al_confirmar(manifiesto_sellado, self.inspeccion.pk, raiz='ab' * 32)
        self.assertFalse(EntregaWebhook.objects.filter(evento='manifiesto.sellado').exists())

    def test_manifiesto_corrupto_no_tumba_el_webhook(self, mock_encolar):
        self._completar()
        Inspeccion.objects.filter(pk=self.inspeccion.pk).update(manifiesto=['no', 'dict'])
        from .salientes import construir_payload
        manifiesto = construir_payload('pdf.listo', self.inspeccion, uuid4())['data']['manifiesto']
        self.assertIsNone(manifiesto['hojas'])
        self.assertTrue(manifiesto['raiz'])

    def test_fallo_al_avisar_no_marca_el_pdf_como_error(self, mock_encolar):
        # Si avisar falla, el PDF ya guardado como LISTO no debe pasar a ERROR ni regenerarse
        with patch('inspecciones.signals.Inspeccion.objects.select_related', side_effect=RuntimeError('bd caída')):
            SenalesSalientesTest._generar_pdf(self)
        self.inspeccion.refresh_from_db()
        self.assertEqual(self.inspeccion.pdf_estado, Inspeccion.PdfEstado.LISTO)


@patch('integraciones.tasks.entregar_webhook.apply_async')
class OutboxTest(BaseSalientesTest):
    """El evento se registra en la transacción del cambio y se reparte al confirmar:
    si el reparto falla o el proceso muere, queda PENDIENTE para el barrido."""

    def _registrar_sin_despachar(self):
        self.inspeccion.estado = Inspeccion.Estado.COMPLETADA
        with self.captureOnCommitCallbacks(execute=False):
            self.inspeccion.save()
        return EventoSaliente.objects.get()

    def test_completar_registra_el_evento_y_lo_despacha_al_confirmar(self, mock_encolar):
        self._completar()
        evento = EventoSaliente.objects.get()
        self.assertEqual(evento.estado, EventoSaliente.Estado.DESPACHADO)
        self.assertEqual(evento.tipo, 'inspeccion.completada')
        self.assertEqual(evento.owner, self.admin)
        entrega = EntregaWebhook.objects.get()
        self.assertEqual(entrega.evento_saliente, evento)
        self.assertEqual(entrega.event_id, evento.event_id)
        self.assertEqual(entrega.payload['event_id'], str(evento.event_id))
        mock_encolar.assert_called_once_with(args=[entrega.pk], retry=False)

    def test_sin_despachar_el_evento_queda_pendiente(self, mock_encolar):
        evento = self._registrar_sin_despachar()
        self.assertEqual(evento.estado, EventoSaliente.Estado.PENDIENTE)
        self.assertEqual(EntregaWebhook.objects.count(), 0)
        self.assertIsNotNone(evento.datos)

    def test_despachar_dos_veces_no_duplica_entregas(self, mock_encolar):
        evento = self._registrar_sin_despachar()
        with self.captureOnCommitCallbacks(execute=True):
            despachar_evento(evento.pk)
            despachar_evento(evento.pk)
        self.assertEqual(EntregaWebhook.objects.count(), 1)
        self.assertEqual(mock_encolar.call_count, 1)

    def test_una_sola_entrega_por_destino_y_evento(self, mock_encolar):
        from django.db import IntegrityError, transaction
        self._completar()
        entrega = EntregaWebhook.objects.get()
        with self.assertRaises(IntegrityError), transaction.atomic():
            EntregaWebhook.objects.create(
                destino=entrega.destino, evento=entrega.evento, event_id=entrega.event_id,
                evento_saliente=entrega.evento_saliente,
            )

    def test_destino_creado_despues_del_evento_no_lo_recibe(self, mock_encolar):
        evento = self._registrar_sin_despachar()
        EventoSaliente.objects.filter(pk=evento.pk).update(creado=timezone.now() - timedelta(minutes=5))
        WebhookDestino.objects.filter(pk=self.destino.pk).update(creado=timezone.now() - timedelta(days=1))
        WebhookDestino.objects.create(owner=self.admin, url='https://otro.example.com/hook')
        with self.captureOnCommitCallbacks(execute=True):
            despachar_evento(evento.pk)
        self.assertEqual(list(EntregaWebhook.objects.values_list('destino', flat=True)), [self.destino.pk])

    def test_fallo_al_despachar_deja_el_evento_pendiente_con_el_error(self, mock_encolar):
        with patch('integraciones.salientes.construir_datos', side_effect=RuntimeError('boom')), \
                self.assertLogs('integraciones.salientes', level='ERROR'):
            self._completar()
        self.inspeccion.refresh_from_db()
        self.assertEqual(self.inspeccion.estado, Inspeccion.Estado.COMPLETADA)
        evento = EventoSaliente.objects.get()
        self.assertEqual(evento.estado, EventoSaliente.Estado.PENDIENTE)
        self.assertIsNone(evento.datos)
        self.assertEqual(evento.intentos, 1)
        self.assertIn('RuntimeError', evento.error)
        self.assertEqual(EntregaWebhook.objects.count(), 0)
        # Al reintentar, sin el fallo, se reconstruyen los datos y se entrega
        with self.captureOnCommitCallbacks(execute=True):
            despachar_evento(evento.pk)
        self.assertEqual(EntregaWebhook.objects.get().payload['data']['inspeccion_id'], self.inspeccion.pk)

    def test_tras_max_intentos_el_evento_se_descarta(self, mock_encolar):
        evento = self._registrar_sin_despachar()
        EventoSaliente.objects.filter(pk=evento.pk).update(intentos=MAX_INTENTOS_DESPACHO - 1)
        with patch('integraciones.salientes._destinos_suscritos', side_effect=RuntimeError('boom')), \
                self.assertLogs('integraciones.salientes', level='ERROR'):
            despachar_evento(evento.pk)
        evento.refresh_from_db()
        self.assertEqual(evento.estado, EventoSaliente.Estado.DESCARTADO)

    def test_sin_destinos_suscritos_no_se_escribe_en_el_outbox(self, mock_encolar):
        self.destino.eventos = ['pdf.listo']
        self.destino.save()
        self._completar()
        self.assertEqual(EventoSaliente.objects.count(), 0)

    def test_inspeccion_borrada_antes_de_despachar_se_descarta(self, mock_encolar):
        evento = self._registrar_sin_despachar()
        self.inspeccion.delete()
        despachar_evento(evento.pk)
        evento.refresh_from_db()
        self.assertEqual(evento.estado, EventoSaliente.Estado.DESCARTADO)
        self.assertEqual(EntregaWebhook.objects.count(), 0)

    def test_fecha_es_la_del_despacho_y_ocurrido_la_del_evento(self, mock_encolar):
        evento = self._registrar_sin_despachar()
        hace_una_hora = timezone.now() - timedelta(hours=1)
        EventoSaliente.objects.filter(pk=evento.pk).update(creado=hace_una_hora)
        WebhookDestino.objects.filter(pk=self.destino.pk).update(creado=timezone.now() - timedelta(days=1))
        with self.captureOnCommitCallbacks(execute=True):
            despachar_evento(evento.pk)
        payload = EntregaWebhook.objects.get().payload
        from datetime import datetime
        self.assertEqual(datetime.fromisoformat(payload['ocurrido']), hace_una_hora)
        self.assertGreater(datetime.fromisoformat(payload['fecha']), hace_una_hora + timedelta(minutes=59))

    def test_el_enlace_del_pdf_se_firma_al_despachar(self, mock_encolar):
        self.destino.eventos = ['pdf.listo']
        self.destino.save()
        from .salientes import emitir_evento
        with self.captureOnCommitCallbacks(execute=False):
            emitir_evento('pdf.listo', self.inspeccion)
        evento = EventoSaliente.objects.get()
        self.assertNotIn('pdf_url', evento.datos)
        with self.captureOnCommitCallbacks(execute=True):
            despachar_evento(evento.pk)
        self.assertIn('/api/v1/informes/', EntregaWebhook.objects.get().payload['data']['pdf_url'])

    def test_si_no_se_puede_anotar_el_fallo_despachar_no_lanza(self, mock_encolar):
        evento = self._registrar_sin_despachar()
        with patch('integraciones.salientes._destinos_suscritos', side_effect=RuntimeError('boom')), \
                patch('integraciones.salientes._fallo_despacho', side_effect=RuntimeError('bd caída')), \
                self.assertLogs('integraciones.salientes', level='ERROR') as logs:
            self.assertEqual(despachar_evento(evento.pk), [])
        self.assertTrue(any('anotar el fallo' in m for m in logs.output))

    def test_datos_no_serializables_se_guardan_como_null(self, mock_encolar):
        with patch('integraciones.salientes.construir_datos', return_value={'x': object()}), \
                self.assertLogs('integraciones.salientes', level='ERROR'):
            evento = self._registrar_sin_despachar()
        self.assertIsNone(evento.datos)
