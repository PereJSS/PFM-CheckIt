"""Descripción de cada foto: texto libre del operario, con límite y sin caracteres trampa.

Antes se guardaba tal cual: un carácter nulo daba un 500 en Postgres (y el móvil reintentaba
la foto para siempre), no había límite de largo y un U+202E invertía el texto en el acta.
"""
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APITestCase

from propiedades.models import Propiedad
from users.models import User

from .models import DESCRIPCION_MAX, Evidencia, Inspeccion
from .tests import _make_image
from .views import _descripcion


@patch('inspecciones.views.solicitar_sello_tiempo', return_value=None)
class DescripcionEnSubidaTest(APITestCase):
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

    def test_un_caracter_nulo_da_400_y_no_guarda_la_foto(self, _tsa):
        resp = self._subir(descripcion='Grieta\x00 en la pared')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('descripcion', resp.data['error'])
        self.assertEqual(Evidencia.objects.count(), 0)

    def test_rechaza_controles_y_direccion_del_texto(self, _tsa):
        # U+202E invierte el texto en el acta: mostraría algo distinto de lo guardado
        # Y los invisibles que esconden texto: etiquetas Unicode (U+E0041), ancho cero,
        # BOM, word joiner y guion blando
        for trampa in [
            'Grieta\u202e', 'a\u2066b', 'a\u200fb', 'a\x1bb', 'a\x7fb', 'a\u2028b', 'a\u2029b',
            'a\U000e0041b', 'a\u200bb', 'a\ufeffb', 'a\u2060b', 'a\u00adb',
        ]:
            with self.subTest(trampa=repr(trampa)):
                resp = self._subir(descripcion=trampa)
                self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn('descripcion', resp.data['error'])
        self.assertEqual(Evidencia.objects.count(), 0)

    def test_rechaza_una_descripcion_demasiado_larga(self, _tsa):
        resp = self._subir(descripcion='x' * (DESCRIPCION_MAX + 1))
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn(str(DESCRIPCION_MAX), resp.data['error'])
        self.assertEqual(Evidencia.objects.count(), 0)

    def test_acepta_el_largo_maximo(self, _tsa):
        resp = self._subir(descripcion='x' * DESCRIPCION_MAX)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

    def test_admite_saltos_de_linea_tabuladores_y_emojis(self, _tsa):
        # El ZWJ (U+200D) de los emojis compuestos es de formato pero legítimo
        texto = 'Mancha en la pared\n\tjunto a la ventana \U0001f468\u200d\U0001f527 y\u200cno'
        resp = self._subir(descripcion=texto)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Evidencia.objects.get().descripcion, texto)

    def test_unifica_los_saltos_de_linea_de_windows(self, _tsa):
        self._subir(descripcion='uno\r\ndos\rtres')
        self.assertEqual(Evidencia.objects.get().descripcion, 'uno\ndos\ntres')

    def test_quita_espacios_y_sin_descripcion_guarda_vacio(self, _tsa):
        self._subir(descripcion='  Grieta  \n')
        self._subir()
        self.assertEqual(
            list(Evidencia.objects.order_by('pk').values_list('descripcion', flat=True)),
            ['Grieta', ''],
        )

    def test_normaliza_a_nfc_y_mide_en_caracteres_compuestos(self, _tsa):
        # «ñ» descompuesta (n + tilde combinante) cuenta como un carácter
        resp = self._subir(descripcion='n\u0303' * DESCRIPCION_MAX)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Evidencia.objects.get().descripcion, '\u00f1' * DESCRIPCION_MAX)

    def test_rechaza_lo_que_no_es_texto(self, _tsa):
        # Solo llega con un cuerpo JSON, que no puede llevar la foto: se prueba la validación
        for valor in (['Grieta'], {'a': 1}, 3):
            with self.subTest(valor=valor):
                self.assertEqual(_descripcion(valor), ('', 'descripcion debe ser texto.'))

    def test_rechaza_un_sustituto_suelto(self, _tsa):
        # Con un cuerpo JSON llegaría y Postgres no puede codificarlo: sería un 500
        self.assertIn('caracteres de control', _descripcion('a\ud800b')[1])


class DescripcionEnElModeloTest(TestCase):
    """La misma regla en full_clean, para que el admin no se la salte."""

    def setUp(self):
        admin = User.objects.create_user(username='admin', password='Pass1234!', role=User.Role.ADMIN)
        prop = Propiedad.objects.create(owner=admin, nombre='Prop', direccion='C1')
        self.insp = Inspeccion.objects.create(propiedad=prop)

    def _evidencia(self, descripcion):
        return Evidencia(inspeccion=self.insp, foto='evidencias/x.jpg', descripcion=descripcion)

    def test_rechaza_trampas_y_textos_largos(self):
        for malo in ['a\u202eb', 'a\x00b', 'x' * (DESCRIPCION_MAX + 1)]:
            with self.subTest(malo=repr(malo[:12])):
                with self.assertRaises(ValidationError) as ctx:
                    self._evidencia(malo).full_clean()
                self.assertIn('descripcion', ctx.exception.message_dict)

    def test_admite_los_saltos_del_textarea_del_admin(self):
        self._evidencia('uno\r\ndos\tconsigo').full_clean()


class DescripcionEnElActaTest(TestCase):
    def setUp(self):
        admin = User.objects.create_user(username='admin', password='Pass1234!', role=User.Role.ADMIN)
        prop = Propiedad.objects.create(owner=admin, nombre='Prop', direccion='C1')
        self.insp = Inspeccion.objects.create(propiedad=prop)

    def _texto(self, descripcion):
        from .services.pdf_generator import _texto_evidencia
        ev = Evidencia.objects.create(inspeccion=self.insp, foto='evidencias/x.jpg', descripcion=descripcion)
        return _texto_evidencia(ev)

    def test_conserva_los_saltos_de_linea(self):
        self.assertEqual(self._texto('uno\ndos\r\ntres'), 'uno<br/>dos<br/>tres')

    def test_quita_los_invisibles_guardados_antes_de_la_regla(self):
        # Filas anteriores a la validación: el acta no debe invertir ni esconder texto
        self.assertEqual(self._texto('ab\u202ec\u200bd\U000e0041'), 'abcd')

    def test_sigue_escapando_el_marcado(self):
        self.assertEqual(self._texto('<b>\nx'), '&lt;b&gt;<br/>x')
