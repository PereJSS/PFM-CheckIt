import os
import tempfile
from io import BytesIO
from unittest.mock import patch, MagicMock

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image
from rest_framework import status
from rest_framework.test import APITestCase

from propiedades.models import Propiedad
from users.models import User
from .models import Evidencia, Inspeccion


def _make_image(name='test.jpg'):
    buf = BytesIO()
    Image.new('RGB', (100, 100), color=(100, 150, 200)).save(buf, format='JPEG')
    buf.seek(0)
    return SimpleUploadedFile(name, buf.read(), content_type='image/jpeg')


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------

class InspeccionModelTest(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username='admin', password='Pass1234!', role=User.Role.ADMIN
        )
        self.operario = User.objects.create_user(
            username='op', password='Pass1234!', role=User.Role.OPERARIO
        )
        self.prop = Propiedad.objects.create(
            owner=self.admin, nombre='Prop', direccion='Calle 1'
        )
        self.inspeccion = Inspeccion.objects.create(
            propiedad=self.prop, operario=self.operario
        )

    def test_default_estado_is_pendiente(self):
        self.assertEqual(self.inspeccion.estado, Inspeccion.Estado.PENDIENTE)

    def test_estado_display_pendiente(self):
        self.assertEqual(self.inspeccion.estado_display(), 'Pendiente')

    def test_str_contains_inspeccion(self):
        self.assertIn('Inspección', str(self.inspeccion))


class EvidenciaModelTest(TestCase):
    def setUp(self):
        admin = User.objects.create_user(
            username='admin', password='Pass1234!', role=User.Role.ADMIN
        )
        op = User.objects.create_user(
            username='op', password='Pass1234!', role=User.Role.OPERARIO
        )
        prop = Propiedad.objects.create(owner=admin, nombre='P', direccion='C')
        self.inspeccion = Inspeccion.objects.create(propiedad=prop, operario=op)
        self.evidencia = Evidencia.objects.create(
            inspeccion=self.inspeccion,
            foto=SimpleUploadedFile('t.jpg', b'img', content_type='image/jpeg'),
        )

    def test_str_contains_evidencia_text(self):
        self.assertIn('Evidencia', str(self.evidencia))


# ---------------------------------------------------------------------------
# InspeccionViewSet tests
# ---------------------------------------------------------------------------

