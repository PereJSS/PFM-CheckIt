from django.conf import settings
from django.test import TestCase, override_settings
from rest_framework.test import APITestCase
from rest_framework import status

from core.testing import limitar
from .models import User


class UserModelTest(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username='admin1', password='Pass1234!', role=User.Role.ADMIN
        )
        self.operario = User.objects.create_user(
            username='op1', password='Pass1234!', role=User.Role.OPERARIO
        )

    def test_is_admin_true_for_admin_role(self):
        self.assertTrue(self.admin.is_admin())

    def test_is_admin_false_for_operario(self):
        self.assertFalse(self.operario.is_admin())

    def test_is_admin_true_for_superuser(self):
        su = User.objects.create_superuser(username='su', password='Pass1234!')
        self.assertTrue(su.is_admin())

    def test_is_admin_true_for_staff(self):
        staff = User.objects.create_user(
            username='staff', password='Pass1234!', is_staff=True
        )
        self.assertTrue(staff.is_admin())

    def test_is_operator_true_for_operario(self):
        self.assertTrue(self.operario.is_operator())

    def test_is_operator_false_for_admin(self):
        self.assertFalse(self.admin.is_operator())

    def test_role_display_admin(self):
        self.assertEqual(self.admin.role_display(), 'Administrador')

    def test_role_display_operario(self):
        self.assertEqual(self.operario.role_display(), 'Operario')

    def test_str_contains_username(self):
        self.assertIn('admin1', str(self.admin))

    def test_default_role_is_operario(self):
        user = User.objects.create_user(username='plain', password='Pass1234!')
        self.assertEqual(user.role, User.Role.OPERARIO)


