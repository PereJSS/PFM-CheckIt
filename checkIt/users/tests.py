from django.test import TestCase
from rest_framework.test import APITestCase
from rest_framework import status

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
            username='op1', password='Pass1234!', role=User.Role.OPERARIO
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


class OperarioDetailViewTest(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username='admin', password='Pass1234!', role=User.Role.ADMIN
        )
        self.operario = User.objects.create_user(
            username='op1', password='Pass1234!', role=User.Role.OPERARIO
        )

    def _url(self, pk):
        return f'/api/v1/usuarios/operarios/{pk}/'

    def test_admin_deletes_operario(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.delete(self._url(self.operario.pk))
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(User.objects.filter(pk=self.operario.pk).exists())

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
