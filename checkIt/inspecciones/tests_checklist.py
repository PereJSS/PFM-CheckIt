"""Checklist de cada inspección (#19): copia fija de la plantilla y reglas de completado.

Sesión 1, parte de modelo y servicio. La API va en tests_checklist_api.py.
"""
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import RestrictedError
from django.test import TestCase

from propiedades.models import PlantillaChecklist, Propiedad
from users.models import User

from .models import ChecklistInspeccion, Evidencia, Inspeccion, PuntoInspeccion
from .services.checklist import (
    checklist_canonico,
    faltas_para_completar,
    instanciar_checklist,
    plantilla_efectiva,
    resumen,
)

ESTRUCTURA = {'estancias': [
    {'nombre': 'Cocina', 'puntos': [
        {'texto': 'Encimera', 'requiere_foto': False},
        {'texto': 'Vista general', 'requiere_foto': True},
    ]},
    {'nombre': 'Baño', 'puntos': [{'texto': 'Grifo', 'requiere_foto': False}]},
]}


class BaseChecklist(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='admin', password='Pass1234!', role=User.Role.ADMIN)
        self.otro = User.objects.create_user(username='otro', password='Pass1234!', role=User.Role.ADMIN)
        self.plantilla = PlantillaChecklist.objects.create(
            owner=self.admin, nombre='Vivienda', estructura=ESTRUCTURA,
        )
        self.prop = Propiedad.objects.create(owner=self.admin, nombre='Piso', direccion='C1')
        self.insp = Inspeccion.objects.create(propiedad=self.prop)

    def _puntos(self, inspeccion=None):
        return {
            p.texto: p for p in PuntoInspeccion.objects.filter(inspeccion=inspeccion or self.insp)
        }

    def _foto(self, punto):
        return Evidencia.objects.create(inspeccion=self.insp, foto='evidencias/x.jpg', punto=punto)


class PlantillaEfectivaTest(BaseChecklist):
    def test_la_asignada_a_la_propiedad(self):
        PlantillaChecklist.objects.create(
            owner=self.admin, nombre='Defecto', estructura=ESTRUCTURA, por_defecto=True,
        )
        self.prop.plantilla_checklist = self.plantilla
        self.prop.save()
        self.assertEqual(plantilla_efectiva(self.prop), self.plantilla)

    def test_sin_asignar_usa_la_por_defecto_del_cliente(self):
        defecto = PlantillaChecklist.objects.create(
            owner=self.admin, nombre='Defecto', estructura=ESTRUCTURA, por_defecto=True,
        )
        PlantillaChecklist.objects.create(
            owner=self.otro, nombre='Ajena', estructura=ESTRUCTURA, por_defecto=True,
        )
        self.assertEqual(plantilla_efectiva(self.prop), defecto)

    def test_sin_asignar_ni_defecto_no_hay(self):
        self.assertIsNone(plantilla_efectiva(self.prop))

    def test_ignora_una_asignada_de_otro_cliente(self):
        ajena = PlantillaChecklist.objects.create(owner=self.otro, nombre='Ajena', estructura=ESTRUCTURA)
        Propiedad.objects.filter(pk=self.prop.pk).update(plantilla_checklist=ajena)
        self.prop.refresh_from_db()
        self.assertIsNone(plantilla_efectiva(self.prop))


class InstanciarChecklistTest(BaseChecklist):
    def test_copia_estancias_y_puntos_en_orden(self):
        checklist = instanciar_checklist(self.insp, self.plantilla)
        self.assertEqual(checklist.plantilla_nombre, 'Vivienda')
        self.assertEqual(checklist.plantilla_version, 1)
        estancias = list(checklist.estancias.all())
        self.assertEqual([e.nombre for e in estancias], ['Cocina', 'Baño'])
        self.assertEqual(
            [(p.texto, p.requiere_foto, p.orden) for p in estancias[0].puntos.all()],
            [('Encimera', False, 0), ('Vista general', True, 1)],
        )
        self.assertTrue(all(p.resultado is None for p in self._puntos().values()))

    def test_usa_la_plantilla_efectiva_si_no_se_indica(self):
        self.prop.plantilla_checklist = self.plantilla
        self.prop.save()
        self.assertIsNotNone(instanciar_checklist(self.insp))

    def test_sin_plantilla_no_crea_nada(self):
        self.assertIsNone(instanciar_checklist(self.insp))
        self.assertFalse(ChecklistInspeccion.objects.exists())

    def test_es_idempotente(self):
        primera = instanciar_checklist(self.insp, self.plantilla)
        self.assertEqual(instanciar_checklist(self.insp, self.plantilla), primera)
        self.assertEqual(PuntoInspeccion.objects.count(), 3)

    def test_si_otra_llamada_la_crea_a_la_vez_devuelve_esa(self):
        primera = instanciar_checklist(self.insp, self.plantilla)
        # La comprobación previa no la ve (llegaron a la vez): el OneToOne decide
        with patch('inspecciones.services.checklist._checklist_de', side_effect=[None, primera]):
            with transaction.atomic():
                segunda = instanciar_checklist(self.insp, self.plantilla)
                # La transacción de quien llama sigue usable
                self.assertTrue(Inspeccion.objects.filter(pk=self.insp.pk).exists())
        self.assertEqual(segunda, primera)
        self.assertEqual(PuntoInspeccion.objects.count(), 3)

    def test_cambiar_o_borrar_la_plantilla_no_toca_la_copia(self):
        instanciar_checklist(self.insp, self.plantilla)
        self.plantilla.estructura = {'estancias': [{'nombre': 'Salón', 'puntos': [{'texto': 'Sofá'}]}]}
        self.plantilla.save()
        self.plantilla.delete()
        checklist = ChecklistInspeccion.objects.get()
        self.assertIsNone(checklist.plantilla)
        self.assertEqual(checklist.plantilla_nombre, 'Vivienda')
        self.assertEqual(set(self._puntos()), {'Encimera', 'Vista general', 'Grifo'})


