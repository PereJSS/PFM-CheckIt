from django.test import TestCase
from rest_framework.test import APITestCase
from rest_framework import status

from users.models import User
from .models import Propiedad


class PropiedadModelTest(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username='admin', password='Pass1234!', role=User.Role.ADMIN
        )
        self.prop = Propiedad.objects.create(
            owner=self.admin, nombre='Casa Rural', direccion='Calle Falsa 123'
        )

    def test_str_contains_nombre(self):
        self.assertIn('Casa Rural', str(self.prop))

    def test_str_contains_direccion(self):
        self.assertIn('Calle Falsa 123', str(self.prop))


class PropiedadViewSetTest(APITestCase):
    URL = '/api/v1/propiedades/'

    def setUp(self):
        self.admin1 = User.objects.create_user(
            username='admin1', password='Pass1234!', role=User.Role.ADMIN
        )
        self.admin2 = User.objects.create_user(
            username='admin2', password='Pass1234!', role=User.Role.ADMIN
        )
        self.operario = User.objects.create_user(
            username='op1', password='Pass1234!', role=User.Role.OPERARIO
        )
        self.prop1 = Propiedad.objects.create(
            owner=self.admin1, nombre='Prop Admin1', direccion='Calle 1'
        )
        self.prop2 = Propiedad.objects.create(
            owner=self.admin2, nombre='Prop Admin2', direccion='Calle 2'
        )

    # --- Listing ---

    def test_admin_sees_only_own_properties(self):
        self.client.force_authenticate(user=self.admin1)
        response = self.client.get(self.URL)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['nombre'], 'Prop Admin1')

    def test_multitenant_isolation_between_admins(self):
        self.client.force_authenticate(user=self.admin2)
        response = self.client.get(self.URL)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['nombre'], 'Prop Admin2')

    # --- Operarios: el endpoint es solo de administradores ---

    def test_operario_no_puede_listar(self):
        self.client.force_authenticate(user=self.operario)
        response = self.client.get(self.URL)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_operario_no_puede_crear_propiedades(self):
        self.client.force_authenticate(user=self.operario)
        response = self.client.post(
            self.URL, {'nombre': 'Mía', 'direccion': 'Calle 9'}, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(Propiedad.objects.filter(owner=self.operario).exists())

    def test_operario_no_puede_ver_editar_ni_borrar_la_suya(self):
        # Una propiedad cuyo dueño es un operario (las que se crearon antes del 6 nov).
        propia = Propiedad.objects.create(owner=self.operario, nombre='Vieja', direccion='Calle 8')
        self.client.force_authenticate(user=self.operario)
        url = f'{self.URL}{propia.pk}/'
        peticiones = {
            'retrieve': lambda: self.client.get(url),
            'patch': lambda: self.client.patch(url, {'nombre': 'Nueva'}, format='json'),
            'delete': lambda: self.client.delete(url),
        }
        for accion, peticion in peticiones.items():
            with self.subTest(accion=accion):
                self.assertEqual(peticion().status_code, status.HTTP_403_FORBIDDEN)
        propia.refresh_from_db()
        self.assertEqual(propia.nombre, 'Vieja')

    def test_unauthenticated_returns_401(self):
        response = self.client.get(self.URL)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    # --- Create ---

    def test_admin_creates_property_assigned_to_self(self):
        self.client.force_authenticate(user=self.admin1)
        response = self.client.post(self.URL, {'nombre': 'Nueva', 'direccion': 'Av. Test 1'})
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        prop = Propiedad.objects.get(nombre='Nueva')
        self.assertEqual(prop.owner, self.admin1)

    def test_create_missing_required_field_returns_400(self):
        self.client.force_authenticate(user=self.admin1)
        response = self.client.post(self.URL, {'nombre': 'Sin direccion'})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    # --- Retrieve ---

    def test_admin_retrieves_own_property(self):
        self.client.force_authenticate(user=self.admin1)
        response = self.client.get(f'{self.URL}{self.prop1.pk}/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['nombre'], 'Prop Admin1')

    def test_admin_cannot_retrieve_other_admins_property(self):
        self.client.force_authenticate(user=self.admin1)
        response = self.client.get(f'{self.URL}{self.prop2.pk}/')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    # --- Update ---

    def test_admin_updates_own_property(self):
        self.client.force_authenticate(user=self.admin1)
        response = self.client.patch(f'{self.URL}{self.prop1.pk}/', {'nombre': 'Updated'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.prop1.refresh_from_db()
        self.assertEqual(self.prop1.nombre, 'Updated')

    def test_admin_cannot_update_other_admins_property(self):
        self.client.force_authenticate(user=self.admin1)
        response = self.client.patch(f'{self.URL}{self.prop2.pk}/', {'nombre': 'Hack'})
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    # --- Delete ---

    def test_admin_deletes_own_property(self):
        self.client.force_authenticate(user=self.admin1)
        response = self.client.delete(f'{self.URL}{self.prop1.pk}/')
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Propiedad.objects.filter(pk=self.prop1.pk).exists())

    def test_admin_cannot_delete_other_admins_property(self):
        self.client.force_authenticate(user=self.admin1)
        response = self.client.delete(f'{self.URL}{self.prop2.pk}/')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertTrue(Propiedad.objects.filter(pk=self.prop2.pk).exists())


class PropiedadExternalIdTest(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='ext1', password='Pass1234!', role=User.Role.ADMIN)
        self.admin2 = User.objects.create_user(username='ext2', password='Pass1234!', role=User.Role.ADMIN)
        self.client.force_authenticate(user=self.admin)

    def _crear(self, external_id):
        return self.client.post(
            '/api/v1/propiedades/',
            {'nombre': 'P', 'direccion': 'C', 'external_id': external_id},
            format='json',
        )

    def test_external_id_se_guarda(self):
        resp = self._crear('LISTING-1')
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data['external_id'], 'LISTING-1')

    def test_external_id_duplicado_en_mismo_tenant_devuelve_400(self):
        self._crear('LISTING-1')
        self.assertEqual(self._crear('LISTING-1').status_code, 400)

    def test_external_id_duplicado_con_otras_mayusculas_devuelve_400(self):
        # El webhook busca sin distinguir mayúsculas: 'listing-1' y 'LISTING-1' serían ambiguos
        self._crear('LISTING-1')
        self.assertEqual(self._crear('listing-1').status_code, 400)

    def test_carrera_con_otras_mayusculas_devuelve_400(self):
        # El índice único de BD también compara sin distinguir mayúsculas
        from unittest.mock import patch
        from .serializers import PropiedadSerializer
        self._crear('LISTING-1')
        with patch.object(PropiedadSerializer, 'validate_external_id', side_effect=lambda v: v):
            resp = self._crear('listing-1')
        self.assertEqual(resp.status_code, 400)

    def test_external_id_repetido_en_otro_tenant_permitido(self):
        Propiedad.objects.create(owner=self.admin2, nombre='X', direccion='Y', external_id='LISTING-1')
        self.assertEqual(self._crear('LISTING-1').status_code, 201)

    def test_varias_propiedades_sin_external_id(self):
        self.assertEqual(self._crear('').status_code, 201)
        self.assertEqual(self._crear(None).status_code, 201)
        self.assertEqual(Propiedad.objects.filter(owner=self.admin, external_id__isnull=True).count(), 2)

    def test_editar_manteniendo_external_id(self):
        pk = self._crear('LISTING-1').data['id']
        resp = self.client.patch(f'/api/v1/propiedades/{pk}/', {'nombre': 'Nuevo', 'external_id': 'LISTING-1'}, format='json')
        self.assertEqual(resp.status_code, 200)

    def test_carrera_en_external_id_devuelve_400_no_500(self):
        # Simula dos peticiones simultáneas: la validación previa no ve la otra fila
        from unittest.mock import patch
        from .serializers import PropiedadSerializer
        self._crear('LISTING-1')
        with patch.object(PropiedadSerializer, 'validate_external_id', side_effect=lambda v: v):
            resp = self._crear('LISTING-1')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('external_id', resp.data)
