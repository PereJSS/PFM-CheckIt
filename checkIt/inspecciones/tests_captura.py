"""#29: hora de captura (la que declara el dispositivo) frente a hora de recepción y de sello."""
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from propiedades.models import Propiedad
from users.models import User

from .models import Evidencia, Inspeccion
from .tests import _make_image


@patch('inspecciones.views.solicitar_sello_tiempo', return_value=None)
class CapturadaEnSubidaTest(APITestCase):
    def setUp(self):
        admin = User.objects.create_user(username='admin', password='Pass1234!', role=User.Role.ADMIN)
        self.op = User.objects.create_user(username='op', password='Pass1234!', role=User.Role.OPERARIO)
        prop = Propiedad.objects.create(owner=admin, nombre='Prop', direccion='C1')
        self.insp = Inspeccion.objects.create(propiedad=prop, operario=self.op)
        self.client.force_authenticate(user=self.op)

    def _subir(self, **extra):
        return self.client.post(
            f'/api/v1/inspecciones/{self.insp.pk}/evidencias/',
            {'foto': _make_image(), **extra}, format='multipart',
        )

    def test_guarda_la_hora_que_declara_el_dispositivo(self, _tsa):
        hora = (timezone.now() - timedelta(minutes=12)).replace(microsecond=0)
        resp = self._subir(capturada_en=hora.isoformat())
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Evidencia.objects.get().capturada_en, hora)
        self.assertIsNotNone(resp.data['capturada_en'])

    def test_es_opcional(self, _tsa):
        self.assertEqual(self._subir().status_code, status.HTTP_201_CREATED)
        self.assertIsNone(Evidencia.objects.get().capturada_en)

    def test_acepta_el_formato_de_toisostring(self, _tsa):
        # Date.prototype.toISOString(): milisegundos y Z
        resp = self._subir(capturada_en='2026-10-22T08:15:03.250Z')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Evidencia.objects.get().capturada_en.isoformat(), '2026-10-22T08:15:03.250000+00:00')

    def test_rechaza_lo_que_no_es_fecha(self, _tsa):
        resp = self._subir(capturada_en='ayer')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Evidencia.objects.count(), 0)

    def test_rechaza_fecha_sin_zona_horaria(self, _tsa):
        resp = self._subir(capturada_en='2026-10-22T08:15:03')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_rechaza_una_hora_futura(self, _tsa):
        resp = self._subir(capturada_en=(timezone.now() + timedelta(minutes=10)).isoformat())
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_fecha_absurda_da_400_y_no_500(self, _tsa):
        # Año 1 con offset positivo desbordaba al pasar a UTC
        for valor in ('0001-01-01T00:00:00+05:00', '1999-12-31T23:59:59Z', '2026-13-01T00:00:00Z'):
            with self.subTest(valor=valor):
                self.assertEqual(self._subir(capturada_en=valor).status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Evidencia.objects.count(), 0)

    def test_tolera_un_reloj_algo_adelantado(self, _tsa):
        resp = self._subir(capturada_en=(timezone.now() + timedelta(minutes=2)).isoformat())
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)


class HorasEnElPdfTest(TestCase):
    def setUp(self):
        admin = User.objects.create_user(username='admin', password='Pass1234!', role=User.Role.ADMIN)
        prop = Propiedad.objects.create(owner=admin, nombre='Prop', direccion='C1')
        self.insp = Inspeccion.objects.create(propiedad=prop)

    def _evidencia(self, **campos):
        return Evidencia.objects.create(inspeccion=self.insp, foto='evidencias/x.jpg', **campos)

    def test_muestra_las_tres_horas_y_quien_las_da(self):
        from .services.pdf_generator import _horas_evidencia
        ev = self._evidencia(
            capturada_en=timezone.now() - timedelta(hours=1), tsa_timestamp=timezone.now(),
        )
        texto = _horas_evidencia(ev)
        self.assertIn('Capturada', texto)
        self.assertIn('según el dispositivo', texto)
        self.assertIn('Recibida', texto)
        self.assertIn('Sello TSA', texto)

    def test_avisa_si_la_hora_del_dispositivo_no_cuadra(self):
        from .services.pdf_generator import _horas_evidencia
        antes = self._evidencia(capturada_en=self.insp.fecha_creacion - timedelta(days=3))
        self.assertEqual(antes.avisos_hora(), ['anterior al inicio de la inspección'])
        self.assertIn('anterior al inicio de la inspección', _horas_evidencia(antes))
        despues = self._evidencia(capturada_en=timezone.now() + timedelta(minutes=3))
        self.assertEqual(despues.avisos_hora(), ['posterior a la recepción'])
        self.assertEqual(self._evidencia(capturada_en=timezone.now()).avisos_hora(), [])
        self.assertEqual(self._evidencia().avisos_hora(), [])

    def test_la_descripcion_no_se_interpreta_como_marcado(self):
        import os
        import tempfile
        from .services.pdf_generator import generar_informe_base
        self._evidencia(descripcion='Grieta <b>sin cerrar & <font color="red">roja')
        with tempfile.TemporaryDirectory() as d:
            ruta = generar_informe_base(self.insp, os.path.join(d, 'a.pdf'))
            self.assertGreater(os.path.getsize(ruta), 0)

    def test_sin_hora_del_dispositivo_no_la_inventa(self):
        from .services.pdf_generator import _horas_evidencia
        texto = _horas_evidencia(self._evidencia())
        self.assertNotIn('Capturada', texto)
        self.assertIn('Recibida', texto)
        self.assertIn('Sello TSA pendiente', texto)