class InspeccionViewSetTest(APITestCase):
    URL = '/api/v1/inspecciones/'

    def setUp(self):
        self.admin1 = User.objects.create_user(
            username='admin1', password='Pass1234!', role=User.Role.ADMIN
        )
        self.admin2 = User.objects.create_user(
            username='admin2', password='Pass1234!', role=User.Role.ADMIN
        )
        self.op1 = User.objects.create_user(
            username='op1', password='Pass1234!', role=User.Role.OPERARIO
        )
        self.op2 = User.objects.create_user(
            username='op2', password='Pass1234!', role=User.Role.OPERARIO
        )
        self.prop1 = Propiedad.objects.create(
            owner=self.admin1, nombre='Prop1', direccion='C1'
        )
        self.prop2 = Propiedad.objects.create(
            owner=self.admin2, nombre='Prop2', direccion='C2'
        )
        self.insp1 = Inspeccion.objects.create(propiedad=self.prop1, operario=self.op1)
        self.insp2 = Inspeccion.objects.create(propiedad=self.prop2, operario=self.op2)

    # --- Create ---

    def test_admin_creates_inspection(self):
        self.client.force_authenticate(user=self.admin1)
        response = self.client.post(self.URL, {
            'propiedad': self.prop1.pk, 'operario': self.op1.pk
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_operario_cannot_create_inspection(self):
        self.client.force_authenticate(user=self.op1)
        response = self.client.post(self.URL, {
            'propiedad': self.prop1.pk, 'operario': self.op1.pk
        })
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_cannot_create_for_other_admins_property(self):
        self.client.force_authenticate(user=self.admin1)
        response = self.client.post(self.URL, {
            'propiedad': self.prop2.pk, 'operario': self.op1.pk
        })
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_cannot_assign_another_admin_as_operario(self):
        self.client.force_authenticate(user=self.admin1)
        response = self.client.post(self.URL, {
            'propiedad': self.prop1.pk, 'operario': self.admin2.pk
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    # --- List ---

    def test_admin_sees_only_own_inspections(self):
        self.client.force_authenticate(user=self.admin1)
        response = self.client.get(self.URL)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['id'], self.insp1.pk)

    def test_operario_sees_only_assigned_inspections(self):
        self.client.force_authenticate(user=self.op1)
        response = self.client.get(self.URL)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['id'], self.insp1.pk)

    def test_unauthenticated_returns_401(self):
        response = self.client.get(self.URL)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    # --- Partial update ---

    def test_operario_marks_inspection_completada(self):
        self.client.force_authenticate(user=self.op1)
        response = self.client.patch(
            f'{self.URL}{self.insp1.pk}/', {'estado': 'COMPLETADA'}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.insp1.refresh_from_db()
        self.assertEqual(self.insp1.estado, Inspeccion.Estado.COMPLETADA)

    def test_operario_cannot_change_fields_other_than_estado(self):
        self.client.force_authenticate(user=self.op1)
        response = self.client.patch(
            f'{self.URL}{self.insp1.pk}/', {'propiedad': self.prop2.pk}
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_operario_cannot_set_estado_to_pendiente(self):
        self.client.force_authenticate(user=self.op1)
        response = self.client.patch(
            f'{self.URL}{self.insp1.pk}/', {'estado': 'PENDIENTE'}
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_operario_cannot_update_other_operarios_inspection(self):
        self.client.force_authenticate(user=self.op1)
        response = self.client.patch(
            f'{self.URL}{self.insp2.pk}/', {'estado': 'COMPLETADA'}
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_admin_updates_own_inspection(self):
        self.client.force_authenticate(user=self.admin1)
        response = self.client.patch(
            f'{self.URL}{self.insp1.pk}/', {'operario': self.op1.pk}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_admin_cannot_update_other_admins_inspection(self):
        self.client.force_authenticate(user=self.admin1)
        response = self.client.patch(
            f'{self.URL}{self.insp2.pk}/', {'estado': 'COMPLETADA'}
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    # --- Delete ---

    def test_admin_deletes_own_inspection(self):
        self.client.force_authenticate(user=self.admin1)
        response = self.client.delete(f'{self.URL}{self.insp1.pk}/')
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

    def test_admin_cannot_delete_other_admins_inspection(self):
        self.client.force_authenticate(user=self.admin1)
        response = self.client.delete(f'{self.URL}{self.insp2.pk}/')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_operario_cannot_delete_inspection(self):
        self.client.force_authenticate(user=self.op1)
        response = self.client.delete(f'{self.URL}{self.insp1.pk}/')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


# ---------------------------------------------------------------------------
# Evidence upload tests
# ---------------------------------------------------------------------------

class UploadEvidenceTest(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username='admin', password='Pass1234!', role=User.Role.ADMIN
        )
        self.op = User.objects.create_user(
            username='op', password='Pass1234!', role=User.Role.OPERARIO
        )
        self.other_op = User.objects.create_user(
            username='op2', password='Pass1234!', role=User.Role.OPERARIO
        )
        self.prop = Propiedad.objects.create(
            owner=self.admin, nombre='Prop', direccion='C1'
        )
        self.insp = Inspeccion.objects.create(propiedad=self.prop, operario=self.op)

    def _url(self, pk):
        return f'/api/v1/inspecciones/{pk}/evidencias/'

    @patch('inspecciones.views.solicitar_sello_tiempo', return_value=None)
    def test_assigned_operario_uploads_evidence(self, _mock_tsa):
        self.client.force_authenticate(user=self.op)
        response = self.client.post(
            self._url(self.insp.pk),
            {'foto': _make_image(), 'descripcion': 'Pared dañada'},
            format='multipart',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Evidencia.objects.count(), 1)

    @patch('inspecciones.views.solicitar_sello_tiempo', return_value=None)
    def test_hash_stored_and_tsa_called_when_hash_provided(self, mock_tsa):
        self.client.force_authenticate(user=self.op)
        test_hash = 'a' * 64
        response = self.client.post(
            self._url(self.insp.pk),
            {'foto': _make_image(), 'hash_sha256': test_hash},
            format='multipart',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        evidencia = Evidencia.objects.first()
        self.assertEqual(evidencia.hash_sha256, test_hash)
        mock_tsa.assert_called_once_with(test_hash)

    @patch('inspecciones.views.solicitar_sello_tiempo', return_value=None)
    def test_tsa_not_called_without_hash(self, mock_tsa):
        self.client.force_authenticate(user=self.op)
        self.client.post(
            self._url(self.insp.pk), {'foto': _make_image()}, format='multipart'
        )
        mock_tsa.assert_not_called()

    def test_invalid_mime_type_rejected(self):
        self.client.force_authenticate(user=self.op)
        fake_pdf = SimpleUploadedFile('doc.pdf', b'%PDF-fake', content_type='application/pdf')
        response = self.client.post(
            self._url(self.insp.pk), {'foto': fake_pdf}, format='multipart'
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_file_too_large_rejected(self):
        self.client.force_authenticate(user=self.op)
        big = SimpleUploadedFile('big.jpg', b'x' * (9 * 1024 * 1024), content_type='image/jpeg')
        response = self.client.post(
            self._url(self.insp.pk), {'foto': big}, format='multipart'
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_missing_foto_returns_400(self):
        self.client.force_authenticate(user=self.op)
        response = self.client.post(
            self._url(self.insp.pk), {'descripcion': 'No image'}, format='multipart'
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_unassigned_operario_cannot_upload(self):
        # Returns 404 (not 403) — IDOR prevention: don't reveal the object exists
        self.client.force_authenticate(user=self.other_op)
        response = self.client.post(
            self._url(self.insp.pk), {'foto': _make_image()}, format='multipart'
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_unauthenticated_cannot_upload(self):
        response = self.client.post(
            self._url(self.insp.pk), {'foto': _make_image()}, format='multipart'
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


# ---------------------------------------------------------------------------
# Claim report tests
# ---------------------------------------------------------------------------

class ClaimReportTest(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username='admin', password='Pass1234!', role=User.Role.ADMIN
        )
        self.other_admin = User.objects.create_user(
            username='admin2', password='Pass1234!', role=User.Role.ADMIN
        )
        self.op = User.objects.create_user(
            username='op', password='Pass1234!', role=User.Role.OPERARIO
        )
        self.prop = Propiedad.objects.create(
            owner=self.admin, nombre='Prop', direccion='C1'
        )
        self.insp = Inspeccion.objects.create(propiedad=self.prop, operario=self.op)

    def _url(self, pk):
        return f'/api/v1/inspecciones/{pk}/claim-report/'

    @patch('inspecciones.tasks.generar_pdf_async')
    def test_claim_report_triggers_task_returns_202(self, mock_task):
        mock_task.delay.return_value = MagicMock(id='test-task-id')
        self.client.force_authenticate(user=self.admin)
        response = self.client.get(self._url(self.insp.pk))
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(response.data['task_id'], 'test-task-id')
        mock_task.delay.assert_called_once_with(self.insp.pk)

    def test_claim_report_returns_file_when_listo(self):
        self.insp.pdf_estado = Inspeccion.PdfEstado.LISTO
        self.insp.pdf_ruta = default_storage.save('informes/prueba.pdf', ContentFile(b'%PDF-1.4 signed'))
        self.insp.save()
        self.client.force_authenticate(user=self.admin)
        response = self.client.get(self._url(self.insp.pk))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(b''.join(response.streaming_content), b'%PDF-1.4 signed')

    @patch('inspecciones.tasks.generar_pdf_async')
    def test_claim_report_regenera_si_el_pdf_ya_no_existe(self, mock_task):
        # Un PDF de antes de pasar a S3 (ruta local de un disco que ya no existe)
        mock_task.delay.return_value = MagicMock(id='t')
        self.insp.pdf_estado = Inspeccion.PdfEstado.LISTO
        self.insp.pdf_ruta = '/tmp/no-existe/Reclamacion_Fianza_1_Firmada.pdf'
        self.insp.save()
        self.client.force_authenticate(user=self.admin)
        response = self.client.get(self._url(self.insp.pk))
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        mock_task.delay.assert_called_once_with(self.insp.pk)

    def test_claim_report_returns_202_when_generando(self):
        self.insp.pdf_estado = Inspeccion.PdfEstado.GENERANDO
        self.insp.save()
        self.client.force_authenticate(user=self.admin)
        response = self.client.get(self._url(self.insp.pk))
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertNotIn('task_id', response.data)

    def test_other_admin_cannot_download_report(self):
        self.client.force_authenticate(user=self.other_admin)
        response = self.client.get(self._url(self.insp.pk))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_operario_cannot_download_report(self):
        self.client.force_authenticate(user=self.op)
        response = self.client.get(self._url(self.insp.pk))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_unauthenticated_cannot_download_report(self):
        response = self.client.get(self._url(self.insp.pk))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


# ---------------------------------------------------------------------------
# Claim report status tests
# ---------------------------------------------------------------------------

class ClaimReportStatusTest(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username='admin', password='Pass1234!', role=User.Role.ADMIN
        )
        self.other_admin = User.objects.create_user(
            username='admin2', password='Pass1234!', role=User.Role.ADMIN
        )
        self.op = User.objects.create_user(
            username='op', password='Pass1234!', role=User.Role.OPERARIO
        )
        self.prop = Propiedad.objects.create(
            owner=self.admin, nombre='Prop', direccion='C1'
        )
        self.insp = Inspeccion.objects.create(propiedad=self.prop, operario=self.op)

    def _url(self, pk):
        return f'/api/v1/inspecciones/{pk}/claim-report-status/'

    def test_status_pendiente_by_default(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.get(self._url(self.insp.pk))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['estado'], Inspeccion.PdfEstado.PENDIENTE)
        self.assertFalse(response.data['pdf_listo'])

    def test_status_listo_when_pdf_ready(self):
        self.insp.pdf_estado = Inspeccion.PdfEstado.LISTO
        self.insp.save()
        self.client.force_authenticate(user=self.admin)
        response = self.client.get(self._url(self.insp.pk))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['pdf_listo'])

    def test_status_other_admin_gets_403(self):
        self.client.force_authenticate(user=self.other_admin)
        response = self.client.get(self._url(self.insp.pk))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_status_unauthenticated_gets_401(self):
        response = self.client.get(self._url(self.insp.pk))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


# ---------------------------------------------------------------------------
# Audit token tests
# ---------------------------------------------------------------------------

class AuditTokenTest(TestCase):
    def setUp(self):
        admin = User.objects.create_user(
            username='admin', password='Pass1234!', role=User.Role.ADMIN
        )
        op = User.objects.create_user(
            username='op', password='Pass1234!', role=User.Role.OPERARIO
        )
        prop = Propiedad.objects.create(owner=admin, nombre='P', direccion='C')
        insp = Inspeccion.objects.create(propiedad=prop, operario=op)
        self.evidencia = Evidencia.objects.create(
            inspeccion=insp,
            foto=SimpleUploadedFile('t.jpg', b'img', content_type='image/jpeg'),
            hash_sha256='a' * 64,
        )

    def test_valid_token_matches_correct_id(self):
        from inspecciones.views import _build_audit_token, _token_matches_evidence
        token = _build_audit_token(self.evidencia.pk)
        self.assertTrue(_token_matches_evidence(token, self.evidencia.pk))

    def test_token_does_not_match_wrong_id(self):
        from inspecciones.views import _build_audit_token, _token_matches_evidence
        token = _build_audit_token(self.evidencia.pk)
        self.assertFalse(_token_matches_evidence(token, self.evidencia.pk + 999))

    def test_malformed_token_does_not_match(self):
        from inspecciones.views import _token_matches_evidence
        self.assertFalse(_token_matches_evidence('bad.token.data', self.evidencia.pk))

    def test_none_token_does_not_match(self):
        from inspecciones.views import _token_matches_evidence
        self.assertFalse(_token_matches_evidence(None, self.evidencia.pk))


# ---------------------------------------------------------------------------
# TSA service tests
# ---------------------------------------------------------------------------

class TSAServiceTest(TestCase):
    @patch('inspecciones.services.tsa.requests.post')
    def test_returns_none_on_network_error(self, mock_post):
        mock_post.side_effect = Exception('Network error')
        from inspecciones.services.tsa import solicitar_sello_tiempo
        self.assertIsNone(solicitar_sello_tiempo('a' * 64))

    @patch('inspecciones.services.tsa.requests.post')
    def test_returns_none_on_non_200_response(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 503
        mock_resp.text = 'Service Unavailable'
        mock_post.return_value = mock_resp
        from inspecciones.services.tsa import solicitar_sello_tiempo
        self.assertIsNone(solicitar_sello_tiempo('a' * 64))

    @patch('inspecciones.services.tsa.requests.post')
    def test_returns_none_when_no_hash_and_network_fails(self, mock_post):
        mock_post.side_effect = Exception('No connection')
        from inspecciones.services.tsa import solicitar_sello_tiempo
        self.assertIsNone(solicitar_sello_tiempo(None))

    @patch('inspecciones.services.tsa.requests.post')
    def test_default_timeout_is_3_seconds(self, mock_post):
        mock_post.side_effect = Exception('Network error')
        from inspecciones.services.tsa import solicitar_sello_tiempo
        solicitar_sello_tiempo('a' * 64)
        self.assertEqual(mock_post.call_args.kwargs['timeout'], 3.0)

    @patch('inspecciones.services.tsa.requests.post')
    def test_custom_timeout_from_settings(self, mock_post):
        mock_post.side_effect = Exception('Network error')
        from inspecciones.services.tsa import solicitar_sello_tiempo
        with self.settings(TSA_TIMEOUT='2.5'):
            solicitar_sello_tiempo('a' * 64)
        self.assertEqual(mock_post.call_args.kwargs['timeout'], 2.5)

    @patch('inspecciones.services.tsa.requests.post')
    def test_invalid_timeouts_fall_back_to_3_seconds_with_warning(self, mock_post):
        mock_post.side_effect = Exception('Network error')
        from inspecciones.services.tsa import solicitar_sello_tiempo
        for valor in ('abc', '10s', '0', '-1', 'nan', 'inf'):
            with self.subTest(valor=valor), self.settings(TSA_TIMEOUT=valor):
                with self.assertLogs('inspecciones.services.tsa', level='WARNING') as logs:
                    solicitar_sello_tiempo('a' * 64)
                self.assertEqual(mock_post.call_args.kwargs['timeout'], 3.0)
                self.assertIn('TSA_TIMEOUT no válido', logs.output[0])

    @patch('inspecciones.services.tsa.requests.post')
    def test_timeout_logged_as_warning_not_error(self, mock_post):
        import requests
        mock_post.side_effect = requests.exceptions.Timeout('read timed out')
        from inspecciones.services.tsa import solicitar_sello_tiempo
        with self.assertLogs('inspecciones.services.tsa', level='WARNING') as logs:
            self.assertIsNone(solicitar_sello_tiempo('a' * 64))
        self.assertTrue(all(r.levelname == 'WARNING' for r in logs.records))


# ---------------------------------------------------------------------------
# Re-sellado periódico de evidencias pendientes (TSA)
# ---------------------------------------------------------------------------

@override_settings(TSA_RESELLO_PAUSA_SEG=0)
class ResellarEvidenciasPendientesTest(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.admin = User.objects.create_user(
            username='admin_resello', password='x', role='ADMIN',
        )
        self.operario = User.objects.create_user(
            username='op_resello', password='x', role='OPERARIO',
        )
        propiedad = Propiedad.objects.create(
            owner=self.admin, nombre='P', direccion='Calle 1',
        )
        self.inspeccion = Inspeccion.objects.create(
            propiedad=propiedad, operario=self.operario,
        )

    def _evidencia(self, hash_sha256='a' * 64, tsa_token=None):
        return Evidencia.objects.create(
            inspeccion=self.inspeccion, foto=_make_image(),
            hash_sha256=hash_sha256, tsa_token=tsa_token,
        )

    @patch('inspecciones.tasks.solicitar_sello_tiempo')
    def test_sella_pendientes(self, mock_tsa):
        from django.utils import timezone
        ahora = timezone.now()
        mock_tsa.return_value = {'timestamp': ahora, 'token': b'tok'}
        ev = self._evidencia()
        from .tasks import resellar_evidencias_pendientes
        resellar_evidencias_pendientes()
        ev.refresh_from_db()
        self.assertEqual(bytes(ev.tsa_token), b'tok')
        self.assertEqual(ev.tsa_timestamp, ahora)
        mock_tsa.assert_called_once_with('a' * 64)

    @patch('inspecciones.tasks.solicitar_sello_tiempo')
    def test_ignora_ya_selladas(self, mock_tsa):
        ev = self._evidencia(tsa_token=b'viejo')
        from .tasks import resellar_evidencias_pendientes
        resellar_evidencias_pendientes()
        mock_tsa.assert_not_called()
        ev.refresh_from_db()
        self.assertEqual(bytes(ev.tsa_token), b'viejo')

    @patch('inspecciones.tasks.solicitar_sello_tiempo')
    def test_ignora_sin_hash(self, mock_tsa):
        self._evidencia(hash_sha256=None)
        from .tasks import resellar_evidencias_pendientes
        resellar_evidencias_pendientes()
        mock_tsa.assert_not_called()

    @patch('inspecciones.tasks.solicitar_sello_tiempo')
    def test_continua_si_una_falla(self, mock_tsa):
        from django.utils import timezone
        e1 = self._evidencia(hash_sha256='1' * 64)
        e2 = self._evidencia(hash_sha256='2' * 64)
        e3 = self._evidencia(hash_sha256='3' * 64)
        ahora = timezone.now()

        def fake(h):
            if h == '1' * 64:
                return None
            if h == '2' * 64:
                raise RuntimeError('FreeTSA caído')
            return {'timestamp': ahora, 'token': b'ok'}

        mock_tsa.side_effect = fake
        from .tasks import resellar_evidencias_pendientes
        with self.assertLogs('inspecciones.tasks', level='WARNING'):
            resultado = resellar_evidencias_pendientes()
        for e in (e1, e2, e3):
            e.refresh_from_db()
        self.assertIsNone(e1.tsa_token)
        self.assertIsNone(e2.tsa_token)
        self.assertEqual(bytes(e3.tsa_token), b'ok')
        self.assertEqual(mock_tsa.call_count, 3)
        self.assertEqual(resultado, {'procesadas': 3, 'selladas': 1})

    @patch('inspecciones.tasks.solicitar_sello_tiempo')
    def test_respeta_tamano_de_lote(self, mock_tsa):
        from django.utils import timezone
        mock_tsa.return_value = {'timestamp': timezone.now(), 'token': b'tok'}
        for i in range(5):
            self._evidencia(hash_sha256=str(i) * 64)
        from .tasks import resellar_evidencias_pendientes
        with self.settings(TSA_RESELLO_BATCH_SIZE=2):
            resellar_evidencias_pendientes()
        self.assertEqual(mock_tsa.call_count, 2)
        self.assertEqual(Evidencia.objects.filter(tsa_token__isnull=False).count(), 2)

    @patch('inspecciones.tasks.solicitar_sello_tiempo', return_value=None)
    def test_rota_las_que_fallan(self, mock_tsa):
        # Con lote 2 y tres evidencias que siempre fallan, la segunda pasada
        # empieza por la que no se intentó en la primera
        evs = [self._evidencia(hash_sha256=str(i) * 64) for i in range(3)]
        from .tasks import resellar_evidencias_pendientes
        with self.settings(TSA_RESELLO_BATCH_SIZE=2, TSA_RESELLO_PAUSA_SEG=0, TSA_RESELLO_MAX_FALLOS=10):
            resellar_evidencias_pendientes()
            self.assertEqual([c.args[0] for c in mock_tsa.call_args_list], ['0' * 64, '1' * 64])
            mock_tsa.reset_mock()
            resellar_evidencias_pendientes()
        self.assertEqual(mock_tsa.call_args_list[0].args[0], '2' * 64)
        for ev in evs:
            ev.refresh_from_db()
            self.assertIsNotNone(ev.tsa_ultimo_intento)

    def test_indice_parcial_de_pendientes(self):
        nombres = {i.name for i in Evidencia._meta.indexes}
        self.assertIn('evidencia_tsa_pendiente_idx', nombres)

    def test_beat_schedule_configurado(self):
        from django.conf import settings
        entrada = settings.CELERY_BEAT_SCHEDULE['resellar-evidencias-pendientes']
        self.assertEqual(entrada['task'], 'inspecciones.tasks.resellar_evidencias_pendientes')

    @patch('inspecciones.tasks.solicitar_sello_tiempo')
    def test_soft_time_limit_corta_el_lote(self, mock_tsa):
        from celery.exceptions import SoftTimeLimitExceeded
        self._evidencia(hash_sha256='1' * 64)
        self._evidencia(hash_sha256='2' * 64)
        mock_tsa.side_effect = SoftTimeLimitExceeded()
        from .tasks import resellar_evidencias_pendientes
        with self.assertLogs('inspecciones.tasks', level='WARNING'):
            resultado = resellar_evidencias_pendientes()
        self.assertEqual(mock_tsa.call_count, 1)
        self.assertEqual(resultado['selladas'], 0)

    @patch('inspecciones.tasks.solicitar_sello_tiempo', return_value=None)
    def test_corta_tras_fallos_consecutivos(self, mock_tsa):
        for i in range(6):
            self._evidencia(hash_sha256=str(i) * 64)
        from .tasks import resellar_evidencias_pendientes
        with self.settings(TSA_RESELLO_MAX_FALLOS=2):
            with self.assertLogs('inspecciones.tasks', level='WARNING'):
                resellar_evidencias_pendientes()
        self.assertEqual(mock_tsa.call_count, 2)

    @patch('inspecciones.tasks.solicitar_sello_tiempo')
    def test_no_pisa_sello_existente(self, mock_tsa):
        from django.utils import timezone
        ev = self._evidencia()

        def fake(h):
            # Otro proceso sella la evidencia mientras esperamos a la TSA
            Evidencia.objects.filter(pk=ev.pk).update(tsa_token=b'otro')
            return {'timestamp': timezone.now(), 'token': b'mio'}

        mock_tsa.side_effect = fake
        from .tasks import resellar_evidencias_pendientes
        resultado = resellar_evidencias_pendientes()
        ev.refresh_from_db()
        self.assertEqual(bytes(ev.tsa_token), b'otro')
        self.assertEqual(resultado['selladas'], 0)

    @patch('inspecciones.tasks.solicitar_sello_tiempo')
    def test_evidencia_borrada_a_mitad_de_lote(self, mock_tsa):
        from django.utils import timezone
        e1 = self._evidencia(hash_sha256='1' * 64)
        e2 = self._evidencia(hash_sha256='2' * 64)

        def fake(h):
            if h == '1' * 64:
                e2.delete()
            return {'timestamp': timezone.now(), 'token': b'tok'}

        mock_tsa.side_effect = fake
        from .tasks import resellar_evidencias_pendientes
        resultado = resellar_evidencias_pendientes()
        e1.refresh_from_db()
        self.assertEqual(bytes(e1.tsa_token), b'tok')
        self.assertFalse(Evidencia.objects.filter(pk=e2.pk).exists())
        self.assertEqual(resultado['selladas'], 1)

    @patch('inspecciones.tasks.solicitar_sello_tiempo')
    def test_lock_ocupado_omite_ejecucion(self, mock_tsa):
        from django.core.cache import cache
        from .tasks import resellar_evidencias_pendientes, RESELLO_LOCK_KEY
        self._evidencia()
        cache.add(RESELLO_LOCK_KEY, 1, 60)
        resultado = resellar_evidencias_pendientes()
        mock_tsa.assert_not_called()
        self.assertTrue(resultado['omitida'])
        self.assertIsNotNone(cache.get(RESELLO_LOCK_KEY))  # no libera el lock ajeno

    @patch('inspecciones.tasks.solicitar_sello_tiempo')
    def test_libera_lock_al_terminar(self, mock_tsa):
        from django.core.cache import cache
        from .tasks import resellar_evidencias_pendientes, RESELLO_LOCK_KEY
        resellar_evidencias_pendientes()
        self.assertIsNone(cache.get(RESELLO_LOCK_KEY))

    @patch('inspecciones.tasks.solicitar_sello_tiempo')
    def test_ignora_hash_vacio(self, mock_tsa):
        self._evidencia(hash_sha256='')
        from .tasks import resellar_evidencias_pendientes
        resellar_evidencias_pendientes()
        mock_tsa.assert_not_called()


# ---------------------------------------------------------------------------
# Throttling de los endpoints públicos de auditoría
# ---------------------------------------------------------------------------

from core.testing import limitar


class AuditThrottleTest(APITestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()  # el throttle guarda contadores en caché
        self.addCleanup(cache.clear)
        admin = User.objects.create_user(
            username='admin', password='Pass1234!', role=User.Role.ADMIN
        )
        op = User.objects.create_user(
            username='op', password='Pass1234!', role=User.Role.OPERARIO
        )
        prop = Propiedad.objects.create(owner=admin, nombre='P', direccion='C')
        insp = Inspeccion.objects.create(propiedad=prop, operario=op)
        self.evidencia = Evidencia.objects.create(
            inspeccion=insp,
            foto=SimpleUploadedFile('t.jpg', b'img', content_type='image/jpeg'),
            hash_sha256='a' * 64,
        )

    def test_audit_returns_429_over_rate(self):
        url = '/api/v1/audit/%d/' % self.evidencia.pk
        with limitar(auditoria='2/min'):
            resps = [self.client.get(url) for _ in range(3)]
        codes = [r.status_code for r in resps]
        self.assertNotIn(status.HTTP_429_TOO_MANY_REQUESTS, codes[:2])
        self.assertEqual(codes[2], status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertIn('Retry-After', resps[2])

    def test_audit_spoofed_xff_does_not_bypass(self):
        url = '/api/v1/audit/%d/' % self.evidencia.pk
        with limitar(auditoria='2/min'):
            codes = [
                self.client.get(url, HTTP_X_FORWARDED_FOR='1.2.3.%d, 10.0.0.1' % i).status_code
                for i in range(3)
            ]
        self.assertEqual(codes[2], status.HTTP_429_TOO_MANY_REQUESTS)

    def test_audit_token_returns_429_over_rate(self):
        url = '/api/v1/audit/%d/token/' % self.evidencia.pk
        with limitar(auditoria='2/min'):
            codes = [self.client.get(url).status_code for _ in range(3)]
        self.assertNotIn(status.HTTP_429_TOO_MANY_REQUESTS, codes[:2])
        self.assertEqual(codes[2], status.HTTP_429_TOO_MANY_REQUESTS)

    def test_audit_default_rate_configured(self):
        from rest_framework.settings import api_settings
        self.assertIn('auditoria', api_settings.DEFAULT_THROTTLE_RATES)


class PdfFirmadoUrlTest(APITestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.addCleanup(cache.clear)
        admin = User.objects.create_user(username='admin_pdf', password='x', role=User.Role.ADMIN)
        prop = Propiedad.objects.create(owner=admin, nombre='P', direccion='C')
        self.insp = Inspeccion.objects.create(propiedad=prop)
        self.otra = Inspeccion.objects.create(propiedad=prop)
        self.ruta = default_storage.save('informes/prueba.pdf', ContentFile(b'%PDF-1.7 prueba'))
        for insp in (self.insp, self.otra):
            insp.pdf_estado = Inspeccion.PdfEstado.LISTO
            insp.pdf_ruta = self.ruta
            insp.save(update_fields=['pdf_estado', 'pdf_ruta'])

    def _url(self, inspeccion):
        from .pdf_publico import url_pdf_firmada
        return url_pdf_firmada(inspeccion.pk)

    @override_settings(BACKEND_PUBLIC_URL='https://api.example.com/')
    def test_url_absoluta_descarga_el_pdf_sin_autenticacion(self):
        url = self._url(self.insp)
        self.assertTrue(url.startswith('https://api.example.com/api/v1/informes/'))
        resp = self.client.get(url.removeprefix('https://api.example.com'))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(b''.join(resp.streaming_content), b'%PDF-1.7 prueba')
        self.assertEqual(resp['Content-Type'], 'application/pdf')

    def test_token_manipulado_404(self):
        from .pdf_publico import token_pdf
        token = token_pdf(self.insp.pk)
        # Cambiar el id conservando la firma no sirve para otra inspección
        falso = f'{self.otra.pk}' + token[len(str(self.insp.pk)):]
        self.assertEqual(self.client.get(f'/api/v1/informes/{falso}/').status_code, 404)
        self.assertEqual(self.client.get('/api/v1/informes/basura/').status_code, 404)

    def test_token_de_auditoria_no_vale_para_pdf(self):
        # Salt distinto: un token de otro uso firmado con la misma clave no sirve
        from django.core.signing import TimestampSigner
        ajeno = TimestampSigner(salt='checkit.evidence.audit').sign(str(self.insp.pk))
        self.assertEqual(self.client.get(f'/api/v1/informes/{ajeno}/').status_code, 404)

    def test_caducado_410(self):
        from .pdf_publico import token_pdf
        token = token_pdf(self.insp.pk)
        with override_settings(PDF_URL_CADUCIDAD_HORAS=0):
            with patch('django.core.signing.time.time', return_value=__import__('time').time() + 10):
                resp = self.client.get(f'/api/v1/informes/{token}/')
        self.assertEqual(resp.status_code, 410)

    def test_pdf_no_disponible_404(self):
        from .pdf_publico import token_pdf
        Inspeccion.objects.filter(pk=self.insp.pk).update(pdf_estado=Inspeccion.PdfEstado.ERROR)
        self.assertEqual(self.client.get(f'/api/v1/informes/{token_pdf(self.insp.pk)}/').status_code, 404)

    def test_throttle(self):
        from .pdf_publico import token_pdf
        url = f'/api/v1/informes/{token_pdf(self.insp.pk)}/'
        with limitar(auditoria='2/min'):
            codes = [self.client.get(url).status_code for _ in range(3)]
        self.assertEqual(codes[2], status.HTTP_429_TOO_MANY_REQUESTS)


class AlmacenamientoPdfTest(TestCase):
    """El PDF y las fotos pasan por el storage de Django (S3/R2 en producción)."""

    def setUp(self):
        admin = User.objects.create_user(username='admin_alm', password='x', role=User.Role.ADMIN)
        prop = Propiedad.objects.create(owner=admin, nombre='P', direccion='C')
        self.insp = Inspeccion.objects.create(propiedad=prop)

    def _generar(self, contenido=b'%PDF-1.7 firmado'):
        from .tasks import generar_pdf_async

        def firmar(entrada, salida):
            with open(salida, 'wb') as f:
                f.write(contenido)

        with patch('inspecciones.services.pdf_signer.firmar_pdf_reclamacion', side_effect=firmar):
            generar_pdf_async.apply(args=[self.insp.pk]).get()
        self.insp.refresh_from_db()

    def test_la_tarea_guarda_el_pdf_firmado_en_el_storage(self):
        self._generar()
        self.assertEqual(self.insp.pdf_estado, Inspeccion.PdfEstado.LISTO)
        self.assertEqual(self.insp.pdf_ruta, f'informes/Reclamacion_Fianza_{self.insp.pk}_Firmada.pdf')
        with default_storage.open(self.insp.pdf_ruta, 'rb') as f:
            self.assertEqual(f.read(), b'%PDF-1.7 firmado')

    def test_no_quedan_ficheros_intermedios_en_disco(self):
        directorio = tempfile.mkdtemp()
        self.addCleanup(os.rmdir, directorio)
        with patch('tempfile.tempdir', directorio):
            self._generar()
        self.assertEqual(os.listdir(directorio), [])

    def test_regenerar_sustituye_el_pdf_con_el_mismo_nombre(self):
        self._generar(b'%PDF v1')
        primero = self.insp.pdf_ruta
        self._generar(b'%PDF v2')
        self.assertEqual(self.insp.pdf_ruta, primero)
        with default_storage.open(primero, 'rb') as f:
            self.assertEqual(f.read(), b'%PDF v2')

    def test_las_fotos_se_leen_del_storage_y_no_de_una_ruta_local(self):
        from .services.pdf_generator import generar_informe_base
        Evidencia.objects.create(inspeccion=self.insp, foto=_make_image(), hash_sha256='a' * 64)
        fd, ruta = tempfile.mkstemp(suffix='.pdf')
        os.close(fd)
        self.addCleanup(os.remove, ruta)
        generar_informe_base(self.insp, ruta)
        with open(ruta, 'rb') as f:
            pdf = f.read()
        self.assertIn(b'/Subtype /Image', pdf)
        self.assertNotIn(b'No se pudo cargar la foto', pdf)