class RegisterAdminViewTest(APITestCase):
    URL = '/api/v1/auth/register/'

    def setUp(self):
        from django.core.cache import cache
        cache.clear()  # el throttle de registro cuenta en caché
        self.addCleanup(cache.clear)

    def _payload(self, username='newadmin'):
        return {
            'username': username,
            'email': f'{username}@test.com',
            'first_name': 'Test',
            'last_name': 'User',
            'password': 'SecurePass123!',
            'password2': 'SecurePass123!',
        }

    def test_register_creates_admin_role(self):
        response = self.client.post(self.URL, self._payload())
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        user = User.objects.get(username='newadmin')
        self.assertTrue(user.is_admin())

    def test_register_returns_username_in_response(self):
        response = self.client.post(self.URL, self._payload('alice'))
        self.assertEqual(response.data['username'], 'alice')

    def test_password_mismatch_returns_400(self):
        data = self._payload()
        data['password2'] = 'DifferentPass!'
        response = self.client.post(self.URL, data)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_duplicate_username_returns_400(self):
        self.client.post(self.URL, self._payload('dup'))
        response = self.client.post(self.URL, self._payload('dup'))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_missing_password_returns_400(self):
        response = self.client.post(self.URL, {'username': 'x', 'email': 'x@x.com'})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_weak_password_returns_400(self):
        data = self._payload()
        data['password'] = data['password2'] = '123'
        response = self.client.post(self.URL, data)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class MeViewTest(APITestCase):
    URL = '/api/v1/auth/me/'

    def setUp(self):
        self.admin = User.objects.create_user(
            username='me_user', password='Pass1234!', role=User.Role.ADMIN
        )

    def test_authenticated_returns_user_data(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.get(self.URL)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['username'], 'me_user')
        self.assertEqual(response.data['role'], User.Role.ADMIN)

    def test_unauthenticated_returns_401(self):
        response = self.client.get(self.URL)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class OperariosViewTest(APITestCase):
    URL = '/api/v1/usuarios/operarios/'

    def setUp(self):
        self.admin = User.objects.create_user(
            username='admin', password='Pass1234!', role=User.Role.ADMIN
        )
        self.operario = User.objects.create_user(
            username='op1', password='Pass1234!', role=User.Role.OPERARIO, administrador=self.admin,
        )

    def test_admin_lists_only_operarios(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.get(self.URL)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['username'], 'op1')

    def test_operario_cannot_list(self):
        self.client.force_authenticate(user=self.operario)
        response = self.client.get(self.URL)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_unauthenticated_returns_401(self):
        response = self.client.get(self.URL)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_admin_creates_operario_with_correct_role(self):
        self.client.force_authenticate(user=self.admin)
        data = {
            'username': 'newop',
            'email': 'newop@test.com',
            'first_name': 'New',
            'last_name': 'Op',
            'password': 'SecurePass123!',
        }
        response = self.client.post(self.URL, data)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        new_user = User.objects.get(username='newop')
        self.assertTrue(new_user.is_operator())

    def test_operario_cannot_create(self):
        self.client.force_authenticate(user=self.operario)
        response = self.client.post(self.URL, {})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_create_missing_fields_returns_400(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(self.URL, {'username': 'incomplete'})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class AislamientoOperariosTest(APITestCase):
    """Un administrador (cliente) solo ve, borra y asigna sus propios operarios."""
    URL = '/api/v1/usuarios/operarios/'

    def setUp(self):
        self.admin1 = User.objects.create_user(username='admin1', password='Pass1234!', role=User.Role.ADMIN)
        self.admin2 = User.objects.create_user(username='admin2', password='Pass1234!', role=User.Role.ADMIN)
        self.client.force_authenticate(user=self.admin1)
        respuesta = self.client.post(self.URL, {
            'username': 'op_de_admin1', 'email': 'op1@cliente1.test', 'password': 'SecurePass123!',
        })
        self.assertEqual(respuesta.status_code, status.HTTP_201_CREATED)
        self.op1 = User.objects.get(username='op_de_admin1')

    def test_el_operario_creado_queda_ligado_a_su_administrador(self):
        self.assertEqual(self.op1.administrador, self.admin1)

    def test_el_administrador_no_se_puede_elegir_desde_el_body(self):
        self.client.post(self.URL, {
            'username': 'colado', 'password': 'SecurePass123!', 'administrador': self.admin2.pk,
        })
        self.assertEqual(User.objects.get(username='colado').administrador, self.admin1)

    def test_el_administrador_ve_sus_operarios(self):
        respuesta = self.client.get(self.URL)
        self.assertEqual([o['username'] for o in respuesta.data], ['op_de_admin1'])

    def test_otro_administrador_no_ve_operarios_ajenos(self):
        self.client.force_authenticate(user=self.admin2)
        respuesta = self.client.get(self.URL)
        self.assertEqual(respuesta.status_code, status.HTTP_200_OK)
        self.assertEqual(respuesta.data, [])

    def test_otro_administrador_no_puede_borrar_un_operario_ajeno(self):
        self.client.force_authenticate(user=self.admin2)
        respuesta = self.client.delete(f'{self.URL}{self.op1.pk}/')
        # 404 y no 403: no se revela que el operario existe
        self.assertEqual(respuesta.status_code, status.HTTP_404_NOT_FOUND)
        self.assertTrue(User.objects.filter(pk=self.op1.pk).exists())


class RellenarAdministradorMigracionTest(TestCase):
    """0003: solo se liga el operario cuyas inspecciones son todas de un único administrador."""

    def _rellenar(self):
        import importlib
        from django.apps import apps
        migracion = importlib.import_module('users.migrations.0003_rellenar_administrador_de_operarios')
        migracion.rellenar(apps, None)

    def test_liga_solo_cuando_no_hay_duda(self):
        from inspecciones.models import Inspeccion
        from propiedades.models import Propiedad
        a1 = User.objects.create_user(username='a1', password='x', role=User.Role.ADMIN)
        a2 = User.objects.create_user(username='a2', password='x', role=User.Role.ADMIN)
        p1 = Propiedad.objects.create(owner=a1, nombre='P1', direccion='C1')
        p2 = Propiedad.objects.create(owner=a2, nombre='P2', direccion='C2')
        de_uno = User.objects.create_user(username='de_uno', password='x', role=User.Role.OPERARIO)
        de_dos = User.objects.create_user(username='de_dos', password='x', role=User.Role.OPERARIO)
        sin_nada = User.objects.create_user(username='sin_nada', password='x', role=User.Role.OPERARIO)
        ya_ligado = User.objects.create_user(
            username='ya_ligado', password='x', role=User.Role.OPERARIO, administrador=a2,
        )
        Inspeccion.objects.create(propiedad=p1, operario=de_uno)
        Inspeccion.objects.create(propiedad=p1, operario=de_uno)
        Inspeccion.objects.create(propiedad=p1, operario=de_dos)
        Inspeccion.objects.create(propiedad=p2, operario=de_dos)
        Inspeccion.objects.create(propiedad=p1, operario=ya_ligado)

        self._rellenar()

        administradores = dict(User.objects.filter(role=User.Role.OPERARIO).values_list('username', 'administrador'))
        self.assertEqual(administradores, {
            'de_uno': a1.pk, 'de_dos': None, 'sin_nada': None, 'ya_ligado': a2.pk,
        })

    def test_no_liga_a_un_dueno_que_no_es_administrador(self):
        from inspecciones.models import Inspeccion
        from propiedades.models import Propiedad
        no_admin = User.objects.create_user(username='no_admin', password='x', role=User.Role.OPERARIO)
        propiedad = Propiedad.objects.create(owner=no_admin, nombre='P', direccion='C')
        operario = User.objects.create_user(username='op', password='x', role=User.Role.OPERARIO)
        Inspeccion.objects.create(propiedad=propiedad, operario=operario)
        self._rellenar()
        operario.refresh_from_db()
        self.assertIsNone(operario.administrador)


class OperarioDetailViewTest(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username='admin', password='Pass1234!', role=User.Role.ADMIN
        )
        self.operario = User.objects.create_user(
            username='op1', password='Pass1234!', role=User.Role.OPERARIO, administrador=self.admin,
        )

    def _url(self, pk):
        return f'/api/v1/usuarios/operarios/{pk}/'

    # --- Dar de baja (6 nov): la cuenta se desactiva, no se borra ---

    def test_admin_da_de_baja_al_operario_sin_borrarlo(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.delete(self._url(self.operario.pk))
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.operario.refresh_from_db()
        self.assertFalse(self.operario.is_active)
        self.assertEqual(self.operario.administrador, self.admin)

    def test_sus_inspecciones_conservan_el_operario(self):
        from inspecciones.models import Inspeccion
        from propiedades.models import Propiedad
        prop = Propiedad.objects.create(owner=self.admin, nombre='P', direccion='C')
        insp = Inspeccion.objects.create(propiedad=prop, operario=self.operario)
        self.client.force_authenticate(user=self.admin)
        self.client.delete(self._url(self.operario.pk))
        insp.refresh_from_db()
        self.assertEqual(insp.operario_id, self.operario.pk)

    def test_el_dado_de_baja_no_sale_en_la_lista_y_un_segundo_delete_da_404(self):
        self.client.force_authenticate(user=self.admin)
        self.client.delete(self._url(self.operario.pk))
        lista = self.client.get('/api/v1/usuarios/operarios/')
        self.assertEqual([o['id'] for o in lista.data], [])
        self.assertEqual(self.client.delete(self._url(self.operario.pk)).status_code, status.HTTP_404_NOT_FOUND)

    def test_el_dado_de_baja_no_inicia_sesion_ni_usa_su_token(self):
        login = self.client.post(
            '/api/v1/auth/login/', {'username': 'op1', 'password': 'Pass1234!'}, format='json'
        )
        self.assertEqual(login.status_code, status.HTTP_200_OK)
        acceso, refresco = login.data['access'], login.data['refresh']
        self.client.force_authenticate(user=self.admin)
        self.client.delete(self._url(self.operario.pk))
        self.client.force_authenticate(user=None)

        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {acceso}')
        self.assertEqual(self.client.get('/api/v1/auth/me/').status_code, status.HTTP_401_UNAUTHORIZED)
        self.client.credentials()
        self.assertEqual(
            self.client.post('/api/v1/auth/refresh/', {'refresh': refresco}, format='json').status_code,
            status.HTTP_401_UNAUTHORIZED,
        )
        self.assertEqual(
            self.client.post(
                '/api/v1/auth/login/', {'username': 'op1', 'password': 'Pass1234!'}, format='json'
            ).status_code,
            status.HTTP_401_UNAUTHORIZED,
        )

    def test_un_usuario_activo_sigue_refrescando_su_token(self):
        login = self.client.post(
            '/api/v1/auth/login/', {'username': 'op1', 'password': 'Pass1234!'}, format='json'
        )
        respuesta = self.client.post('/api/v1/auth/refresh/', {'refresh': login.data['refresh']}, format='json')
        self.assertEqual(respuesta.status_code, status.HTTP_200_OK)
        self.assertIn('access', respuesta.data)

    def test_un_refresh_invalido_da_401(self):
        respuesta = self.client.post('/api/v1/auth/refresh/', {'refresh': 'no-es-un-token'}, format='json')
        self.assertEqual(respuesta.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_no_da_de_baja_al_operario_de_otro_cliente(self):
        otro = User.objects.create_user(username='adm_otro', password='x', role=User.Role.ADMIN)
        ajeno = User.objects.create_user(
            username='op_ajeno', password='x', role=User.Role.OPERARIO, administrador=otro
        )
        self.client.force_authenticate(user=self.admin)
        self.assertEqual(self.client.delete(self._url(ajeno.pk)).status_code, status.HTTP_404_NOT_FOUND)
        ajeno.refresh_from_db()
        self.assertTrue(ajeno.is_active)

    def test_operario_cannot_delete(self):
        self.client.force_authenticate(user=self.operario)
        response = self.client.delete(self._url(self.operario.pk))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_delete_nonexistent_returns_404(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.delete(self._url(99999))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_cannot_delete_admin_via_operario_endpoint(self):
        other_admin = User.objects.create_user(
            username='admin2', password='Pass1234!', role=User.Role.ADMIN
        )
        self.client.force_authenticate(user=self.admin)
        response = self.client.delete(self._url(other_admin.pk))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertTrue(User.objects.filter(pk=other_admin.pk).exists())


class AuthThrottleTest(APITestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()  # el throttle guarda contadores en caché
        self.addCleanup(cache.clear)
        User.objects.create_user(username='u1', password='Pass1234!', role=User.Role.ADMIN)

    def _login(self, username='u1', **extra):
        return self.client.post(
            '/api/v1/auth/login/', {'username': username, 'password': 'mala'}, **extra
        )

    def test_login_returns_429_over_rate(self):
        with limitar(auth_login='2/min'):
            resps = [self._login() for _ in range(3)]
        self.assertEqual([r.status_code for r in resps[:2]], [status.HTTP_401_UNAUTHORIZED] * 2)
        self.assertEqual(resps[2].status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertIn('Retry-After', resps[2])

    def test_register_returns_429_over_rate(self):
        with limitar(auth_register='2/min'):
            resps = [self.client.post('/api/v1/auth/register/', {}) for _ in range(3)]
        self.assertNotIn(status.HTTP_429_TOO_MANY_REQUESTS, [r.status_code for r in resps[:2]])
        self.assertEqual(resps[2].status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertIn('Retry-After', resps[2])

    def test_login_and_register_have_separate_buckets(self):
        with limitar(auth_login='2/min', auth_register='2/min'):
            self._login()
            self._login()
            resp = self.client.post('/api/v1/auth/register/', {})
        self.assertNotEqual(resp.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    @override_settings(REST_FRAMEWORK={**settings.REST_FRAMEWORK, 'NUM_PROXIES': 1})
    def test_spoofed_xff_does_not_bypass_ip_throttle(self):
        with limitar(auth_login='2/min', auth_usuario='100/min'):
            codes = [
                self._login(username='u%d' % i, HTTP_X_FORWARDED_FOR='9.9.9.%d, 10.0.0.1' % i).status_code
                for i in range(3)
            ]
        self.assertEqual(codes[2], status.HTTP_429_TOO_MANY_REQUESTS)

    def test_username_throttle_ignores_ip_and_case(self):
        with limitar(auth_login='100/min', auth_usuario='2/min'):
            codes = [
                self._login(username=name, HTTP_X_FORWARDED_FOR='1.1.1.%d' % i).status_code
                for i, name in enumerate(['u1', 'U1', ' u1 '])
            ]
        self.assertEqual(codes[:2], [status.HTTP_401_UNAUTHORIZED] * 2)
        self.assertEqual(codes[2], status.HTTP_429_TOO_MANY_REQUESTS)

    def test_username_throttle_does_not_affect_other_users(self):
        with limitar(auth_login='100/min', auth_usuario='1/min'):
            self._login('u1')
            resp = self._login('otro')
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)


class OperariosHuerfanosCommandTest(TestCase):
    """`manage.py operarios_huerfanos`: solo lee; lista operarios sin administrador y
    propiedades cuyo dueño es un operario, con la orden sugerida para arreglarlos."""

    def setUp(self):
        from propiedades.models import Propiedad
        self.Propiedad = Propiedad
        self.admin_a = User.objects.create_user(username='admin_a', password='x', role=User.Role.ADMIN)
        self.admin_b = User.objects.create_user(username='admin_b', password='x', role=User.Role.ADMIN)
        self.prop_a = Propiedad.objects.create(owner=self.admin_a, nombre='Piso A', direccion='Calle A')
        self.prop_b = Propiedad.objects.create(owner=self.admin_b, nombre='Piso B', direccion='Calle B')

    def _operario(self, username, administrador=None):
        return User.objects.create_user(
            username=username, password='x', role=User.Role.OPERARIO, administrador=administrador
        )

    def _inspeccion(self, propiedad, operario):
        from inspecciones.models import Inspeccion
        return Inspeccion.objects.create(propiedad=propiedad, operario=operario)

    def _ejecutar(self):
        from io import StringIO
        from django.core.management import call_command
        salida = StringIO()
        call_command('operarios_huerfanos', stdout=salida)
        return salida.getvalue()

    def test_sin_datos_dice_sin_huerfanos(self):
        self._operario('ligado', administrador=self.admin_a)
        salida = self._ejecutar()
        self.assertIn('Sin huérfanos', salida)
        self.assertNotIn('ligado', salida)

    def test_huerfano_con_un_solo_dueno_sugiere_ese_administrador(self):
        op = self._operario('op_uno')
        i1 = self._inspeccion(self.prop_a, op)
        i2 = self._inspeccion(self.prop_a, op)
        salida = self._ejecutar()
        self.assertIn(f'op_uno (#{op.pk})', salida)
        self.assertIn(f'#{i1.pk}', salida)
        self.assertIn(f'#{i2.pk}', salida)
        self.assertIn(
            f'User.objects.filter(pk={op.pk}).update(administrador_id={self.admin_a.pk})', salida
        )
        self.assertNotIn('Sin huérfanos', salida)

    def test_huerfano_con_varios_duenos_pide_revisar_a_mano(self):
        op = self._operario('op_varios')
        self._inspeccion(self.prop_a, op)
        self._inspeccion(self.prop_b, op)
        salida = self._ejecutar()
        self.assertIn('op_varios', salida)
        self.assertIn('varios dueños', salida)
        self.assertIn('admin_a', salida)
        self.assertIn('admin_b', salida)
        self.assertNotIn(f'User.objects.filter(pk={op.pk})', salida)

    def test_huerfano_sin_inspecciones_sugiere_desactivar_o_asignar(self):
        op = self._operario('op_vacio')
        salida = self._ejecutar()
        self.assertIn('op_vacio', salida)
        self.assertIn('sin inspecciones', salida)
        self.assertNotIn(f'User.objects.filter(pk={op.pk})', salida)

    def test_operario_con_administrador_no_aparece(self):
        op = self._operario('op_ligado', administrador=self.admin_a)
        self._inspeccion(self.prop_a, op)
        self.assertNotIn('op_ligado', self._ejecutar())

    def test_propiedad_de_un_operario_sale_con_su_candidato(self):
        op = self._operario('op_dueno', administrador=self.admin_b)
        prop = self.Propiedad.objects.create(owner=op, nombre='Piso del operario', direccion='Calle C')
        insp = self._inspeccion(prop, op)
        salida = self._ejecutar()
        self.assertIn(f'Piso del operario (#{prop.pk})', salida)
        self.assertIn(f'#{insp.pk}', salida)
        self.assertIn(
            f'Propiedad.objects.filter(pk={prop.pk}).update(owner_id={self.admin_b.pk})', salida
        )

    def test_propiedad_de_operario_huerfano_no_tiene_candidato(self):
        op = self._operario('op_solo')
        prop = self.Propiedad.objects.create(owner=op, nombre='Piso suelto', direccion='Calle D')
        salida = self._ejecutar()
        self.assertIn('Piso suelto', salida)
        self.assertNotIn(f'Propiedad.objects.filter(pk={prop.pk})', salida)

    def test_no_muestra_emails_ni_direcciones(self):
        op = User.objects.create_user(
            username='op_mail', password='x', role=User.Role.OPERARIO, email='secreto@example.com'
        )
        self.Propiedad.objects.create(owner=op, nombre='Piso X', direccion='Calle Secreta 7')
        salida = self._ejecutar()
        self.assertNotIn('secreto@example.com', salida)
        self.assertNotIn('Calle Secreta 7', salida)

    def test_no_modifica_nada(self):
        op = self._operario('op_uno')
        self._inspeccion(self.prop_a, op)
        self.Propiedad.objects.create(owner=op, nombre='Piso Y', direccion='Calle Y')

        def huella():
            return (
                list(User.objects.order_by('pk').values_list('pk', 'administrador_id', 'is_active', 'role')),
                list(self.Propiedad.objects.order_by('pk').values_list('pk', 'owner_id')),
            )

        antes = huella()
        self._ejecutar()
        self.assertEqual(huella(), antes)

    def test_inspeccion_en_propiedad_sin_dueno_pide_revisar_a_mano(self):
        op = self._operario('op_sin_dueno')
        suelta = self.Propiedad.objects.create(owner=None, nombre='Piso sin dueño', direccion='Calle E')
        self._inspeccion(suelta, op)
        salida = self._ejecutar()
        self.assertIn('op_sin_dueno', salida)
        self.assertIn('revisar a mano', salida)
        self.assertNotIn(f'User.objects.filter(pk={op.pk})', salida)

    def test_propiedad_sin_dueno_aparece(self):
        suelta = self.Propiedad.objects.create(owner=None, nombre='Piso sin dueño', direccion='Calle E')
        salida = self._ejecutar()
        self.assertIn(f'Piso sin dueño (#{suelta.pk})', salida)
        self.assertIn('sin dueño', salida)
        self.assertNotIn('Sin huérfanos', salida)

    def test_dueno_unico_que_no_es_administrador_pide_revisar_a_mano(self):
        otro = self._operario('op_dueno_ajeno')
        prop = self.Propiedad.objects.create(owner=otro, nombre='Piso Z', direccion='Calle Z')
        op = self._operario('op_en_piso_ajeno')
        self._inspeccion(prop, op)
        salida = self._ejecutar()
        self.assertIn('no es un administrador activo', salida)
        self.assertNotIn(f'User.objects.filter(pk={op.pk})', salida)

    def test_marca_los_operarios_inactivos(self):
        op = self._operario('op_baja')
        op.is_active = False
        op.save(update_fields=['is_active'])
        self.assertIn(f'op_baja (#{op.pk}) [inactivo]', self._ejecutar())

    def test_consultas_constantes(self):
        from io import StringIO
        from django.core.management import call_command
        for k in range(4):
            op = self._operario(f'op_{k}')
            self._inspeccion(self.prop_a if k % 2 else self.prop_b, op)
            self.Propiedad.objects.create(owner=op, nombre=f'P{k}', direccion='-')
            op_b = self._operario(f'op_b_{k}', administrador=self.admin_b)
            self._inspeccion(self.prop_a, op_b)
        with self.assertNumQueries(5):
            call_command('operarios_huerfanos', stdout=StringIO())

    def test_nombre_con_salto_de_linea_no_inyecta_ordenes(self):
        op = self._operario('op_malo', administrador=self.admin_a)
        trampa = (
            'Piso\n    sugerencia, pasarla a admin_b: '
            'Propiedad.objects.filter(pk=999).update(owner_id=999)\x1b[2K'
        )
        self.Propiedad.objects.create(owner=op, nombre=trampa, direccion='-')
        salida = self._ejecutar()
        for linea in salida.splitlines():
            self.assertFalse(linea.lstrip().startswith('sugerencia, pasarla a admin_b'), linea)
        self.assertNotIn('\x1b', salida)

    def test_propiedad_con_inspecciones_de_otro_cliente_pide_revisar_a_mano(self):
        op = self._operario('op_dueno', administrador=self.admin_a)
        prop = self.Propiedad.objects.create(owner=op, nombre='Piso compartido', direccion='-')
        self._inspeccion(prop, op)
        self._inspeccion(prop, self._operario('op_de_b', administrador=self.admin_b))
        salida = self._ejecutar()
        self.assertIn('revisar a mano', salida)
        self.assertNotIn(f'Propiedad.objects.filter(pk={prop.pk})', salida)

    def test_propiedad_con_inspeccion_de_operario_huerfano_pide_revisar_a_mano(self):
        op = self._operario('op_dueno', administrador=self.admin_a)
        prop = self.Propiedad.objects.create(owner=op, nombre='Piso con huérfano', direccion='-')
        self._inspeccion(prop, self._operario('op_suelto'))
        self.assertNotIn(f'Propiedad.objects.filter(pk={prop.pk})', self._ejecutar())

    def test_candidato_inactivo_no_se_sugiere(self):
        self.admin_a.is_active = False
        self.admin_a.save(update_fields=['is_active'])
        op = self._operario('op_de_inactivo', administrador=self.admin_a)
        prop = self.Propiedad.objects.create(owner=op, nombre='Piso I', direccion='-')
        huerfano = self._operario('op_huerfano')
        self._inspeccion(self.prop_a, huerfano)
        salida = self._ejecutar()
        self.assertNotIn(f'Propiedad.objects.filter(pk={prop.pk})', salida)
        self.assertNotIn(f'User.objects.filter(pk={huerfano.pk})', salida)
        self.assertIn('inactivo', salida)

    def test_sugerencia_de_propiedad_indica_el_valor_anterior(self):
        op = self._operario('op_dueno', administrador=self.admin_b)
        self.Propiedad.objects.create(owner=op, nombre='Piso R', direccion='-')
        self.assertIn(f'(antes owner_id={op.pk})', self._ejecutar())

    def test_inspeccion_con_operario_de_otro_cliente_aparece(self):
        op_b = self._operario('op_de_b', administrador=self.admin_b)
        cruzada = self._inspeccion(self.prop_a, op_b)
        salida = self._ejecutar()
        self.assertIn('Inspecciones con un operario de otro cliente:', salida)
        self.assertIn(f'#{cruzada.pk} en Piso A (#{self.prop_a.pk}) de admin_a', salida)
        self.assertIn(f'op_de_b (#{op_b.pk}), de admin_b', salida)
        self.assertIn('Reasignarla en /admin/ de Django a un operario de admin_a', salida)
        self.assertNotIn('Sin huérfanos', salida)

    def test_inspeccion_con_un_administrador_como_operario_aparece(self):
        cruzada = self._inspeccion(self.prop_a, self.admin_b)
        self.assertIn(f'#{cruzada.pk} en Piso A', self._ejecutar())

    def test_inspeccion_bien_asignada_no_aparece_como_cruzada(self):
        self._inspeccion(self.prop_a, self._operario('op_de_a', administrador=self.admin_a))
        salida = self._ejecutar()
        self.assertNotIn('otro cliente:', salida)
        self.assertIn('Sin huérfanos', salida)

    def test_inspeccion_de_operario_huerfano_no_se_repite_como_cruzada(self):
        self._inspeccion(self.prop_a, self._operario('op_suelto'))
        self.assertNotIn('Inspecciones con un operario de otro cliente', self._ejecutar())


class AdministradorSoloEnOperariosTest(TestCase):
    """`administrador` solo lo tiene un operario, nunca él mismo, y apunta a un administrador."""

    def setUp(self):
        self.admin = User.objects.create_user(username='adm', password='x', role=User.Role.ADMIN)
        self.op = User.objects.create_user(
            username='op', password='x', role=User.Role.OPERARIO, administrador=self.admin
        )

    def _rechaza_bd(self, pk, **cambios):
        from django.db import IntegrityError, transaction
        with self.assertRaises(IntegrityError), transaction.atomic():
            User.objects.filter(pk=pk).update(**cambios)

    def _errores_administrador(self, usuario):
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError) as ctx:
            usuario.full_clean()
        return ctx.exception.message_dict['administrador']

    def test_bd_rechaza_un_administrador_con_administrador(self):
        otro = User.objects.create_user(username='adm2', password='x', role=User.Role.ADMIN)
        self._rechaza_bd(pk=otro.pk, administrador=self.admin)

    def test_bd_rechaza_un_operario_que_se_apunta_a_si_mismo(self):
        self._rechaza_bd(pk=self.op.pk, administrador=self.op)

    def test_bd_rechaza_quitar_el_rol_de_operario_si_tiene_administrador(self):
        self._rechaza_bd(pk=self.op.pk, role=User.Role.ADMIN)

    def test_clean_rechaza_apuntar_a_otro_operario_con_un_mensaje_claro(self):
        otro = User.objects.create_user(username='op2', password='x', role=User.Role.OPERARIO)
        self.op.administrador = otro
        self.assertEqual(
            self._errores_administrador(self.op), ['El administrador debe ser un usuario administrador.']
        )

    def test_clean_rechaza_apuntarse_a_si_mismo_con_un_solo_mensaje(self):
        self.op.administrador = self.op
        self.assertEqual(
            self._errores_administrador(self.op), ['Un operario no puede ser su propio administrador.']
        )

    def test_borrar_un_administrador_suelta_a_sus_operarios(self):
        self.admin.delete()
        self.op.refresh_from_db()
        self.assertIsNone(self.op.administrador)

    def test_clean_rechaza_un_administrador_con_administrador(self):
        otro = User.objects.create_user(username='adm2', password='x', role=User.Role.ADMIN)
        otro.administrador = self.admin
        self.assertEqual(self._errores_administrador(otro), ['Solo un operario tiene administrador.'])

    def test_lo_valido_sigue_pasando(self):
        self.op.full_clean()
        suelto = User.objects.create_user(username='op_suelto', password='x', role=User.Role.OPERARIO)
        suelto.full_clean()
        self.admin.full_clean()
