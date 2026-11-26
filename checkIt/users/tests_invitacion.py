"""Alta de administradores solo con invitación (B3).

El token en claro solo existe en el enlace que imprime `crear_invitacion`; en la BD queda su
huella sha256. Una invitación falsa, caducada o ya usada da siempre el mismo 400.
"""
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.core.cache import cache
from django.core.management import call_command
from django.db import IntegrityError
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from core.testing import limitar
from .invitaciones import crear, huella
from .models import Invitacion, User
from .serializers import AdminRegisterSerializer

REGISTRO = '/api/v1/auth/register/'
VALIDAR = '/api/v1/auth/invitacion/'
NO_VALIDA = 'Invitación no válida o caducada.'


def alta(token, username='nuevo', email='nuevo@test.com'):
    return {
        'invitacion': token,
        'username': username,
        'email': email,
        'first_name': 'Ana',
        'last_name': 'García',
        'password': 'SecurePass123!',
        'password2': 'SecurePass123!',
    }


class _Base(APITestCase):
    def setUp(self):
        cache.clear()  # los throttles cuentan en caché
        self.addCleanup(cache.clear)


class CrearInvitacionTest(TestCase):
    def test_guarda_solo_la_huella_del_token(self):
        inv, token = crear()
        self.assertEqual(inv.token_hash, huella(token))
        self.assertNotIn(token, inv.token_hash)
        self.assertGreaterEqual(len(token), 40)

    def test_caduca_a_los_7_dias_por_defecto(self):
        inv, _ = crear()
        self.assertAlmostEqual(
            (inv.caduca_en - timezone.now()).total_seconds(), timedelta(days=7).total_seconds(), delta=60,
        )

    def test_dos_invitaciones_tienen_tokens_distintos(self):
        self.assertNotEqual(crear()[1], crear()[1])

    def test_email_se_guarda_en_minusculas(self):
        inv, _ = crear(email='Ana@Empresa.COM')
        self.assertEqual(inv.email, 'ana@empresa.com')


@override_settings(FRONTEND_URL='https://app.ejemplo.test')
class ComandoCrearInvitacionTest(TestCase):
    def _ejecutar(self, *args):
        out = StringIO()
        call_command('crear_invitacion', *args, stdout=out)
        return out.getvalue()

    def test_imprime_el_enlace_con_el_token_en_el_fragmento(self):
        salida = self._ejecutar()
        inv = Invitacion.objects.get()
        enlace = next(l for l in salida.split() if l.startswith('https://'))
        self.assertTrue(enlace.startswith('https://app.ejemplo.test/register#invitacion='))
        token = enlace.split('#invitacion=', 1)[1]
        self.assertEqual(inv.token_hash, huella(token))

    def test_email_y_dias(self):
        self._ejecutar('--email', 'cliente@empresa.com', '--dias', '2')
        inv = Invitacion.objects.get()
        self.assertEqual(inv.email, 'cliente@empresa.com')
        self.assertLess(inv.caduca_en, timezone.now() + timedelta(days=2, minutes=1))

    def test_dias_fuera_de_rango_falla(self):
        from django.core.management.base import CommandError
        for dias in ('0', '31'):
            with self.assertRaises(CommandError):
                self._ejecutar('--dias', dias)
        self.assertFalse(Invitacion.objects.exists())