class FaltasYResumenTest(BaseChecklist):
    def setUp(self):
        super().setUp()
        instanciar_checklist(self.insp, self.plantilla)
        self.p = self._puntos()

    def _marcar(self, texto, resultado):
        PuntoInspeccion.objects.filter(pk=self.p[texto].pk).update(resultado=resultado)

    def test_sin_marcar(self):
        motivos = {(f['texto'], f['motivo']) for f in faltas_para_completar(self.insp.pk)}
        self.assertEqual(motivos, {
            ('Encimera', 'sin_marcar'), ('Vista general', 'sin_marcar'), ('Grifo', 'sin_marcar'),
        })

    def test_dano_y_requiere_foto_exigen_foto(self):
        self._marcar('Encimera', PuntoInspeccion.Resultado.DANO)
        self._marcar('Vista general', PuntoInspeccion.Resultado.OK)
        self._marcar('Grifo', PuntoInspeccion.Resultado.OK)
        faltas = faltas_para_completar(self.insp.pk)
        self.assertEqual(
            {(f['texto'], f['motivo']) for f in faltas},
            {('Encimera', 'falta_foto'), ('Vista general', 'falta_foto')},
        )
        self.assertEqual(faltas[0]['estancia'], 'Cocina')
        self.assertEqual(faltas[0]['punto'], self.p['Encimera'].pk)

    def test_con_fotos_o_no_aplica_no_falta_nada(self):
        self._marcar('Encimera', PuntoInspeccion.Resultado.DANO)
        self._marcar('Vista general', PuntoInspeccion.Resultado.NO_APLICA)
        self._marcar('Grifo', PuntoInspeccion.Resultado.OK)
        self._foto(self.p['Encimera'])
        self.assertEqual(faltas_para_completar(self.insp.pk), [])

    def test_sin_checklist_no_falta_nada(self):
        otra = Inspeccion.objects.create(propiedad=self.prop)
        self.assertEqual(faltas_para_completar(otra.pk), [])
        self.assertIsNone(resumen(otra.pk))

    def test_resumen(self):
        self._marcar('Encimera', PuntoInspeccion.Resultado.DANO)
        self._marcar('Grifo', PuntoInspeccion.Resultado.OK)
        self.assertEqual(resumen(self.insp.pk), {
            'puntos': 3, 'ok': 1, 'danos': 1, 'no_aplica': 0, 'sin_marcar': 1,
        })

    def test_canonico_es_determinista(self):
        self._marcar('Encimera', PuntoInspeccion.Resultado.DANO)
        ev2 = self._foto(self.p['Encimera'])
        ev1 = self._foto(self.p['Encimera'])
        canonico = checklist_canonico(self.insp)
        self.assertEqual(canonico['plantilla'], 'Vivienda')
        self.assertEqual(canonico['plantilla_version'], 1)
        self.assertEqual([e['nombre'] for e in canonico['estancias']], ['Cocina', 'Baño'])
        encimera = canonico['estancias'][0]['puntos'][0]
        self.assertEqual(encimera['texto'], 'Encimera')
        self.assertEqual(encimera['resultado'], 'DANO')
        self.assertEqual(encimera['evidencias'], sorted([ev1.pk, ev2.pk]))
        self.assertEqual(checklist_canonico(self.insp), canonico)
        self.assertIsNone(checklist_canonico(Inspeccion.objects.create(propiedad=self.prop)))


class IntegridadTest(BaseChecklist):
    def setUp(self):
        super().setUp()
        instanciar_checklist(self.insp, self.plantilla)
        self.p = self._puntos()

    def test_la_bd_rechaza_un_resultado_desconocido(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            PuntoInspeccion.objects.filter(pk=self.p['Grifo'].pk).update(resultado='ROTO')

    def test_no_se_borra_un_punto_con_fotos(self):
        self._foto(self.p['Grifo'])
        with self.assertRaises(RestrictedError):
            self.p['Grifo'].delete()

    def test_una_foto_no_documenta_puntos_de_otra_inspeccion(self):
        otra = Inspeccion.objects.create(propiedad=self.prop)
        foto = Evidencia(inspeccion=otra, foto='evidencias/x.jpg', punto=self.p['Grifo'])
        with self.assertRaises(ValidationError) as ctx:
            foto.full_clean()
        self.assertIn('punto', ctx.exception.message_dict)
        Evidencia(inspeccion=self.insp, foto='evidencias/x.jpg', punto=self.p['Grifo']).full_clean()

    def test_borrar_la_propiedad_o_el_cliente_lo_borra_todo(self):
        self._foto(self.p['Grifo'])
        self.prop.delete()
        self.assertFalse(PuntoInspeccion.objects.exists())
        self.assertFalse(Evidencia.objects.exists())
        self.admin.delete()
        self.assertFalse(PlantillaChecklist.objects.exists())

    def test_borrar_la_inspeccion_borra_su_checklist_y_sus_fotos(self):
        self._foto(self.p['Grifo'])
        self.insp.delete()
        self.assertFalse(ChecklistInspeccion.objects.exists())
        self.assertFalse(PuntoInspeccion.objects.exists())
        self.assertFalse(Evidencia.objects.exists())
