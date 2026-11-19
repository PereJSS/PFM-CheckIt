"""API de plantillas de checklist (#19) y su asignación a propiedades."""
from unittest.mock import patch

from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework import status
from rest_framework.test import APITestCase

from users.models import User

from .models import PlantillaChecklist, Propiedad

URL = '/api/v1/plantillas-checklist/'
ESTRUCTURA = {'estancias': [{'nombre': 'Cocina', 'puntos': [{'texto': 'Encimera'}]}]}


class BasePlantillas(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='admin', password='Pass1234!', role=User.Role.ADMIN)
        self.otro = User.objects.create_user(username='otro', password='Pass1234!', role=User.Role.ADMIN)
        self.op = User.objects.create_user(
            username='op', password='Pass1234!', role=User.Role.OPERARIO, administrador=self.admin,
        )
        self.client.force_authenticate(user=self.admin)

    def _plantilla(self, owner=None, nombre='Vivienda', **extra):
        return PlantillaChecklist.objects.create(
            owner=owner or self.admin, nombre=nombre, estructura=ESTRUCTURA, **extra,
        )


class PlantillasCrudTest(BasePlantillas):
    def test_un_operario_no_entra(self):
        self.client.force_authenticate(user=self.op)
        self.assertEqual(self.client.get(URL).status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(
            self.client.post(URL, {'nombre': 'X', 'estructura': ESTRUCTURA}, format='json').status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_lista_solo_las_suyas_con_cuantas_propiedades_la_usan(self):
        mia = self._plantilla()
        self._plantilla(owner=self.otro, nombre='Ajena')
        Propiedad.objects.create(owner=self.admin, nombre='P', direccion='C', plantilla_checklist=mia)
        resp = self.client.get(URL)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual([p['nombre'] for p in resp.data], ['Vivienda'])
        self.assertEqual(resp.data[0]['propiedades_count'], 1)

    def test_la_de_otro_cliente_da_404(self):
        ajena = self._plantilla(owner=self.otro)
        for metodo in ('get', 'patch', 'delete'):
            with self.subTest(metodo=metodo):
                resp = getattr(self.client, metodo)(f'{URL}{ajena.pk}/', {'version': 1}, format='json')
                self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(self.client.post(f'{URL}{ajena.pk}/duplicar/').status_code, status.HTTP_404_NOT_FOUND)

    def test_crear_guarda_la_estructura_normalizada(self):
        resp = self.client.post(URL, {
            'nombre': '  Vivienda  ', 'version': 7, 'estructura': {'estancias': [
                {'nombre': ' Cocina ', 'puntos': [{'texto': ' Encimera '}]},
            ]},
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        plantilla = PlantillaChecklist.objects.get()
        self.assertEqual(plantilla.owner, self.admin)
        self.assertEqual(plantilla.nombre, 'Vivienda')
        self.assertEqual(plantilla.version, 1)
        self.assertEqual(plantilla.estructura, {'estancias': [
            {'nombre': 'Cocina', 'puntos': [{'texto': 'Encimera', 'requiere_foto': False}]},
        ]})
        self.assertEqual(resp.data['version'], 1)

    def test_crear_con_estructura_no_valida_da_400(self):
        resp = self.client.post(URL, {'nombre': 'X', 'estructura': {'estancias': []}}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('estructura', resp.data)
        self.assertFalse(PlantillaChecklist.objects.exists())

    def test_nombre_vacio_o_con_trampas_da_400(self):
        for nombre in ['   ', 'Vi‮vienda', 'x' * 101]:
            with self.subTest(nombre=repr(nombre[:10])):
                resp = self.client.post(URL, {'nombre': nombre, 'estructura': ESTRUCTURA}, format='json')
                self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn('nombre', resp.data)

    def test_nombre_repetido_da_400(self):
        self._plantilla()
        resp = self.client.post(URL, {'nombre': 'VIVIENDA', 'estructura': ESTRUCTURA}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('nombre', resp.data)

    def test_marcar_por_defecto_desmarca_la_anterior(self):
        vieja = self._plantilla(por_defecto=True)
        ajena = self._plantilla(owner=self.otro, nombre='Ajena', por_defecto=True)
        resp = self.client.post(URL, {'nombre': 'Nueva', 'estructura': ESTRUCTURA, 'por_defecto': True}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        vieja.refresh_from_db()
        ajena.refresh_from_db()
        self.assertFalse(vieja.por_defecto)
        self.assertTrue(ajena.por_defecto)

    def test_borrar_deja_las_propiedades_sin_plantilla(self):
        plantilla = self._plantilla()
        prop = Propiedad.objects.create(owner=self.admin, nombre='P', direccion='C', plantilla_checklist=plantilla)
        self.assertEqual(self.client.delete(f'{URL}{plantilla.pk}/').status_code, status.HTTP_204_NO_CONTENT)
        prop.refresh_from_db()
        self.assertIsNone(prop.plantilla_checklist)


class PlantillasEdicionTest(BasePlantillas):
    def setUp(self):
        super().setUp()
        self.plantilla = self._plantilla()
        self.url = f'{URL}{self.plantilla.pk}/'

    def test_sin_version_da_400(self):
        resp = self.client.patch(self.url, {'nombre': 'Otra'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('version', resp.data)

    def test_con_version_vieja_da_409_y_no_cambia_nada(self):
        PlantillaChecklist.objects.filter(pk=self.plantilla.pk).update(version=2)
        resp = self.client.patch(self.url, {'nombre': 'Otra', 'por_defecto': True, 'version': 1}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT)
        self.assertIn('error', resp.data)
        self.assertEqual(resp.data['version'], 2)
        self.plantilla.refresh_from_db()
        self.assertEqual(self.plantilla.nombre, 'Vivienda')
        self.assertFalse(self.plantilla.por_defecto)

    def test_editar_sube_la_version_y_normaliza(self):
        resp = self.client.patch(self.url, {
            'version': 1,
            'estructura': {'estancias': [{'nombre': ' Baño ', 'puntos': [{'texto': 'Grifo', 'requiere_foto': True}]}]},
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertEqual(resp.data['version'], 2)
        self.plantilla.refresh_from_db()
        self.assertEqual(self.plantilla.version, 2)
        self.assertEqual(self.plantilla.estructura['estancias'][0]['nombre'], 'Baño')

    def test_put_tambien_exige_version(self):
        resp = self.client.put(self.url, {'nombre': 'Otra', 'estructura': ESTRUCTURA}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        resp = self.client.put(self.url, {'nombre': 'Otra', 'estructura': ESTRUCTURA, 'version': 1}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)

    def test_version_mal_tipada_da_400(self):
        for valor in (True, 1.5, 'uno'):
            with self.subTest(valor=valor):
                resp = self.client.patch(self.url, {'nombre': 'Otra', 'version': valor}, format='json')
                self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_carrera_por_el_nombre_la_decide_la_bd(self):
        # Las dos peticiones pasaron la validación a la vez: el índice único responde 400
        self._plantilla(nombre='Coche')
        with patch('propiedades.serializers.PlantillaChecklistSerializer.validate_nombre', side_effect=lambda v: v):
            resp = self.client.patch(self.url, {'version': 1, 'nombre': 'Coche'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('nombre', resp.data)

    def test_solo_admite_json(self):
        # En multipart, un booleano ausente cuenta como false: desmarcaría la por defecto
        resp = self.client.put(self.url, {'nombre': 'Otra', 'version': 1}, format='multipart')
        self.assertEqual(resp.status_code, status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)

    def test_estructura_no_valida_da_400(self):
        resp = self.client.patch(self.url, {'version': 1, 'estructura': {'estancias': 'x'}}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('estructura', resp.data)

    def test_renombrar_a_una_existente_da_400(self):
        self._plantilla(nombre='Coche')
        resp = self.client.patch(self.url, {'version': 1, 'nombre': 'coche'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('nombre', resp.data)

    def test_marcar_por_defecto_al_editar(self):
        otra = self._plantilla(nombre='Coche', por_defecto=True)
        resp = self.client.patch(self.url, {'version': 1, 'por_defecto': True}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        otra.refresh_from_db()
        self.assertFalse(otra.por_defecto)

    def test_duplicar(self):
        self.plantilla.por_defecto = True
        self.plantilla.save()
        primera = self.client.post(f'{self.url}duplicar/')
        segunda = self.client.post(f'{self.url}duplicar/')
        self.assertEqual(primera.status_code, status.HTTP_201_CREATED)
        self.assertEqual([primera.data['nombre'], segunda.data['nombre']], ['Vivienda (copia)', 'Vivienda (copia 2)'])
        copia = PlantillaChecklist.objects.get(pk=primera.data['id'])
        self.assertFalse(copia.por_defecto)
        self.assertEqual(copia.version, 1)
        self.assertEqual(copia.estructura, self.plantilla.estructura)


class PropiedadConPlantillaTest(BasePlantillas):
    PROPS = '/api/v1/propiedades/'

    def test_asignar_una_propia(self):
        plantilla = self._plantilla()
        resp = self.client.post(self.PROPS, {
            'nombre': 'Piso', 'direccion': 'C1', 'plantilla_checklist': plantilla.pk,
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data['plantilla_efectiva'], {
            'id': plantilla.pk, 'nombre': 'Vivienda', 'origen': 'propiedad',
        })

    def test_asignar_una_ajena_da_400(self):
        ajena = self._plantilla(owner=self.otro)
        resp = self.client.post(self.PROPS, {
            'nombre': 'Piso', 'direccion': 'C1', 'plantilla_checklist': ajena.pk,
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('plantilla_checklist', resp.data)

    def test_sin_asignar_muestra_la_por_defecto_o_nada(self):
        prop = Propiedad.objects.create(owner=self.admin, nombre='Piso', direccion='C1')
        self.assertIsNone(self.client.get(f'{self.PROPS}{prop.pk}/').data['plantilla_efectiva'])
        defecto = self._plantilla(por_defecto=True)
        self.assertEqual(self.client.get(f'{self.PROPS}{prop.pk}/').data['plantilla_efectiva'], {
            'id': defecto.pk, 'nombre': 'Vivienda', 'origen': 'defecto',
        })

    def test_quitar_la_plantilla(self):
        plantilla = self._plantilla()
        prop = Propiedad.objects.create(owner=self.admin, nombre='P', direccion='C', plantilla_checklist=plantilla)
        resp = self.client.patch(f'{self.PROPS}{prop.pk}/', {'plantilla_checklist': None}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        prop.refresh_from_db()
        self.assertIsNone(prop.plantilla_checklist)

    def test_el_listado_no_hace_una_consulta_por_propiedad(self):
        plantilla = self._plantilla()
        self._plantilla(nombre='Defecto', por_defecto=True)
        # Una con plantilla y otra con la por defecto: la por defecto se busca una vez por cliente
        Propiedad.objects.create(owner=self.admin, nombre='P0', direccion='C', plantilla_checklist=plantilla)
        Propiedad.objects.create(owner=self.admin, nombre='P1', direccion='C')
        with CaptureQueriesContext(connection) as dos:
            self.client.get(self.PROPS)
        for i in range(2, 5):
            Propiedad.objects.create(
                owner=self.admin, nombre=f'P{i}', direccion='C', plantilla_checklist=plantilla if i % 2 else None,
            )
        with CaptureQueriesContext(connection) as cinco:
            resp = self.client.get(self.PROPS)
        self.assertEqual(len(resp.data), 5)
        self.assertEqual(len(cinco), len(dos))
