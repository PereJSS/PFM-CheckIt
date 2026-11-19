"""Checklist en las salidas (#19, sesión 2): acta PDF, paquete probatorio, reapertura y
webhook saliente. Declarado por el operario y fuera del manifiesto sellado."""
import json
import os
import tempfile

from unittest.mock import patch

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from integraciones.models import EventoSaliente
from integraciones.salientes import construir_datos
from integraciones.tests import IP_PUBLICA, BaseSalientesTest
from propiedades.models import PlantillaChecklist, Propiedad
from users.models import User

from .models import Evidencia, Inspeccion, PuntoInspeccion
from .services.checklist import instanciar_checklist
from .tests_paquete import _abrir, _ConExpediente, _construir
from .tests_sellos import _texto

ESTRUCTURA = {'estancias': [
    {'nombre': 'Cocina', 'puntos': [{'texto': 'Encimera'}, {'texto': 'Vista general', 'requiere_foto': True}]},
    {'nombre': 'Baño', 'puntos': [{'texto': 'Grifo'}]},
]}


def _marcar(inspeccion, texto, resultado, nota=''):
    PuntoInspeccion.objects.filter(inspeccion=inspeccion, texto=texto).update(resultado=resultado, nota=nota)
    return PuntoInspeccion.objects.get(inspeccion=inspeccion, texto=texto)


class _ConChecklist:
    def _crear_checklist(self, inspeccion, owner):
        self.plantilla = PlantillaChecklist.objects.create(owner=owner, nombre='Vivienda', estructura=ESTRUCTURA)
        instanciar_checklist(inspeccion, self.plantilla)


class ActaChecklistTest(_ConChecklist, TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='admin', password='Pass1234!', role=User.Role.ADMIN)
        self.prop = Propiedad.objects.create(owner=self.admin, nombre='Piso', direccion='C1')
        self.insp = Inspeccion.objects.create(propiedad=self.prop)

    def _seccion(self, numeros=None):
        from .services.pdf_generator import _seccion_checklist, _styles
        return _texto(_seccion_checklist(self.insp, _styles(), numeros or {}))

    def test_sin_checklist_no_hay_seccion(self):
        from .services.pdf_generator import _seccion_checklist, _styles
        self.assertEqual(_seccion_checklist(self.insp, _styles(), {}), [])

    def test_resumen_puntos_y_leyenda(self):
        self._crear_checklist(self.insp, self.admin)
        encimera = _marcar(self.insp, 'Encimera', 'DANO', nota='Quemadura <b>grande</b> & fea')
        _marcar(self.insp, 'Grifo', 'NO_APLICA')
        foto = Evidencia.objects.create(inspeccion=self.insp, foto='evidencias/x.jpg', punto=encimera)
        texto = self._seccion({foto.pk: 2})
        self.assertIn('Checklist', texto)
        self.assertIn('Vivienda', texto)
        self.assertIn('3 puntos: 0 OK, 1 con daño, 1 no aplica, 1 sin marcar', texto)
        self.assertIn('no forma parte del manifiesto sellado', texto)
        for fragmento in ('Cocina', 'Baño', 'Encimera', 'Vista general', 'Grifo', 'Daño', 'No aplica', 'Sin marcar', '#2'):
            self.assertIn(fragmento, texto)
        # La nota es texto del operario: el marcado no se interpreta
        self.assertIn('Quemadura <b>grande</b> & fea', texto)

    def test_la_nota_no_inyecta_marcado_en_el_paragraph(self):
        from reportlab.platypus import Table
        from .services.pdf_generator import _seccion_checklist, _styles
        self._crear_checklist(self.insp, self.admin)
        _marcar(self.insp, 'Encimera', 'DANO', nota='<font color="red">x</font>')
        tablas = [f for f in _seccion_checklist(self.insp, _styles(), {}) if isinstance(f, Table)]
        celdas = [c for t in tablas for fila in t._cellvalues for c in fila]
        self.assertTrue(any('&lt;font' in getattr(c, 'text', '') for c in celdas))

    def test_la_evidencia_dice_su_punto(self):
        from .services.pdf_generator import _texto_evidencia
        self._crear_checklist(self.insp, self.admin)
        grifo = PuntoInspeccion.objects.get(inspeccion=self.insp, texto='Grifo')
        foto = Evidencia.objects.create(inspeccion=self.insp, foto='evidencias/x.jpg', punto=grifo, estancia='Baño')
        texto = _texto_evidencia(foto)
        self.assertIn('Baño · Grifo', texto)
        self.assertNotIn('Estancia:', texto)

    def test_una_nota_con_cientos_de_saltos_no_rompe_el_acta(self):
        from .services.pdf_generator import generar_informe_base
        self._crear_checklist(self.insp, self.admin)
        _marcar(self.insp, 'Encimera', 'DANO', nota='x\n' * 500)
        _marcar(self.insp, 'Grifo', 'DANO', nota='palabra ' * 125)
        with tempfile.TemporaryDirectory() as d:
            ruta = generar_informe_base(self.insp, os.path.join(d, 'a.pdf'))
            self.assertGreater(os.path.getsize(ruta), 0)

    def test_limpia_los_textos_cortos_guardados_sin_validar(self):
        self._crear_checklist(self.insp, self.admin)
        PuntoInspeccion.objects.filter(inspeccion=self.insp, texto='Grifo').update(texto='Gri\u202efo')
        self.insp.checklist.__class__.objects.filter(pk=self.insp.checklist.pk).update(plantilla_nombre='Vi\u200bvienda')
        texto = self._seccion()
        self.assertIn('Grifo', texto)
        self.assertIn('Vivienda', texto)
        self.assertNotIn('\u202e', texto)
        self.assertNotIn('\u200b', texto)

    def test_el_acta_se_genera_con_400_puntos(self):
        from .services.pdf_generator import generar_informe_base
        estructura = {'estancias': [
            {'nombre': f'Estancia {i}', 'puntos': [{'texto': f'Punto {j}'} for j in range(40)]} for i in range(10)
        ]}
        plantilla = PlantillaChecklist.objects.create(owner=self.admin, nombre='Grande', estructura=estructura)
        instanciar_checklist(self.insp, plantilla)
        with tempfile.TemporaryDirectory() as d:
            ruta = generar_informe_base(self.insp, os.path.join(d, 'a.pdf'))
            self.assertGreater(os.path.getsize(ruta), 0)


