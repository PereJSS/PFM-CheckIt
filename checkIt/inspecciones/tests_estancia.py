"""Estancia de cada foto en su propio campo.

Antes solo viajaba dentro de `descripcion`, y solo si el operario no escribía nada: con una
descripción, el acta no decía en qué estancia estaba la foto. La declara el operario y queda
fuera del manifiesto sellado, como la descripción y la hora del dispositivo.
"""
from unittest.mock import patch

from django.test import TestCase
from rest_framework import status
from rest_framework.test import APITestCase

from propiedades.models import Propiedad
from users.models import User

from .models import Evidencia, Inspeccion
from .services.manifiesto import construir_manifiesto
from .tests import _make_image


@patch('inspecciones.views.solicitar_sello_tiempo', return_value=None)
class EstanciaEnSubidaTest(APITestCase):
    def setUp(self):
        admin = User.objects.create_user(username='admin', password='Pass1234!', role=User.Role.ADMIN)
        self.op = User.objects.create_user(
            username='op', password='Pass1234!', role=User.Role.OPERARIO, administrador=admin,
        )
        prop = Propiedad.objects.create(owner=admin, nombre='Prop', direccion='C1')
        self.insp = Inspeccion.objects.create(propiedad=prop, operario=self.op)
        self.client.force_authenticate(user=self.op)

    def _subir(self, **extra):
        return self.client.post(
            f'/api/v1/inspecciones/{self.insp.pk}/evidencias/',
            {'foto': _make_image(), **extra}, format='multipart',
        )

    def test_guarda_estancia_y_descripcion_por_separado(self, _tsa):
        resp = self._subir(estancia='Cocina', descripcion='Grieta junto al fregadero')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        ev = Evidencia.objects.get()
        self.assertEqual(ev.estancia, 'Cocina')
        self.assertEqual(ev.descripcion, 'Grieta junto al fregadero')
        self.assertEqual(resp.data['estancia'], 'Cocina')

    def test_es_opcional_para_los_integradores(self, _tsa):
        self.assertEqual(self._subir().status_code, status.HTTP_201_CREATED)
        self.assertIsNone(Evidencia.objects.get().estancia)

    def test_quita_espacios_y_trata_el_vacio_como_sin_estancia(self, _tsa):
        self._subir(estancia='  Baño Principal  ')
        self._subir(estancia='   ')
        self.assertEqual(
            list(Evidencia.objects.order_by('pk').values_list('estancia', flat=True)),
            ['Baño Principal', None],
        )

    def test_rechaza_una_estancia_demasiado_larga_sin_guardar_la_foto(self, _tsa):
        resp = self._subir(estancia='x' * 61)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('estancia', resp.data['error'])
        self.assertEqual(Evidencia.objects.count(), 0)

    def test_rechaza_caracteres_de_control_y_de_direccion(self, _tsa):
        # U+202E invierte el texto en el acta: mostraría algo distinto de lo guardado
        for trampa in ['Cocina\u202e', 'Coci\nna', 'Coc\x00ina', 'Baño\u200b', 'Sa\u2028lón']:
            with self.subTest(trampa=repr(trampa)):
                resp = self._subir(estancia=trampa)
                self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn('estancia', resp.data['error'])
        self.assertEqual(Evidencia.objects.count(), 0)

    def test_rechaza_lo_que_no_es_texto(self, _tsa):
        # Solo llega con un cuerpo JSON, que no puede llevar la foto: se prueba la validación
        from .views import _estancia
        for valor in (['Cocina'], {'a': 1}, 3):
            with self.subTest(valor=valor):
                self.assertEqual(_estancia(valor), (None, 'estancia debe ser texto.'))

    def test_normaliza_a_nfc_y_mide_en_caracteres_compuestos(self, _tsa):
        # «ñ» como n + tilde combinable: 60 letras son 120 puntos de código
        self.assertEqual(self._subir(estancia='n\u0303' * 60).status_code, status.HTTP_201_CREATED)
        self.assertEqual(Evidencia.objects.get().estancia, '\u00f1' * 60)

    def test_acepta_el_largo_maximo(self, _tsa):
        self.assertEqual(self._subir(estancia='x' * 60).status_code, status.HTTP_201_CREATED)

    def test_sale_en_el_listado_de_la_inspeccion(self, _tsa):
        self._subir(estancia='Terraza')
        resp = self.client.get(f'/api/v1/inspecciones/{self.insp.pk}/')
        self.assertEqual(resp.data['evidencias'][0]['estancia'], 'Terraza')


class EstanciaFueraDelSelloTest(TestCase):
    def test_la_estancia_no_cambia_las_hojas_ni_la_raiz(self):
        # Lo sellado no puede depender de un dato que declara el operario
        admin = User.objects.create_user(username='admin', password='x', role=User.Role.ADMIN)
        prop = Propiedad.objects.create(owner=admin, nombre='Prop', direccion='C1')
        insp = Inspeccion.objects.create(propiedad=prop)
        ev = Evidencia.objects.create(inspeccion=insp, foto='evidencias/x.jpg', hash_sha256='a' * 64)
        antes = construir_manifiesto(insp)
        Evidencia.objects.filter(pk=ev.pk).update(estancia='Cocina')
        despues = construir_manifiesto(insp)
        self.assertEqual(antes, despues)
        self.assertEqual(set(despues['hojas'][0]), {'evidencia', 'sha256'})


class EstanciaEnElPdfTest(TestCase):
    def setUp(self):
        admin = User.objects.create_user(username='admin', password='x', role=User.Role.ADMIN)
        prop = Propiedad.objects.create(owner=admin, nombre='Prop', direccion='C1')
        self.insp = Inspeccion.objects.create(propiedad=prop)

    def _texto(self, **campos):
        from .services.pdf_generator import _texto_evidencia
        ev = Evidencia.objects.create(inspeccion=self.insp, foto='evidencias/x.jpg', **campos)
        return _texto_evidencia(ev)

    def test_muestra_estancia_y_descripcion(self):
        texto = self._texto(estancia='Cocina', descripcion='Grieta')
        self.assertIn('Cocina', texto)
        self.assertIn('Grieta', texto)
        self.assertIn('declarada por el operario', texto)

    def test_sin_estancia_no_la_inventa(self):
        texto = self._texto(descripcion='Grieta')
        self.assertNotIn('Estancia', texto)
        self.assertIn('Grieta', texto)

    def test_sin_nada_dice_sin_descripcion(self):
        self.assertIn('Sin descripción', self._texto())

    def test_escapa_el_marcado(self):
        texto = self._texto(estancia='<b>Baño</b>', descripcion='a & <font color="red">b')
        self.assertIn('&lt;b&gt;Baño&lt;/b&gt;', texto)
        self.assertNotIn('<font', texto)
        self.assertIn('&amp;', texto)
