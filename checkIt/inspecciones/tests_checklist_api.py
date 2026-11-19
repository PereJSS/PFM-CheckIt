"""API del checklist de cada inspección (#19, sesión 1): copia al crear, marcar puntos,
fotos por punto, regla de completado y resumen en el listado."""
from unittest.mock import patch

from django.contrib.admin.sites import site as admin_site
from django.db import connection
from django.forms.models import model_to_dict
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from rest_framework import status
from rest_framework.test import APITestCase

from integraciones.models import WebhookEvent
from integraciones.services import procesar_evento
from propiedades.models import PlantillaChecklist, Propiedad
from users.models import User

from .models import ChecklistInspeccion, Evidencia, Inspeccion, PuntoInspeccion
from .services.checklist import instanciar_checklist
from .tests import _make_image

ESTRUCTURA = {'estancias': [
    {'nombre': 'Cocina', 'puntos': [
        {'texto': 'Encimera', 'requiere_foto': False},
        {'texto': 'Vista general', 'requiere_foto': True},
    ]},
    {'nombre': 'Baño', 'puntos': [{'texto': 'Grifo', 'requiere_foto': False}]},
]}
INSPECCIONES = '/api/v1/inspecciones/'


class BaseChecklistApi(APITestCase):
    def setUp(self):
        # Sin red: la TSA no se consulta en los tests
        parche = patch('inspecciones.views.solicitar_sello_tiempo', return_value=None)
        parche.start()
        self.addCleanup(parche.stop)
        self.admin = User.objects.create_user(username='admin', password='Pass1234!', role=User.Role.ADMIN)
        self.otro = User.objects.create_user(username='otro', password='Pass1234!', role=User.Role.ADMIN)
        self.op = User.objects.create_user(
            username='op', password='Pass1234!', role=User.Role.OPERARIO, administrador=self.admin,
        )
        self.op2 = User.objects.create_user(
            username='op2', password='Pass1234!', role=User.Role.OPERARIO, administrador=self.admin,
        )
        self.plantilla = PlantillaChecklist.objects.create(owner=self.admin, nombre='Vivienda', estructura=ESTRUCTURA)
        self.prop = Propiedad.objects.create(
            owner=self.admin, nombre='Piso', direccion='C1', plantilla_checklist=self.plantilla,
            external_id='LISTING-42',
        )
        self.insp = Inspeccion.objects.create(propiedad=self.prop, operario=self.op)
        instanciar_checklist(self.insp)
        self.p = {p.texto: p for p in PuntoInspeccion.objects.filter(inspeccion=self.insp)}
        self.client.force_authenticate(user=self.op)

    def _url(self, sufijo='', inspeccion=None):
        return f'{INSPECCIONES}{(inspeccion or self.insp).pk}/{sufijo}'

    def _marcar(self, texto, resultado, **extra):
        return self.client.patch(
            self._url(f'checklist/puntos/{self.p[texto].pk}/'), {'resultado': resultado, **extra}, format='json',
        )

    def _foto(self, **extra):
        return self.client.post(
            self._url('evidencias/'), {'foto': _make_image(), **extra}, format='multipart',
        )