class PaqueteChecklistTest(_ConExpediente, _ConChecklist, TestCase):
    def setUp(self):
        self._crear_expediente()

    def _zip(self):
        return _abrir(_construir(self.inspeccion))

    def test_sin_checklist_el_expediente_no_lo_inventa(self):
        datos = json.loads(self._zip().read('expediente.json'))
        self.assertIsNone(datos['checklist'])
        self.assertEqual([f['punto'] for f in datos['evidencias']], [None, None])

    def test_expediente_con_checklist_y_punto_de_cada_foto(self):
        self._crear_checklist(self.inspeccion, self.admin)
        encimera = _marcar(self.inspeccion, 'Encimera', 'DANO', nota='Quemadura')
        Evidencia.objects.filter(pk=self.fotos[0].pk).update(punto=encimera)
        z = self._zip()
        datos = json.loads(z.read('expediente.json'))
        checklist = datos['checklist']
        self.assertFalse(checklist['sellado'])
        self.assertIn('manifiesto', checklist['nota'])
        self.assertEqual(checklist['plantilla'], 'Vivienda')
        primero = checklist['estancias'][0]['puntos'][0]
        self.assertEqual((primero['texto'], primero['resultado']), ('Encimera', 'DANO'))
        self.assertEqual(primero['evidencias'], [self.fotos[0].pk])
        self.assertEqual([f['punto'] for f in datos['evidencias']], [encimera.pk, None])
        self.assertIn('checklist', z.read('LEEME.txt').decode('utf-8').lower())


