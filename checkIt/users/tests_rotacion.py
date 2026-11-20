"""Rotación de refresh tokens: cada refresco entrega uno nuevo y anula el anterior,
logout que anula el refresh en el servidor y limpieza diaria de los caducados."""
from datetime import timedelta
from unittest.mock import patch

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken

from core.testing import limitar
from .models import User

LOGIN = '/api/v1/auth/login/'
REFRESH = '/api/v1/auth/refresh/'
LOGOUT = '/api/v1/auth/logout/'


class _Base(APITestCase):
    def setUp(self):
        cache.clear()  # contadores del throttling
        self.addCleanup(cache.clear)
        self.usuario = User.objects.create_user(username='op1', password='Pass1234!', role=User.Role.ADMIN)

    def _login(self):
        resp = self.client.post(LOGIN, {'username': 'op1', 'password': 'Pass1234!'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        return resp.data['refresh']

    def _refrescar(self, token):
        return self.client.post(REFRESH, {'refresh': token}, format='json')


class RotacionTest(_Base):
    def test_el_login_registra_el_refresh_del_usuario(self):
        self._login()
        self.assertEqual(OutstandingToken.objects.filter(user=self.usuario).count(), 1)

    def test_refrescar_entrega_un_refresh_nuevo_y_anula_el_anterior(self):
        viejo = self._login()
        resp = self._refrescar(viejo)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn('access', resp.data)
        nuevo = resp.data['refresh']
        self.assertNotEqual(nuevo, viejo)

        self.assertEqual(self._refrescar(viejo).status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(self._refrescar(nuevo).status_code, status.HTTP_200_OK)

    def test_un_refresh_emitido_antes_del_cambio_sigue_valiendo_una_vez(self):
        # Los refresh repartidos antes del deploy no están registrados: no se echa a nadie
        viejo = self._login()
        OutstandingToken.objects.all().delete()
        resp = self._refrescar(viejo)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(self._refrescar(viejo).status_code, status.HTTP_401_UNAUTHORIZED)

    def test_el_alta_en_la_lista_negra_decide_quien_gana(self):
        # Lógica del árbitro, no concurrencia real: el segundo refresco llega con la comprobación
        # de la lista negra ya pasada (como en una carrera) y el alta existente lo rechaza.
        # En Postgres el perdedor espera al INSERT del ganador sobre la OneToOne única.
        viejo = self._login()
        self.assertEqual(self._refrescar(viejo).status_code, status.HTTP_200_OK)
        with patch.object(RefreshToken, 'check_blacklist', autospec=True, return_value=None), \
                self.assertLogs('users.serializers', 'WARNING') as logs:
            resp = self._refrescar(viejo)
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertNotIn('refresh', resp.data)
        self.assertIn('reutilizado', logs.output[0])
        self.assertNotIn(viejo, logs.output[0])

    def test_si_falla_tras_anular_el_refresh_sigue_valiendo(self):
        # Un 500 a mitad no debe dejar al cliente con un refresh anulado y sin el nuevo
        from users import serializers as modulo

        viejo = self._login()
        original = modulo._registrar
        llamadas = []

        def falla_la_segunda(token, user):
            llamadas.append(1)
            if len(llamadas) == 2:
                raise RuntimeError('BD caída')
            return original(token, user)

        self.client.raise_request_exception = False
        with patch.object(modulo, '_registrar', falla_la_segunda):
            self.assertEqual(self._refrescar(viejo).status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        self.assertFalse(BlacklistedToken.objects.exists())
        self.assertEqual(self._refrescar(viejo).status_code, status.HTTP_200_OK)

    def test_el_dado_de_baja_no_refresca_ni_gasta_el_token(self):
        viejo = self._login()
        User.objects.filter(pk=self.usuario.pk).update(is_active=False)
        self.assertEqual(self._refrescar(viejo).status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertFalse(BlacklistedToken.objects.exists())

    def test_un_access_token_no_sirve_para_refrescar(self):
        acceso = str(RefreshToken.for_user(self.usuario).access_token)
        self.assertEqual(self._refrescar(acceso).status_code, status.HTTP_401_UNAUTHORIZED)


class HuellaTest(_Base):
    def test_no_se_guarda_ningun_refresh_en_claro(self):
        primero = self._login()
        rotado = self._refrescar(primero).data['refresh']
        guardados = list(OutstandingToken.objects.values_list('token', flat=True))
        self.assertEqual(len(guardados), 2)
        for valor in guardados:
            self.assertTrue(valor.startswith('sha256:'), valor)
        self.assertNotIn(primero, guardados)
        self.assertNotIn(rotado, guardados)


class LogoutTest(_Base):
    def test_el_logout_anula_el_refresh(self):
        token = self._login()
        resp = self.client.post(LOGOUT, {'refresh': token}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(self._refrescar(token).status_code, status.HTTP_401_UNAUTHORIZED)

    def test_el_logout_de_un_refresh_rotado_tambien_lo_anula(self):
        rotado = self._refrescar(self._login()).data['refresh']
        self.assertEqual(self.client.post(LOGOUT, {'refresh': rotado}, format='json').status_code, status.HTTP_200_OK)
        self.assertEqual(self._refrescar(rotado).status_code, status.HTTP_401_UNAUTHORIZED)

    def test_token_no_valido_da_401_y_sin_token_400(self):
        self.assertEqual(
            self.client.post(LOGOUT, {'refresh': 'no-es-un-token'}, format='json').status_code,
            status.HTTP_401_UNAUTHORIZED,
        )
        self.assertEqual(self.client.post(LOGOUT, {}, format='json').status_code, status.HTTP_400_BAD_REQUEST)

    def test_logout_dos_veces_da_401_la_segunda(self):
        token = self._login()
        self.client.post(LOGOUT, {'refresh': token}, format='json')
        self.assertEqual(
            self.client.post(LOGOUT, {'refresh': token}, format='json').status_code,
            status.HTTP_401_UNAUTHORIZED,
        )

    def test_logout_tiene_limite_propio(self):
        self.assertIn('auth_logout', settings.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES'])
        with limitar(auth_logout='2/min', auth_refresh='100/min'):
            codigos = [self.client.post(LOGOUT, {'refresh': 'x'}, format='json').status_code for _ in range(3)]
            refresco = self._refrescar('x').status_code
        self.assertEqual(codigos[2], status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertEqual(refresco, status.HTTP_401_UNAUTHORIZED)


class LimpiezaTest(_Base):
    def test_borra_los_caducados_y_conserva_los_vigentes(self):
        from users.tasks import purgar_tokens_caducados

        vigente = self._login()
        OutstandingToken.objects.create(
            user=self.usuario, jti='caducado', token='x',
            expires_at=timezone.now() - timedelta(minutes=1),
        )
        purgar_tokens_caducados()
        self.assertEqual(list(OutstandingToken.objects.values_list('jti', flat=True)),
                         [RefreshToken(vigente)['jti']])

    def test_purga_por_lotes_tambien_los_anulados(self):
        from users import tasks

        caducado = timezone.now() - timedelta(minutes=1)
        for i in range(5):
            fila = OutstandingToken.objects.create(user=self.usuario, jti=f'c{i}', token='x', expires_at=caducado)
            BlacklistedToken.objects.create(token=fila)
        with patch.object(tasks, 'PURGA_LOTE', 2):
            tasks.purgar_tokens_caducados()
        self.assertFalse(OutstandingToken.objects.exists())
        self.assertFalse(BlacklistedToken.objects.exists())

    def test_esta_programada_a_diario(self):
        tareas = {e['task']: e for e in settings.CELERY_BEAT_SCHEDULE.values()}
        self.assertIn('users.tasks.purgar_tokens_caducados', tareas)
        self.assertEqual(tareas['users.tasks.purgar_tokens_caducados']['schedule'], 24 * 3600.0)


class OpenApiAuthTest(APITestCase):
    def test_documenta_429_y_el_refresh_rotado(self):
        esquema = self.client.get('/api/v1/schema/?format=json').json()
        for ruta in ('/api/v1/auth/login/', '/api/v1/auth/refresh/', '/api/v1/auth/logout/'):
            respuestas = esquema['paths'][ruta]['post']['responses']
            self.assertIn('429', respuestas, ruta)
            self.assertIn('Retry-After', respuestas['429'].get('headers', {}), ruta)
        refresco = esquema['components']['schemas']['TokenRefresh']['properties']
        self.assertIn('refresh', refresco)
        login = esquema['components']['schemas']['TokenObtainPair']['properties']
        self.assertTrue(login['access']['readOnly'] and login['refresh']['readOnly'])