class CopiaAlCrearTest(BaseChecklistApi):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(user=self.admin)

    def _crear(self, **extra):
        return self.client.post(INSPECCIONES, {'propiedad': self.prop.pk, 'operario': self.op.pk, **extra}, format='json')

    def test_el_admin_crea_la_inspeccion_con_su_checklist(self):
        resp = self._crear()
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        insp = Inspeccion.objects.get(pk=resp.data['id'])
        self.assertEqual(insp.checklist.plantilla_nombre, 'Vivienda')
        self.assertEqual(PuntoInspeccion.objects.filter(inspeccion=insp).count(), 3)
        self.assertEqual(resp.data['checklist_resumen']['puntos'], 3)

    def test_sin_plantilla_se_crea_como_hasta_ahora(self):
        self.prop.plantilla_checklist = None
        self.prop.save()
        resp = self._crear()
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertFalse(ChecklistInspeccion.objects.filter(inspeccion_id=resp.data['id']).exists())
        self.assertIsNone(resp.data['checklist_resumen'])

    def test_puede_elegir_otra_plantilla_propia(self):
        coche = PlantillaChecklist.objects.create(owner=self.admin, nombre='Coche', estructura={'estancias': [
            {'nombre': 'Exterior', 'puntos': [{'texto': 'Carrocería'}]},
        ]})
        resp = self._crear(plantilla=coche.pk)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(Inspeccion.objects.get(pk=resp.data['id']).checklist.plantilla_nombre, 'Coche')

    def test_una_plantilla_corrupta_da_400_y_no_crea_nada(self):
        PlantillaChecklist.objects.filter(pk=self.plantilla.pk).update(estructura={'estancias': []})
        antes = Inspeccion.objects.count()
        resp = self._crear()
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('Vivienda', resp.data['error'])
        self.assertEqual(Inspeccion.objects.count(), antes)

    def test_una_plantilla_ajena_da_400_y_no_crea_nada(self):
        ajena = PlantillaChecklist.objects.create(owner=self.otro, nombre='Ajena', estructura=ESTRUCTURA)
        antes = Inspeccion.objects.count()
        resp = self._crear(plantilla=ajena.pk)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Inspeccion.objects.count(), antes)

    def test_el_checkout_del_pms_crea_la_inspeccion_con_su_checklist(self):
        evento = WebhookEvent.objects.create(
            owner=self.admin, event_id='evt_1', tipo='reservation.checkout',
            payload={'event_type': 'reservation.checkout', 'data': {'property_id': 'listing-42'}},
        )
        evento = procesar_evento(evento.pk)
        self.assertEqual(evento.estado, WebhookEvent.Estado.PROCESADO)
        self.assertEqual(evento.inspeccion.checklist.plantilla_nombre, 'Vivienda')

    def test_una_plantilla_corrupta_no_pierde_el_checkout(self):
        # Guardada sin validar (shell, datos viejos): la inspección se crea igual, sin checklist
        PlantillaChecklist.objects.filter(pk=self.plantilla.pk).update(estructura={'estancias': []})
        evento = WebhookEvent.objects.create(
            owner=self.admin, event_id='evt_3', tipo='reservation.checkout',
            payload={'data': {'property_id': 'LISTING-42'}},
        )
        with self.assertLogs('integraciones.services', level='ERROR'):
            evento = procesar_evento(evento.pk)
        self.assertEqual(evento.estado, WebhookEvent.Estado.PROCESADO)
        self.assertIsNotNone(evento.inspeccion)
        self.assertFalse(ChecklistInspeccion.objects.filter(inspeccion=evento.inspeccion).exists())
        self.assertIn('Vivienda', evento.error)

    def test_el_checkout_sin_plantilla_crea_la_inspeccion_sin_checklist(self):
        self.prop.plantilla_checklist = None
        self.prop.save()
        evento = WebhookEvent.objects.create(
            owner=self.admin, event_id='evt_2', tipo='reservation.checkout',
            payload={'data': {'property_id': 'LISTING-42'}},
        )
        evento = procesar_evento(evento.pk)
        self.assertEqual(evento.estado, WebhookEvent.Estado.PROCESADO)
        self.assertFalse(ChecklistInspeccion.objects.filter(inspeccion=evento.inspeccion).exists())


class VerChecklistTest(BaseChecklistApi):
    def test_el_operario_ve_estancias_y_puntos(self):
        resp = self.client.get(self._url('checklist/'))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data['tiene_checklist'])
        self.assertEqual(resp.data['plantilla_nombre'], 'Vivienda')
        self.assertEqual([e['nombre'] for e in resp.data['estancias']], ['Cocina', 'Baño'])
        punto = resp.data['estancias'][0]['puntos'][1]
        self.assertEqual(punto['texto'], 'Vista general')
        self.assertTrue(punto['requiere_foto'])
        self.assertIsNone(punto['resultado'])
        self.assertEqual(punto['evidencias'], [])
        self.assertEqual(resp.data['resumen']['sin_marcar'], 3)

    def test_sin_checklist(self):
        otra = Inspeccion.objects.create(propiedad=self.prop, operario=self.op)
        resp = self.client.get(self._url('checklist/', otra))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data, {'tiene_checklist': False})

    def test_el_admin_dueno_si_y_otro_cliente_no(self):
        self.client.force_authenticate(user=self.admin)
        self.assertEqual(self.client.get(self._url('checklist/')).status_code, status.HTTP_200_OK)
        self.client.force_authenticate(user=self.otro)
        self.assertEqual(self.client.get(self._url('checklist/')).status_code, status.HTTP_404_NOT_FOUND)
        self.client.force_authenticate(user=self.op2)
        self.assertEqual(self.client.get(self._url('checklist/')).status_code, status.HTTP_404_NOT_FOUND)