class RegistroConInvitacionTest(_Base):
    def test_sin_invitacion_da_400_y_no_crea_usuario(self):
        datos = alta('x')
        del datos['invitacion']
        resp = self.client.post(REGISTRO, datos)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('invitacion', resp.data)
        self.assertFalse(User.objects.filter(username='nuevo').exists())

    def test_invitacion_valida_crea_administrador_y_la_gasta(self):
        inv, token = crear()
        resp = self.client.post(REGISTRO, alta(token))
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        user = User.objects.get(username='nuevo')
        self.assertTrue(user.is_admin())
        inv.refresh_from_db()
        self.assertIsNotNone(inv.usada_en)
        self.assertEqual(inv.usada_por, user)

    def test_no_sirve_dos_veces(self):
        _, token = crear()
        self.client.post(REGISTRO, alta(token, 'primero', 'p@test.com'))
        resp = self.client.post(REGISTRO, alta(token, 'segundo', 's@test.com'))
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(User.objects.filter(username='segundo').exists())

    def test_falsa_caducada_y_usada_dan_el_mismo_error(self):
        _, caducada = crear()
        Invitacion.objects.update(caduca_en=timezone.now() - timedelta(seconds=1))
        inv_usada, usada = crear()
        Invitacion.objects.filter(pk=inv_usada.pk).update(usada_en=timezone.now())
        respuestas = [
            self.client.post(REGISTRO, alta(t)) for t in ('no-existe', caducada, usada)
        ]
        for resp in respuestas:
            self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertEqual(resp.data['invitacion'], [NO_VALIDA])
        self.assertFalse(User.objects.filter(username='nuevo').exists())

    def test_nominativa_con_otro_email_da_400_y_sigue_libre(self):
        inv, token = crear(email='ana@empresa.com')
        resp = self.client.post(REGISTRO, alta(token, email='otra@empresa.com'))
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('email', resp.data)
        inv.refresh_from_db()
        self.assertIsNone(inv.usada_en)

    def test_nominativa_sin_email_da_400(self):
        _, token = crear(email='ana@empresa.com')
        resp = self.client.post(REGISTRO, alta(token, email=''))
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('email', resp.data)

    def test_nominativa_acepta_otras_mayusculas(self):
        _, token = crear(email='ana@empresa.com')
        resp = self.client.post(REGISTRO, alta(token, email='Ana@Empresa.com'))
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

    def test_error_de_otro_campo_no_gasta_la_invitacion(self):
        inv, token = crear()
        datos = alta(token)
        datos['password2'] = 'Distinta123!'
        resp = self.client.post(REGISTRO, datos)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        inv.refresh_from_db()
        self.assertIsNone(inv.usada_en)

    def test_sin_invitacion_no_revela_si_el_username_existe(self):
        User.objects.create_user(username='existe', password='Pass1234!')
        ocupado = self.client.post(REGISTRO, alta('falsa', username='existe'))
        libre = self.client.post(REGISTRO, alta('falsa', username='libre'))
        self.assertEqual(ocupado.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(ocupado.data, libre.data)
        self.assertEqual(ocupado.data, {'invitacion': [NO_VALIDA]})

    def test_un_jwt_viejo_no_convierte_el_alta_en_401(self):
        _, token = crear()
        resp = self.client.post(REGISTRO, alta(token), HTTP_AUTHORIZATION='Bearer caducado')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

    def test_respuesta_no_devuelve_el_token(self):
        _, token = crear()
        resp = self.client.post(REGISTRO, alta(token))
        self.assertNotIn(token, resp.content.decode())


class ConsumoAtomicoTest(TestCase):
    def test_gastada_entre_validar_y_guardar_no_crea_usuario(self):
        """Dos altas a la vez con la misma invitación: la segunda pasa validate() pero pierde
        el update condicional."""
        inv, token = crear()
        ser = AdminRegisterSerializer(data=alta(token))
        self.assertTrue(ser.is_valid(), ser.errors)
        Invitacion.objects.filter(pk=inv.pk).update(usada_en=timezone.now())
        from rest_framework.exceptions import ValidationError
        with self.assertRaises(ValidationError) as ctx:
            ser.save()
        self.assertEqual(ctx.exception.detail['invitacion'], [NO_VALIDA])
        self.assertFalse(User.objects.filter(username='nuevo').exists())

    def test_caducada_entre_validar_y_guardar_no_crea_usuario(self):
        inv, token = crear()
        ser = AdminRegisterSerializer(data=alta(token))
        self.assertTrue(ser.is_valid(), ser.errors)
        Invitacion.objects.filter(pk=inv.pk).update(caduca_en=timezone.now() - timedelta(seconds=1))
        from rest_framework.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            ser.save()
        self.assertFalse(User.objects.filter(username='nuevo').exists())

    def test_si_falla_crear_el_usuario_la_invitacion_no_se_gasta(self):
        inv, token = crear()
        ser = AdminRegisterSerializer(data=alta(token))
        self.assertTrue(ser.is_valid(), ser.errors)
        from rest_framework.exceptions import ValidationError
        with patch.object(User.objects, 'create_user', side_effect=IntegrityError('carrera')):
            with self.assertRaises(ValidationError) as ctx:
                ser.save()
        self.assertIn('username', ctx.exception.detail)
        inv.refresh_from_db()
        self.assertIsNone(inv.usada_en)


class ValidarInvitacionTest(_Base):
    def test_valida_devuelve_200_y_email_vacio(self):
        _, token = crear()
        resp = self.client.post(VALIDAR, {'invitacion': token})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data, {'email': ''})

    def test_nominativa_devuelve_su_email(self):
        _, token = crear(email='ana@empresa.com')
        resp = self.client.post(VALIDAR, {'invitacion': token})
        self.assertEqual(resp.data, {'email': 'ana@empresa.com'})

    def test_no_la_gasta(self):
        inv, token = crear()
        self.client.post(VALIDAR, {'invitacion': token})
        inv.refresh_from_db()
        self.assertIsNone(inv.usada_en)

    def test_falsa_caducada_usada_y_vacia_dan_el_mismo_400(self):
        _, caducada = crear()
        Invitacion.objects.update(caduca_en=timezone.now() - timedelta(seconds=1))
        inv_usada, usada = crear()
        Invitacion.objects.filter(pk=inv_usada.pk).update(usada_en=timezone.now())
        for token in ('no-existe', caducada, usada):
            resp = self.client.post(VALIDAR, {'invitacion': token})
            self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertEqual(resp.data['invitacion'], [NO_VALIDA])
        vacia = self.client.post(VALIDAR, {})
        self.assertEqual(vacia.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(vacia.data['invitacion'], [NO_VALIDA])

    def test_un_jwt_viejo_no_convierte_la_validacion_en_401(self):
        _, token = crear()
        resp = self.client.post(VALIDAR, {'invitacion': token}, HTTP_AUTHORIZATION='Bearer caducado')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_get_no_esta_permitido(self):
        """El token nunca viaja en la query: quedaría en el log de acceso de Render."""
        _, token = crear()
        resp = self.client.get(VALIDAR, {'invitacion': token})
        self.assertEqual(resp.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_429_por_encima_del_limite(self):
        with limitar(auth_invitacion='2/min'):
            resps = [self.client.post(VALIDAR, {'invitacion': 'x'}) for _ in range(3)]
        self.assertEqual(resps[2].status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertIn('Retry-After', resps[2])

    def test_limite_separado_del_registro(self):
        with limitar(auth_invitacion='2/min', auth_register='2/min'):
            self.client.post(VALIDAR, {'invitacion': 'x'})
            self.client.post(VALIDAR, {'invitacion': 'x'})
            resp = self.client.post(REGISTRO, {})
        self.assertNotEqual(resp.status_code, status.HTTP_429_TOO_MANY_REQUESTS)


class InvitacionAdminTest(TestCase):
    def setUp(self):
        self.su = User.objects.create_superuser(username='su', password='Pass1234!')
        self.client.force_login(self.su)

    def test_listado_visible_y_sin_alta_desde_el_admin(self):
        crear(email='ana@empresa.com')
        self.assertEqual(self.client.get('/admin/users/invitacion/').status_code, 200)
        self.assertEqual(self.client.get('/admin/users/invitacion/add/').status_code, 403)

    def test_no_muestra_la_huella(self):
        inv, _ = crear()
        resp = self.client.get(f'/admin/users/invitacion/{inv.pk}/change/')
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, inv.token_hash)


class OpenApiInvitacionTest(APITestCase):
    def test_esquema_incluye_la_validacion_y_el_campo_del_registro(self):
        resp = self.client.get('/api/v1/schema/', {'format': 'json'})
        self.assertEqual(resp.status_code, 200)
        esquema = resp.json()
        self.assertIn('post', esquema['paths']['/api/v1/auth/invitacion/'])
        self.assertNotIn('get', esquema['paths']['/api/v1/auth/invitacion/'])
        registro = esquema['components']['schemas']['AdminRegister']
        self.assertIn('invitacion', registro['required'])
