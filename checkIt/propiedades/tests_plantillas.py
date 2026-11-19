"""Plantillas de checklist (#19): estructura validada, una por defecto por cliente.

Caracteres invisibles siempre con escapes, nunca literales.
"""
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from users.models import User

from .models import PlantillaChecklist, Propiedad, normalizar_estructura


def _estructura(*estancias):
    return {'estancias': [
        {'nombre': nombre, 'puntos': [{'texto': t} for t in puntos]} for nombre, puntos in estancias
    ]}


class NormalizarEstructuraTest(TestCase):
    def test_normaliza_textos_y_completa_requiere_foto(self):
        estructura = {'estancias': [{'nombre': '  Cocina ', 'puntos': [
            {'texto': ' Encimera '}, {'texto': 'Vista general', 'requiere_foto': True},
        ]}]}
        self.assertEqual(normalizar_estructura(estructura), {'estancias': [{'nombre': 'Cocina', 'puntos': [
            {'texto': 'Encimera', 'requiere_foto': False},
            {'texto': 'Vista general', 'requiere_foto': True},
        ]}]})

    def test_nfc(self):
        resultado = normalizar_estructura(_estructura(('Baño', ['Grifo'])))
        self.assertEqual(resultado['estancias'][0]['nombre'], 'Baño')

    def test_rechaza_estructuras_no_validas(self):
        cuarenta = [f'P{i}' for i in range(40)]
        casos = {
            'no es objeto': ['Cocina'],
            'sin estancias': {},
            'estancias vacías': {'estancias': []},
            'estancias no lista': {'estancias': 'Cocina'},
            '41 estancias': _estructura(*[(f'E{i}', ['P']) for i in range(41)]),
            'estancia sin puntos': _estructura(('Cocina', [])),
            '41 puntos': _estructura(('Cocina', [f'P{i}' for i in range(41)])),
            'más de 400 puntos': _estructura(*[(f'E{i}', cuarenta) for i in range(11)]),
            'nombre vacío': _estructura(('   ', ['P'])),
            'nombre largo': _estructura(('x' * 61, ['P'])),
            'nombre repetido': _estructura(('Cocina', ['P']), ('COCINA', ['P'])),
            'nombre con control de dirección': _estructura(('Coc‮ina', ['P'])),
            'nombre no texto': {'estancias': [{'nombre': 3, 'puntos': [{'texto': 'P'}]}]},
            'punto vacío': _estructura(('Cocina', [' '])),
            'punto largo': _estructura(('Cocina', ['x' * 201])),
            'punto con salto': _estructura(('Cocina', ['a\nb'])),
            'punto repetido': _estructura(('Cocina', ['Grifo', 'grifo'])),
            'requiere_foto no booleano': {'estancias': [
                {'nombre': 'Cocina', 'puntos': [{'texto': 'P', 'requiere_foto': 'sí'}]}]},
            # True == 1 en Python: un 1 no debe colarse como booleano
            'requiere_foto 1': {'estancias': [
                {'nombre': 'Cocina', 'puntos': [{'texto': 'P', 'requiere_foto': 1}]}]},
            'clave extra en la raíz': {**_estructura(('Cocina', ['P'])), 'x': 1},
            'clave extra en la estancia': {'estancias': [
                {'nombre': 'Cocina', 'puntos': [{'texto': 'P'}], 'x': 1}]},
            'clave extra en el punto': {'estancias': [
                {'nombre': 'Cocina', 'puntos': [{'texto': 'P', 'x': 1}]}]},
        }
        for caso, estructura in casos.items():
            with self.subTest(caso):
                with self.assertRaises(ValidationError):
                    normalizar_estructura(estructura)

    def test_acepta_los_maximos(self):
        cuarenta = [f'P{i}' for i in range(40)]
        normalizar_estructura(_estructura(*[(f'E{i}', cuarenta) for i in range(10)]))
        normalizar_estructura(_estructura(('x' * 60, ['y' * 200])))

    def test_la_misma_estancia_puede_repetir_puntos_de_otra(self):
        normalizar_estructura(_estructura(('Baño 1', ['Grifo']), ('Baño 2', ['Grifo'])))


class PlantillaChecklistModelTest(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='admin', password='Pass1234!', role=User.Role.ADMIN)
        self.otro = User.objects.create_user(username='otro', password='Pass1234!', role=User.Role.ADMIN)

    def _plantilla(self, owner=None, nombre='Vivienda', **extra):
        return PlantillaChecklist.objects.create(
            owner=owner or self.admin, nombre=nombre,
            estructura=_estructura(('Cocina', ['Encimera'])), **extra,
        )

    def test_full_clean_valida_la_estructura(self):
        plantilla = PlantillaChecklist(owner=self.admin, nombre='Mala', estructura={'estancias': []})
        with self.assertRaises(ValidationError) as ctx:
            plantilla.full_clean()
        self.assertIn('estructura', ctx.exception.message_dict)

    def test_nombre_unico_por_cliente_sin_distinguir_mayusculas(self):
        self._plantilla()
        self._plantilla(owner=self.otro)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._plantilla(nombre='VIVIENDA')

    def test_una_sola_por_defecto_por_cliente(self):
        self._plantilla(por_defecto=True)
        self._plantilla(owner=self.otro, por_defecto=True)
        self._plantilla(nombre='Otra')
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._plantilla(nombre='Tercera', por_defecto=True)

    def test_empieza_en_la_version_1(self):
        self.assertEqual(self._plantilla().version, 1)

    def test_borrarla_deja_la_propiedad_sin_plantilla(self):
        plantilla = self._plantilla()
        prop = Propiedad.objects.create(
            owner=self.admin, nombre='Piso', direccion='C1', plantilla_checklist=plantilla,
        )
        plantilla.delete()
        prop.refresh_from_db()
        self.assertIsNone(prop.plantilla_checklist)