class AplicarChecklistTest(BaseChecklistApi):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(user=self.admin)
        self.sin = Inspeccion.objects.create(propiedad=self.prop, operario=self.op)

    def test_aplica_la_plantilla_efectiva_a_una_sin_checklist(self):
        resp = self.client.post(self._url('checklist/', self.sin), {}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data['plantilla_nombre'], 'Vivienda')
        self.assertEqual(PuntoInspeccion.objects.filter(inspeccion=self.sin).count(), 3)

    def test_si_ya_tiene_da_409(self):
        self.assertEqual(self.client.post(self._url('checklist/'), {}, format='json').status_code, status.HTTP_409_CONFLICT)

    def test_reemplazar_solo_sin_marcas_ni_fotos(self):
        coche = PlantillaChecklist.objects.create(owner=self.admin, nombre='Coche', estructura={'estancias': [
            {'nombre': 'Exterior', 'puntos': [{'texto': 'Carrocería'}]},
        ]})
        resp = self.client.post(self._url('checklist/'), {'plantilla': coche.pk, 'reemplazar': True}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(
            list(PuntoInspeccion.objects.filter(inspeccion=self.insp).values_list('texto', flat=True)), ['Carrocería'],
        )
        PuntoInspeccion.objects.filter(inspeccion=self.insp).update(resultado='OK')
        resp = self.client.post(self._url('checklist/'), {'plantilla': self.plantilla.pk, 'reemplazar': True}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT)

    def test_reemplazar_debe_ser_booleano(self):
        resp = self.client.post(self._url('checklist/'), {'reemplazar': 'true'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_plantilla_con_id_mal_tipado_da_400(self):
        for valor in (True, 1.5, '1e3', 2 ** 70):
            with self.subTest(valor=valor):
                resp = self.client.post(self._url('checklist/', self.sin), {'plantilla': valor}, format='json')
                self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_sin_plantilla_da_400(self):
        self.prop.plantilla_checklist = None
        self.prop.save()
        resp = self.client.post(self._url('checklist/', self.sin), {}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_plantilla_ajena_da_400(self):
        ajena = PlantillaChecklist.objects.create(owner=self.otro, nombre='Ajena', estructura=ESTRUCTURA)
        resp = self.client.post(self._url('checklist/', self.sin), {'plantilla': ajena.pk}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_completada_da_409_y_el_operario_no_puede(self):
        Inspeccion.objects.filter(pk=self.sin.pk).update(estado=Inspeccion.Estado.COMPLETADA)
        self.assertEqual(self.client.post(self._url('checklist/', self.sin), {}, format='json').status_code,
                         status.HTTP_409_CONFLICT)
        otra = Inspeccion.objects.create(propiedad=self.prop, operario=self.op)
        self.client.force_authenticate(user=self.op)
        self.assertEqual(self.client.post(self._url('checklist/', otra), {}, format='json').status_code,
                         status.HTTP_403_FORBIDDEN)


class MarcarPuntoTest(BaseChecklistApi):
    def test_marca_y_guarda_quien_y_cuando(self):
        resp = self._marcar('Encimera', 'DANO', nota='Quemadura')
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertTrue(resp.data['aplicado'])
        self.assertEqual(resp.data['punto']['resultado'], 'DANO')
        punto = PuntoInspeccion.objects.get(pk=self.p['Encimera'].pk)
        self.assertEqual((punto.resultado, punto.nota, punto.marcado_por), ('DANO', 'Quemadura', self.op))
        self.assertIsNotNone(punto.marcado_en)

    def test_desmarcar(self):
        self._marcar('Encimera', 'OK')
        resp = self._marcar('Encimera', None)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIsNone(PuntoInspeccion.objects.get(pk=self.p['Encimera'].pk).resultado)

    def test_resultado_o_nota_no_validos_dan_400(self):
        self.assertEqual(self._marcar('Encimera', 'ROTO').status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self._marcar('Encimera', 'OK', nota='a\x00b').status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            self._marcar('Encimera', 'OK', marcado_en_dispositivo='ayer').status_code, status.HTTP_400_BAD_REQUEST,
        )
        self.assertIsNone(PuntoInspeccion.objects.get(pk=self.p['Encimera'].pk).resultado)

    def test_un_punto_de_otra_inspeccion_da_404(self):
        otra = Inspeccion.objects.create(propiedad=self.prop, operario=self.op)
        instanciar_checklist(otra)
        ajeno = PuntoInspeccion.objects.filter(inspeccion=otra).first()
        resp = self.client.patch(self._url(f'checklist/puntos/{ajeno.pk}/'), {'resultado': 'OK'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
        self.assertIsNone(PuntoInspeccion.objects.get(pk=ajeno.pk).resultado)

    def test_otro_operario_o_cliente_no_puede(self):
        for usuario in (self.op2, self.otro):
            with self.subTest(usuario=usuario.username):
                self.client.force_authenticate(user=usuario)
                self.assertEqual(self._marcar('Encimera', 'OK').status_code, status.HTTP_404_NOT_FOUND)

    def test_el_admin_dueno_puede(self):
        self.client.force_authenticate(user=self.admin)
        self.assertEqual(self._marcar('Encimera', 'OK').status_code, status.HTTP_200_OK)

    def test_completada_da_409(self):
        Inspeccion.objects.filter(pk=self.insp.pk).update(estado=Inspeccion.Estado.COMPLETADA)
        self.assertEqual(self._marcar('Encimera', 'OK').status_code, status.HTTP_409_CONFLICT)

    def test_sin_resultado_ni_nota_da_400(self):
        resp = self.client.patch(self._url(f'checklist/puntos/{self.p["Encimera"].pk}/'), {}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIsNone(PuntoInspeccion.objects.get(pk=self.p['Encimera'].pk).marcado_en)

    def test_un_id_enorme_da_404(self):
        resp = self.client.patch(self._url(f'checklist/puntos/{10 ** 30}/'), {'resultado': 'OK'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_una_marca_sin_hora_del_movil_no_borra_la_proteccion(self):
        self._marcar('Encimera', 'DANO', marcado_en_dispositivo='2026-11-19T10:05:00Z')
        self.client.force_authenticate(user=self.admin)
        self._marcar('Encimera', 'DANO', nota='Revisado')
        self.client.force_authenticate(user=self.op)
        resp = self._marcar('Encimera', 'OK', marcado_en_dispositivo='2026-11-19T10:00:00Z')
        self.assertFalse(resp.data['aplicado'])
        self.assertEqual(PuntoInspeccion.objects.get(pk=self.p['Encimera'].pk).resultado, 'DANO')

    def test_gana_la_marca_mas_reciente_del_movil(self):
        self._marcar('Encimera', 'DANO', marcado_en_dispositivo='2026-11-19T10:05:00Z')
        # Llega tarde una marca anterior de la cola sin conexión: no pisa la nueva
        resp = self._marcar('Encimera', 'OK', marcado_en_dispositivo='2026-11-19T10:00:00Z')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertFalse(resp.data['aplicado'])
        self.assertEqual(resp.data['motivo'], 'marca_mas_reciente')
        self.assertEqual(resp.data['punto']['resultado'], 'DANO')
        self.assertEqual(PuntoInspeccion.objects.get(pk=self.p['Encimera'].pk).resultado, 'DANO')


class FotoDeUnPuntoTest(BaseChecklistApi):
    def test_enlaza_la_foto_y_toma_la_estancia_del_punto(self):
        resp = self._foto(punto=self.p['Grifo'].pk, estancia='Salón')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        ev = Evidencia.objects.get()
        self.assertEqual(ev.punto, self.p['Grifo'])
        self.assertEqual(ev.estancia, 'Baño')
        self.assertEqual(resp.data['punto'], self.p['Grifo'].pk)
        self.assertNotIn('aviso', resp.data)

    def test_un_punto_ajeno_guarda_la_foto_sin_enlazar_y_avisa(self):
        # Un 400 haría que el móvil apartase la foto, que es la prueba
        otra = Inspeccion.objects.create(propiedad=self.prop, operario=self.op)
        instanciar_checklist(otra)
        ajeno = PuntoInspeccion.objects.filter(inspeccion=otra).first()
        for valor in (ajeno.pk, 999999, 'abc', str(10 ** 30)):
            with self.subTest(valor=valor), self.assertLogs('inspecciones.views', level='WARNING'):
                resp = self._foto(punto=valor)
                self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
                self.assertIn('aviso', resp.data)
                self.assertIsNone(Evidencia.objects.get(pk=resp.data['id']).punto)


class CompletarConChecklistTest(BaseChecklistApi):
    def _completar(self):
        return self.client.patch(self._url(), {'estado': 'COMPLETADA'}, format='json')

    def test_con_puntos_sin_marcar_da_409_con_las_faltas(self):
        self._marcar('Encimera', 'OK')
        resp = self._completar()
        self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT)
        self.assertIn('error', resp.data)
        self.assertEqual(
            {(f['texto'], f['motivo']) for f in resp.data['faltas']},
            {('Vista general', 'sin_marcar'), ('Grifo', 'sin_marcar')},
        )
        self.insp.refresh_from_db()
        self.assertNotEqual(self.insp.estado, Inspeccion.Estado.COMPLETADA)
        self.assertIsNone(self.insp.manifiesto_raiz)

    def test_dano_sin_foto_da_409(self):
        self._marcar('Encimera', 'DANO')
        self._marcar('Vista general', 'NO_APLICA')
        self._marcar('Grifo', 'OK')
        resp = self._completar()
        self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual([f['motivo'] for f in resp.data['faltas']], ['falta_foto'])

    def test_con_todo_en_regla_se_completa(self):
        self._marcar('Encimera', 'DANO')
        self._marcar('Vista general', 'OK')
        self._marcar('Grifo', 'NO_APLICA')
        self._foto(punto=self.p['Encimera'].pk)
        self._foto(punto=self.p['Vista general'].pk)
        resp = self._completar()
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.insp.refresh_from_db()
        self.assertEqual(self.insp.estado, Inspeccion.Estado.COMPLETADA)

    def test_el_admin_tampoco_puede_saltarselo(self):
        self.client.force_authenticate(user=self.admin)
        self.assertEqual(self._completar().status_code, status.HTTP_409_CONFLICT)

    def test_sin_checklist_se_completa_como_hasta_ahora(self):
        otra = Inspeccion.objects.create(propiedad=self.prop, operario=self.op)
        resp = self.client.patch(self._url('', otra), {'estado': 'COMPLETADA'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_el_admin_de_django_aplica_la_misma_regla(self):
        modelo_admin = admin_site._registry[Inspeccion]
        peticion = RequestFactory().get('/admin/')
        peticion.user = User.objects.create_superuser(username='root', password='Pass1234!')
        formulario = modelo_admin.get_form(peticion, self.insp, change=True)
        datos = model_to_dict(self.insp)
        datos['estado'] = Inspeccion.Estado.COMPLETADA
        form = formulario(data={k: v for k, v in datos.items() if v is not None}, instance=self.insp)
        self.assertFalse(form.is_valid())
        self.assertIn('estado', form.errors)


class ResumenEnElListadoTest(BaseChecklistApi):
    def test_cada_inspeccion_trae_su_resumen_sin_consultas_por_fila(self):
        self._marcar('Encimera', 'DANO')
        sin = Inspeccion.objects.create(propiedad=self.prop, operario=self.op)
        resp = self.client.get(INSPECCIONES)
        por_id = {i['id']: i['checklist_resumen'] for i in resp.data}
        self.assertEqual(por_id[self.insp.pk], {'puntos': 3, 'ok': 0, 'danos': 1, 'no_aplica': 0, 'sin_marcar': 2})
        self.assertIsNone(por_id[sin.pk])
        with CaptureQueriesContext(connection) as dos:
            self.client.get(INSPECCIONES)
        for _ in range(3):
            instanciar_checklist(Inspeccion.objects.create(propiedad=self.prop, operario=self.op))
        with CaptureQueriesContext(connection) as cinco:
            self.client.get(INSPECCIONES)
        self.assertEqual(len(cinco), len(dos))


class ChecklistEnElAdminTest(BaseChecklistApi):
    def test_el_checklist_de_una_inspeccion_es_de_solo_lectura(self):
        modelo_admin = admin_site._registry[ChecklistInspeccion]
        peticion = RequestFactory().get('/admin/')
        peticion.user = User.objects.create_superuser(username='root', password='Pass1234!')
        self.assertFalse(modelo_admin.has_change_permission(peticion, self.insp.checklist))
        self.assertFalse(modelo_admin.has_delete_permission(peticion, self.insp.checklist))
        self.assertFalse(modelo_admin.has_add_permission(peticion))

