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
