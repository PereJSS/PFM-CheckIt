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

    def test_operario_list_is_empty(self):
        self.client.force_authenticate(user=self.operario)
        response = self.client.get(self.URL)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 0)

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