class ReaperturaChecklistTest(_ConExpediente, _ConChecklist, TestCase):
    def test_al_reabrir_se_archiva_el_estado_del_checklist(self):
        from .services.manifiesto import reabrir_manifiesto
        self._crear_expediente()
        self._crear_checklist(self.inspeccion, self.admin)
        _marcar(self.inspeccion, 'Grifo', 'OK')
        reabrir_manifiesto(self.inspeccion.pk)
        self.inspeccion.refresh_from_db()
        archivado = self.inspeccion.manifiestos_anteriores[-1]
        self.assertEqual(archivado['checklist']['plantilla'], 'Vivienda')
        grifo = archivado['checklist']['estancias'][1]['puntos'][0]
        self.assertEqual(grifo['resultado'], 'OK')

    def test_sin_checklist_no_se_archiva_la_clave(self):
        from .services.manifiesto import reabrir_manifiesto
        self._crear_expediente()
        reabrir_manifiesto(self.inspeccion.pk)
        self.inspeccion.refresh_from_db()
        self.assertNotIn('checklist', self.inspeccion.manifiestos_anteriores[-1])


class WebhookChecklistTest(_ConChecklist, TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='admin', password='Pass1234!', role=User.Role.ADMIN)
        self.prop = Propiedad.objects.create(owner=self.admin, nombre='Piso', direccion='C1')
        self.insp = Inspeccion.objects.create(propiedad=self.prop)

    def test_sin_checklist_no_hay_bloque(self):
        self.assertNotIn('checklist', construir_datos('inspeccion.completada', self.insp))

    def test_recuentos_y_danos_sin_notas(self):
        self._crear_checklist(self.insp, self.admin)
        encimera = _marcar(self.insp, 'Encimera', 'DANO', nota='Datos personales del inquilino')
        _marcar(self.insp, 'Vista general', 'OK')
        foto = Evidencia.objects.create(inspeccion=self.insp, foto='evidencias/x.jpg', punto=encimera)
        bloque = construir_datos('inspeccion.completada', self.insp)['checklist']
        self.assertEqual(bloque, {
            'plantilla': 'Vivienda', 'plantilla_version': 1,
            'puntos': 3, 'ok': 1, 'danos': 1, 'no_aplica': 0, 'sin_marcar': 1,
            'lista_danos': [{'punto_id': encimera.pk, 'estancia': 'Cocina', 'punto': 'Encimera', 'evidencias': [foto.pk]}],
        })
        self.assertNotIn('Datos personales', json.dumps(bloque))

    def test_las_consultas_no_crecen_con_los_danos(self):
        self._crear_checklist(self.insp, self.admin)
        _marcar(self.insp, 'Encimera', 'DANO')
        with CaptureQueriesContext(connection) as uno:
            construir_datos('inspeccion.completada', self.insp)
        _marcar(self.insp, 'Vista general', 'DANO')
        _marcar(self.insp, 'Grifo', 'DANO')
        for texto in ('Vista general', 'Grifo'):
            Evidencia.objects.create(
                inspeccion=self.insp, foto='evidencias/x.jpg',
                punto=PuntoInspeccion.objects.get(inspeccion=self.insp, texto=texto),
            )
        with CaptureQueriesContext(connection) as tres:
            bloque = construir_datos('inspeccion.completada', self.insp)['checklist']
        self.assertEqual(len(bloque['lista_danos']), 3)
        self.assertEqual(len(tres), len(uno))


@patch('integraciones.salientes.socket.getaddrinfo', return_value=IP_PUBLICA)
class WebhookChecklistAlCompletarTest(BaseSalientesTest, _ConChecklist):
    def test_el_evento_de_completar_lleva_el_checklist_definitivo(self, _dns):
        self._crear_checklist(self.inspeccion, self.admin)
        for texto in ('Encimera', 'Grifo'):
            _marcar(self.inspeccion, texto, 'OK')
        _marcar(self.inspeccion, 'Vista general', 'NO_APLICA', nota='Nota privada')
        with patch('integraciones.tasks.entregar_webhook.apply_async'):
            self._completar()
        datos = EventoSaliente.objects.get(tipo='inspeccion.completada').datos
        self.assertEqual(
            {k: datos['checklist'][k] for k in ('puntos', 'ok', 'no_aplica', 'sin_marcar')},
            {'puntos': 3, 'ok': 2, 'no_aplica': 1, 'sin_marcar': 0},
        )
        self.assertNotIn('Nota privada', json.dumps(datos))

