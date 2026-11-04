"""
Tests del verificador público (POST /api/v1/verificacion/): la prueba
devuelta se comprueba con merkle.verificar_inclusion y el sello con
verificador_paquete.huella_sellada, sin confiar en lo que afirme el servidor.
"""
import base64
import hashlib
import json
from io import BytesIO
from unittest.mock import patch
from urllib.parse import urlencode

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase, override_settings
from rest_framework import status
from rest_framework.exceptions import ParseError
from rest_framework.test import APIClient, APITestCase

from propiedades.models import Propiedad
from users.models import User

from .models import Evidencia, Inspeccion, generar_clave_verificacion
from .services import merkle
from .services.verificacion import MAX_MANIFIESTOS, _sello_individual, _sello_vigente, buscar
from .services.verificador_paquete import huella_sellada
from .tests import _make_image
from .tests_sellos import _crear_tsa, _post_con_tsa, _sello, _texto
from .verificacion_publica import MAX_CUERPO, CuerpoAcotadoParser, VerificacionSerializer


def _hash(n):
    return hashlib.sha256(f'foto-{n}'.encode()).hexdigest()


def _raiz(*hashes):
    return merkle.raiz([bytes.fromhex(h) for h in hashes]).hex()


URL = '/api/v1/verificacion/'


class _Base(APITestCase):
    def setUp(self):
        cache.clear()  # el throttle guarda contadores en caché
        self.addCleanup(cache.clear)
        self.admin = User.objects.create_user(username='admin_secreto', password='Pass1234!', role=User.Role.ADMIN)
        self.operario = User.objects.create_user(username='op_secreto', password='Pass1234!', role=User.Role.OPERARIO)
        self.propiedad = Propiedad.objects.create(owner=self.admin, nombre='Casa secreta', direccion='Calle secreta')
        self.inspeccion = self._inspeccion()

    @property
    def clave(self):
        return self.inspeccion.clave_verificacion

    def _inspeccion(self):
        return Inspeccion.objects.create(propiedad=self.propiedad, operario=self.operario)

    def _evidencia(self, hash_sha256, inspeccion=None, **campos):
        return Evidencia.objects.create(
            inspeccion=inspeccion or self.inspeccion, foto=_make_image(), hash_sha256=hash_sha256, **campos,
        )

    def _guardar_estado(self, estado, inspeccion=None):
        inspeccion = inspeccion or self.inspeccion
        inspeccion.estado = estado
        inspeccion.save()

    def _completar(self, inspeccion=None):
        self._guardar_estado(Inspeccion.Estado.COMPLETADA, inspeccion)

    def _reabrir(self, inspeccion=None):
        self._guardar_estado(Inspeccion.Estado.EN_PROGRESO, inspeccion)

    def _sellar(self, inspeccion=None, **sello):
        from .services.manifiesto import sellar_manifiesto
        pk = (inspeccion or self.inspeccion).pk
        with patch('inspecciones.services.manifiesto.solicitar_sello_tiempo', return_value=_sello(**sello)):
            self.assertTrue(sellar_manifiesto(pk))

    def _post(self, clave, hash_sha256, **extra):
        return self.client.post(URL, {'clave': clave, 'sha256': hash_sha256}, format='json', **extra)

    def _consultar(self, hash_sha256, clave=None):
        response = self._post(clave or self.clave, hash_sha256)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        return response.json()

    def _no_encontrada(self, hash_sha256, clave=None):
        response = self._post(clave or self.clave, hash_sha256)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def _archivo(self, anteriores, inspeccion=None):
        pk = (inspeccion or self.inspeccion).pk
        Inspeccion.objects.filter(pk=pk).update(manifiestos_anteriores=anteriores)

    def _entrada(self, *hashes, **campos):
        return {
            'manifiesto': {
                'version': 1, 'arbol': 'RFC6962-SHA256',
                'hojas': [{'evidencia': n, 'sha256': h} for n, h in enumerate(hashes)],
            },
            'raiz': _raiz(*hashes), 'tsa_token': base64.b64encode(b'x').decode(), **campos,
        }


class ClaveTest(_Base):
    def test_es_aleatoria_de_256_bits_y_unica(self):
        otra = self._inspeccion()
        self.assertRegex(self.clave, r'^[0-9a-f]{64}$')
        self.assertNotEqual(self.clave, otra.clave_verificacion)
        self.assertNotEqual(generar_clave_verificacion(), generar_clave_verificacion())

    def test_no_depende_de_las_fotos(self):
        # La raíz sí se puede calcular con las fotos; por eso no sirve de llave
        self._evidencia(_hash(1))
        self._completar()
        self.inspeccion.refresh_from_db()
        self.assertNotEqual(self.clave, self.inspeccion.manifiesto_raiz)
        self._no_encontrada(_hash(1), clave=_raiz(_hash(1)))

    def test_no_cambia_al_completar_ni_al_reabrir(self):
        clave = self.clave
        self._evidencia(_hash(1))
        self._completar()
        self._reabrir()
        self.inspeccion.save()
        self.assertEqual(Inspeccion.objects.get(pk=self.inspeccion.pk).clave_verificacion, clave)

    def test_no_sale_en_la_api_de_inspecciones(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get(f'/api/v1/inspecciones/{self.inspeccion.pk}/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertNotIn(self.clave, json.dumps(response.json()))

    def test_no_se_puede_fijar_por_la_api(self):
        self.client.force_authenticate(self.admin)
        clave = self.clave
        self.client.patch(
            f'/api/v1/inspecciones/{self.inspeccion.pk}/', {'clave_verificacion': 'a' * 64}, format='json',
        )
        self.assertEqual(Inspeccion.objects.get(pk=self.inspeccion.pk).clave_verificacion, clave)


class EntregaDeLaClaveTest(_Base):
    def test_va_en_el_pdf(self):
        from .services.pdf_generator import _seccion_manifiesto, _styles
        self._evidencia(_hash(1))
        self._completar()
        self.inspeccion.refresh_from_db()
        self.assertIn(self.clave, _texto(_seccion_manifiesto(self.inspeccion, _styles())))

    def test_va_en_el_webhook(self):
        from integraciones.models import EVENTO_INSPECCION_COMPLETADA
        from integraciones.salientes import construir_datos
        datos = construir_datos(EVENTO_INSPECCION_COMPLETADA, self.inspeccion)
        self.assertEqual(datos['clave_verificacion'], self.clave)

    def test_va_en_el_expediente_del_paquete(self):
        from .services.paquete import _expediente
        self._evidencia(_hash(1))
        self._completar()
        self.inspeccion.refresh_from_db()
        expediente = _expediente(self.inspeccion, [], [], {}, [])
        self.assertEqual(expediente['inspeccion']['clave_verificacion'], self.clave)


class MigracionClaveTest(TransactionTestCase):
    def test_rellena_una_clave_distinta_en_las_inspecciones_existentes(self):
        antes = [('inspecciones', '0010_inspeccion_clave_verificacion')]
        despues = [('inspecciones', '0012_clave_verificacion_unica')]
        executor = MigrationExecutor(connection)
        ultima = executor.loader.graph.leaf_nodes('inspecciones')
        self.addCleanup(lambda: MigrationExecutor(connection).migrate(ultima))
        executor.migrate(antes)
        grafo = executor.loader.graph
        apps = executor.loader.project_state(
            antes + grafo.leaf_nodes('propiedades') + grafo.leaf_nodes('users'),
        ).apps
        usuario = apps.get_model('users', 'User').objects.create(username='u')
        propiedad = apps.get_model('propiedades', 'Propiedad').objects.create(owner=usuario, nombre='P', direccion='C')
        Antigua = apps.get_model('inspecciones', 'Inspeccion')
        pks = [Antigua.objects.create(propiedad=propiedad).pk for _ in range(3)]
        self.assertEqual(set(Antigua.objects.values_list('clave_verificacion', flat=True)), {None})

        executor = MigrationExecutor(connection)
        executor.migrate(despues)
        claves = list(Inspeccion.objects.filter(pk__in=pks).values_list('clave_verificacion', flat=True))
        self.assertEqual(len(set(claves)), 3)
        for clave in claves:
            self.assertRegex(clave, r'^[0-9a-f]{64}$')


    def test_una_inspeccion_creada_tras_el_relleno_no_bloquea_el_deploy(self):
        # El código viejo sigue creando inspecciones sin clave mientras se despliega
        relleno = [('inspecciones', '0011_rellenar_clave_verificacion')]
        executor = MigrationExecutor(connection)
        ultima = executor.loader.graph.leaf_nodes('inspecciones')
        self.addCleanup(lambda: MigrationExecutor(connection).migrate(ultima))
        executor.migrate(relleno)
        grafo = executor.loader.graph
        apps = executor.loader.project_state(
            relleno + grafo.leaf_nodes('propiedades') + grafo.leaf_nodes('users'),
        ).apps
        usuario = apps.get_model('users', 'User').objects.create(username='u')
        propiedad = apps.get_model('propiedades', 'Propiedad').objects.create(owner=usuario, nombre='P', direccion='C')
        tardia = apps.get_model('inspecciones', 'Inspeccion').objects.create(propiedad=propiedad)
        self.assertIsNone(tardia.clave_verificacion)

        MigrationExecutor(connection).migrate([('inspecciones', '0012_clave_verificacion_unica')])
        self.assertRegex(Inspeccion.objects.get(pk=tardia.pk).clave_verificacion, r'^[0-9a-f]{64}$')


class PruebaDeInclusionTest(_Base):
    def _comprobar_prueba(self, total):
        hashes = [_hash(n) for n in range(total)]
        for h in hashes:
            self._evidencia(h)
        self._completar()
        for indice, h in enumerate(hashes):
            m = self._consultar(h)['manifiestos'][0]
            self.assertEqual((m['indice'], m['total'], m['raiz']), (indice, total, _raiz(*hashes)))
            self.assertTrue(merkle.verificar_inclusion(
                bytes.fromhex(h), m['indice'], m['total'], [bytes.fromhex(p) for p in m['prueba']],
                bytes.fromhex(m['raiz']),
            ))

    def test_prueba_valida_con_1_hoja(self):
        self._comprobar_prueba(1)

    def test_prueba_valida_con_2_hojas(self):
        self._comprobar_prueba(2)

    def test_prueba_valida_con_3_hojas(self):
        self._comprobar_prueba(3)

    def test_prueba_valida_con_7_hojas(self):
        self._comprobar_prueba(7)

    def test_el_sello_devuelto_es_de_la_raiz(self):
        from .services.manifiesto import sellar_manifiesto
        self._evidencia(_hash(1))
        self._completar()
        _, dummy = _crear_tsa()
        with override_settings(TSA_URL='https://tsa.example/tsr', TSA_CERT='', TSA_MANIFIESTO_URL=''), \
                patch('inspecciones.services.tsa.requests.post', side_effect=_post_con_tsa(dummy)):
            self.assertTrue(sellar_manifiesto(self.inspeccion.pk))
        m = self._consultar(_hash(1))['manifiestos'][0]
        self.assertEqual(huella_sellada(base64.b64decode(m['sello']['token'])), m['raiz'])

    def test_devuelve_el_arbol_y_la_version(self):
        self._evidencia(_hash(1))
        self._completar()
        m = self._consultar(_hash(1))['manifiestos'][0]
        self.assertEqual((m['version'], m['arbol'], m['vigente']), (1, 'RFC6962-SHA256', True))


class SinClaveNoSeAprendeNadaTest(_Base):
    def test_clave_erronea_misma_respuesta_exista_o_no_la_foto(self):
        self._evidencia(_hash(1))
        self._completar()
        con_foto = self._post('b' * 64, _hash(1))
        sin_foto = self._post('b' * 64, _hash(99))
        self.assertEqual((con_foto.status_code, con_foto.content), (sin_foto.status_code, sin_foto.content))
        self.assertEqual(con_foto.status_code, status.HTTP_404_NOT_FOUND)

    def test_clave_erronea_no_consulta_las_fotos(self):
        # Sin diferencia de trabajo tampoco hay diferencia de tiempo
        self._evidencia(_hash(1))
        with self.assertNumQueries(1):
            self.assertIsNone(buscar('b' * 64, _hash(1)))
        with self.assertNumQueries(1):
            self.assertIsNone(buscar('b' * 64, _hash(99)))

    def test_la_raiz_calculada_con_una_sola_foto_no_abre_nada(self):
        # El ataque que rompió el diseño anterior: la raíz sale del hash de la foto
        self._evidencia(_hash(1))
        self._completar()
        self._no_encontrada(_hash(1), clave=_raiz(_hash(1)))

    def test_la_clave_de_otra_inspeccion_no_abre_esta(self):
        self._evidencia(_hash(1))
        self._completar()
        otra = self._inspeccion()
        self._evidencia(_hash(2), inspeccion=otra)
        self._completar(otra)
        self._no_encontrada(_hash(1), clave=otra.clave_verificacion)

    def test_la_misma_foto_en_otro_cliente_no_se_mezcla(self):
        ajeno = User.objects.create_user(username='ajeno', password='Pass1234!', role=User.Role.ADMIN)
        de_otro = Inspeccion.objects.create(
            propiedad=Propiedad.objects.create(owner=ajeno, nombre='B', direccion='C'), operario=self.operario,
        )
        self._evidencia(_hash(1), inspeccion=de_otro, tsa_token=b'sello-ajeno')
        self._completar(de_otro)
        self._sellar(de_otro, cualificada=False)
        self._evidencia(_hash(1), tsa_token=b'sello-propio')
        self._completar()
        data = self._consultar(_hash(1))
        self.assertEqual(base64.b64decode(data['sello_individual']['token']), b'sello-propio')
        self.assertIsNone(data['manifiestos'][0]['sello'])

    def test_solo_las_claves_previstas(self):
        # Lista blanca: un campo nuevo debe decidirse aquí, sobre todo si es del cliente
        self._evidencia(_hash(1), tsa_token=b'token-foto')
        self._completar()
        self._sellar()
        data = self._consultar(_hash(1))
        self.assertEqual(set(data), {'hash', 'manifiestos', 'sello_individual', 'anomalias', 'truncado'})
        self.assertEqual(set(data['sello_individual']), {'token', 'timestamp'})
        m = data['manifiestos'][0]
        self.assertEqual(set(m), {'vigente', 'version', 'arbol', 'raiz', 'indice', 'total', 'prueba', 'sello'})
        self.assertEqual(set(m['sello']), {'token', 'timestamp', 'url', 'cualificada', 'verificado'})

    def test_sin_datos_del_cliente_ni_la_propia_clave(self):
        self._evidencia(_hash(1))
        self._completar()
        self._sellar()
        self.inspeccion.refresh_from_db()
        self._reabrir()
        self._completar()
        cuerpo = json.dumps(self._consultar(_hash(1)))
        for dato in ('secret', 'admin_', 'op_', 'archivado', 'tsa_', self.clave):
            self.assertNotIn(dato, cuerpo)


class EstadosTest(_Base):
    def test_foto_registrada_sin_manifiesto(self):
        self._evidencia(_hash(1))
        data = self._consultar(_hash(1))
        self.assertEqual((data['manifiestos'], data['anomalias'], data['sello_individual']), ([], 0, None))

    def test_foto_que_no_es_de_la_inspeccion_404(self):
        self._evidencia(_hash(1))
        self._completar()
        self._no_encontrada(_hash(2))

    def test_manifiesto_congelado_sin_sellar(self):
        self._evidencia(_hash(1))
        self._completar()
        self.assertIsNone(self._consultar(_hash(1))['manifiestos'][0]['sello'])

    def test_sello_individual(self):
        self._evidencia(_hash(1), tsa_token=b'token-foto')
        sello = self._consultar(_hash(1))['sello_individual']
        self.assertEqual(base64.b64decode(sello['token']), b'token-foto')

    def test_foto_borrada_sigue_probada_por_el_manifiesto(self):
        # La prueba es el manifiesto sellado, no la fila de la evidencia
        evidencia = self._evidencia(_hash(1))
        self._completar()
        self._sellar()
        evidencia.delete()
        data = self._consultar(_hash(1))
        self.assertEqual(len(data['manifiestos']), 1)
        self.assertIsNone(data['sello_individual'])

    def test_archivado_al_reabrir(self):
        self._evidencia(_hash(1))
        self._completar()
        self._sellar()
        self.inspeccion.refresh_from_db()
        raiz = self.inspeccion.manifiesto_raiz
        self._reabrir()
        manifiestos = self._consultar(_hash(1))['manifiestos']
        self.assertEqual([(m['raiz'], m['vigente']) for m in manifiestos], [(raiz, False)])
        self.assertEqual(base64.b64decode(manifiestos[0]['sello']['token']), b'token-manifiesto')

    def test_el_sello_archivado_es_identico_al_vigente_que_fue(self):
        # Fija las claves del archivo que escribe reabrir_manifiesto y el formato de la fecha
        self._evidencia(_hash(1))
        self._completar()
        self._sellar()
        sello = self._consultar(_hash(1))['manifiestos'][0]['sello']
        self.assertEqual((sello['url'], sello['cualificada'], sello['verificado']),
                         ('https://cualificada.example/tsr', True, True))
        self.assertIsNotNone(sello['timestamp'])
        self.inspeccion.refresh_from_db()
        self._reabrir()
        self.assertEqual(self._consultar(_hash(1))['manifiestos'][0]['sello'], sello)

    def test_del_mas_antiguo_al_vigente(self):
        # El primer sello es el de más valor: prueba la anterioridad
        self._evidencia(_hash(1))
        raices = []
        for n in range(2, 4):
            self._completar()
            self._sellar()
            self.inspeccion.refresh_from_db()
            raices.append(self.inspeccion.manifiesto_raiz)
            self._reabrir()
            self._evidencia(_hash(n))
        self._completar()
        self.inspeccion.refresh_from_db()
        manifiestos = self._consultar(_hash(1))['manifiestos']
        self.assertEqual([m['raiz'] for m in manifiestos], [*raices, self.inspeccion.manifiesto_raiz])
        self.assertEqual([m['vigente'] for m in manifiestos], [False, False, True])

    def test_foto_anadida_tras_reabrir_no_esta_en_el_archivado_y_no_alerta(self):
        self._evidencia(_hash(1))
        self._completar()
        self._sellar()
        self.inspeccion.refresh_from_db()
        self._reabrir()
        self._evidencia(_hash(2))
        with self.assertNoLogs('inspecciones.services.verificacion', level='WARNING'):
            data = self._consultar(_hash(2))
        self.assertEqual((data['manifiestos'], data['anomalias']), ([], 0))

    def test_misma_foto_dos_veces_da_el_primer_indice(self):
        self._evidencia(_hash(1))
        self._evidencia(_hash(1))
        self._completar()
        self.assertEqual(self._consultar(_hash(1))['manifiestos'][0]['indice'], 0)

    def test_tokens_en_memoryview_como_los_da_postgres(self):
        evidencia = Evidencia(tsa_token=memoryview(b'foto'))
        self.assertEqual(base64.b64decode(_sello_individual(evidencia)['token']), b'foto')
        self.assertIsNone(_sello_individual(Evidencia(tsa_token=memoryview(b''))))
        inspeccion = Inspeccion(manifiesto_tsa_token=memoryview(b'raiz'))
        self.assertEqual(base64.b64decode(_sello_vigente(inspeccion)['token']), b'raiz')


class ContratoHttpTest(_Base):
    def test_mal_formado_400_en_cualquiera_de_los_dos(self):
        for malo in (
            'abc', 'g' * 64, 'a' * 63, 'a' * 65, 'a' * 64 + '\n', ' ' + 'a' * 64, 'á' * 64, 'a' * 63 + '\x00', '',
            5, None, True, ['a' * 64], {'a': 1},
        ):
            for clave, sha256 in ((malo, 'a' * 64), ('a' * 64, malo)):
                with self.subTest(clave=clave, sha256=sha256), self.assertNumQueries(0):
                    self.assertEqual(self._post(clave, sha256).status_code, status.HTTP_400_BAD_REQUEST)

    def test_cuerpo_incompleto_o_que_no_es_un_objeto_400(self):
        for cuerpo in ({}, {'clave': 'a' * 64}, {'sha256': 'a' * 64}, ['a' * 64, 'b' * 64], 'a' * 64, 5, None):
            with self.subTest(cuerpo=cuerpo), self.assertNumQueries(0):
                response = self.client.post(URL, cuerpo, format='json')
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertEqual(response['Cache-Control'], 'no-store')

    def test_json_invalido_400(self):
        response = self.client.post(URL, '{no es json', content_type='application/json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response['Cache-Control'], 'no-store')

    def test_json_muy_anidado_400(self):
        # La recursión del parser daría un 500, sin no-store y con un traceback en el log
        for cuerpo in ('[' * 400, '[' * 2000, '{"a":' * 2000):
            with self.subTest(largo=len(cuerpo)):
                response = self.client.post(URL, cuerpo, content_type='application/json')
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertEqual(response['Cache-Control'], 'no-store')

    def test_cuerpo_demasiado_grande_400_aunque_la_consulta_sea_valida(self):
        self._evidencia(_hash(1))
        consulta = json.dumps({'clave': self.clave, 'sha256': _hash(1)})
        justo = consulta + ' ' * (MAX_CUERPO - len(consulta))
        response = self.client.post(URL, justo, content_type='application/json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        with self.assertNumQueries(0):
            response = self.client.post(URL, justo + ' ', content_type='application/json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response['Cache-Control'], 'no-store')

    def test_no_lee_mas_de_lo_permitido(self):
        # Sin depender de Content-Length: un cuerpo troceado no lo lleva
        stream = BytesIO(b' ' * (MAX_CUERPO * 10))
        with self.assertRaises(ParseError):
            CuerpoAcotadoParser().parse(stream)
        self.assertEqual(stream.tell(), MAX_CUERPO + 1)

    def test_solo_acepta_json(self):
        # Un formulario de otra web no puede lanzar consultas sin preflight
        cuerpo = {'clave': self.clave, 'sha256': _hash(1)}
        self._evidencia(_hash(1))
        response = self.client.post(URL, cuerpo, format='multipart')
        self.assertEqual(response.status_code, status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)
        response = self.client.post(URL, urlencode(cuerpo), content_type='application/x-www-form-urlencoded')
        self.assertEqual(response.status_code, status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)

    def test_la_clave_no_viaja_en_la_url(self):
        # Los logs de acceso y los proxies guardan la URL: ni la ruta ni el GET deben aceptarla
        self._evidencia(_hash(1))
        ruta_antigua = f'{URL}{self.clave}/{_hash(1)}/'
        self.assertEqual(self.client.get(ruta_antigua).status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(self.client.post(ruta_antigua).status_code, status.HTTP_404_NOT_FOUND)
        consulta = {'clave': self.clave, 'sha256': _hash(1)}
        self.assertEqual(self.client.get(URL, consulta).status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertEqual(
            self.client.post(f'{URL}?{urlencode(consulta)}', {}, format='json').status_code,
            status.HTTP_400_BAD_REQUEST,
        )

    @override_settings(CORS_ALLOWED_ORIGINS=['https://pagina.example'])
    def test_la_pagina_publica_pasa_el_preflight(self):
        # El POST con JSON desde otro origen lo necesita
        response = self.client.options(
            URL, HTTP_ORIGIN='https://pagina.example', HTTP_ACCESS_CONTROL_REQUEST_METHOD='POST',
            HTTP_ACCESS_CONTROL_REQUEST_HEADERS='content-type',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response['Access-Control-Allow-Origin'], 'https://pagina.example')
        self.assertIn('POST', response['Access-Control-Allow-Methods'])
        self.assertIn('content-type', response['Access-Control-Allow-Headers'])

    def test_no_exige_csrf(self):
        # Sin cuenta ni cookies no hay sesión que proteger, y la página pública no tiene token
        self._evidencia(_hash(1))
        cliente = APIClient(enforce_csrf_checks=True)
        response = cliente.post(URL, {'clave': self.clave, 'sha256': _hash(1)}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_normaliza_mayusculas_en_los_dos(self):
        self._evidencia(_hash(1))
        self.assertEqual(self._consultar(_hash(1).upper(), clave=self.clave.upper())['hash'], _hash(1))

    def test_encuentra_hashes_antiguos_guardados_en_mayusculas(self):
        # Los datos anteriores a la validación de la subida; el manifiesto ya los normaliza
        self._evidencia(_hash(1).upper(), tsa_token=b'token-foto')
        self.assertEqual(base64.b64decode(self._consultar(_hash(1))['sello_individual']['token']), b'token-foto')
        self._completar()
        self.assertEqual(self._consultar(_hash(1))['manifiestos'][0]['total'], 1)

    def test_ninguna_respuesta_se_guarda_en_caches_intermedias(self):
        # Cambia al reabrir; un 404 guardado negaría una foto subida después
        self._evidencia(_hash(1))
        for clave, sha256 in ((self.clave, _hash(1)), ('a' * 64, 'b' * 64), ('abc', 'b' * 64)):
            self.assertEqual(self._post(clave, sha256)['Cache-Control'], 'no-store')

    def test_cumple_el_esquema_publicado(self):
        self._evidencia(_hash(1), tsa_token=b'token-foto')
        self._completar()
        self._sellar()
        serializer = VerificacionSerializer(data=self._consultar(_hash(1)))
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_cumple_el_esquema_con_tipos_raros_en_el_archivo(self):
        self._evidencia(_hash(1))
        entrada = self._entrada(
            _hash(1), tsa_timestamp={'no': 'fecha'}, tsa_url=5, tsa_cualificada='si', tsa_verificado=1,
        )
        entrada['manifiesto'].update(version='x', arbol=7)
        self._archivo([entrada])
        data = self._consultar(_hash(1))
        serializer = VerificacionSerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        sello = data['manifiestos'][0]['sello']
        self.assertEqual((sello['cualificada'], sello['verificado']), (False, False))

    def test_un_jwt_no_cambia_el_resultado(self):
        self._evidencia(_hash(1))
        self._completar()
        anonimo = self._consultar(_hash(1))
        self.client.force_authenticate(self.admin)
        self.assertEqual(self._consultar(_hash(1)), anonimo)

    def test_token_invalido_en_la_cabecera_no_da_401(self):
        self._evidencia(_hash(1))
        response = self._post(self.clave, _hash(1), HTTP_AUTHORIZATION='Bearer basura')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_throttle(self):
        with patch('rest_framework.throttling.ScopedRateThrottle.THROTTLE_RATES', {'verificacion': '2/min'}):
            codigos = [self._post('a' * 64, 'b' * 64).status_code for _ in range(3)]
        self.assertEqual(codigos, [404, 404, 429])

    def test_la_cuota_esta_configurada(self):
        # Sin ella la vista da un 500, y el test del throttle no lo ve porque la parchea
        self.assertIn('verificacion', settings.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES'])

    def test_el_esquema_documenta_el_endpoint(self):
        schema = self.client.get('/api/v1/schema/?format=json')
        ruta = schema.json()['paths'][URL]
        self.assertEqual(set(ruta), {'post'})
        self.assertIn('application/json', ruta['post']['requestBody']['content'])
        self.assertFalse(ruta['post'].get('parameters'))


class TrabajoAcotadoTest(_Base):
    def test_dos_consultas_a_la_bd_con_clave_valida(self):
        # Manifiesto, raíz y sellos salen de una misma fila: no pueden ser de momentos distintos
        self._evidencia(_hash(1))
        self._completar()
        with self.assertNumQueries(2):
            self.assertIsNotNone(buscar(self.clave, _hash(1)))

    def test_tope_de_manifiestos_conserva_los_mas_antiguos_y_lo_avisa(self):
        self._evidencia(_hash(1))
        self._archivo([self._entrada(_hash(1), tsa_timestamp=f't{n}') for n in range(MAX_MANIFIESTOS + 2)])
        data = self._consultar(_hash(1))
        self.assertEqual(
            [m['sello']['timestamp'] for m in data['manifiestos']], [f't{n}' for n in range(MAX_MANIFIESTOS)],
        )
        self.assertTrue(data['truncado'])

    def test_sin_truncar_justo_en_el_tope(self):
        self._evidencia(_hash(1))
        self._archivo([self._entrada(_hash(1)) for _ in range(MAX_MANIFIESTOS)])
        self.assertFalse(self._consultar(_hash(1))['truncado'])

    @patch('inspecciones.services.verificacion.MAX_HOJAS_POR_PETICION', 25)
    def test_el_presupuesto_de_hojas_recorta_y_lo_avisa(self):
        hashes = [_hash(n) for n in range(1, 11)]
        self._evidencia(hashes[0])
        self._archivo([self._entrada(*hashes) for _ in range(5)])
        data = self._consultar(hashes[0])
        self.assertEqual(len(data['manifiestos']), 2)
        self.assertTrue(data['truncado'])
        self.assertEqual(data['anomalias'], 0)

    @patch('inspecciones.services.verificacion.MAX_HOJAS_POR_PETICION', 25)
    def test_un_manifiesto_corrupto_tambien_gasta_presupuesto(self):
        # Sin esto, muchos manifiestos con la última hoja mala se recorrerían enteros sin tope
        hashes = [_hash(n) for n in range(1, 11)]
        malo = self._entrada(*hashes)
        malo['manifiesto']['hojas'][-1]['sha256'] = 'mala'
        self._evidencia(hashes[0])
        self._archivo([malo] * 5)
        with self.assertLogs('inspecciones.services.verificacion', level='ERROR'):
            data = self._consultar(hashes[0])
        self.assertEqual(data['anomalias'], 2)
        self.assertTrue(data['truncado'])

    @patch('inspecciones.services.verificacion.MAX_HOJAS_POR_PETICION', 5)
    def test_el_vigente_tiene_prioridad_sobre_el_archivo(self):
        hashes = [_hash(n) for n in range(1, 6)]
        for h in hashes:
            self._evidencia(h)
        self._archivo([self._entrada(*hashes)])
        self._completar()
        data = self._consultar(hashes[0])
        self.assertEqual([m['vigente'] for m in data['manifiestos']], [True])
        self.assertTrue(data['truncado'])


class DatosCorruptosTest(_Base):
    """Nada guardado debe dar un 500 en un endpoint público. Quien llega aquí tiene la
    clave: lo que no se puede leer se cuenta, para no confundirlo con una foto sin manifiesto."""

    def setUp(self):
        super().setUp()
        self.evidencia = self._evidencia(_hash(1))
        self.bueno = self._entrada(_hash(1))

    def _anomalias(self):
        with self.assertLogs('inspecciones.services.verificacion', level='ERROR'):
            return self._consultar(_hash(1))

    def _se_salta(self, entrada):
        self._archivo([entrada, self.bueno])
        data = self._anomalias()
        self.assertEqual(len(data['manifiestos']), 1)
        self.assertEqual(data['manifiestos'][0]['raiz'], self.bueno['raiz'])
        self.assertEqual(data['anomalias'], 1)

    def test_entrada_que_no_es_un_objeto(self):
        self._se_salta('basura')

    def test_manifiesto_que_no_es_un_objeto(self):
        self._se_salta({**self.bueno, 'manifiesto': 7})

    def test_hojas_que_no_son_lista(self):
        for hojas in ('basura', {}, '', 5, None):
            self._se_salta({**self.bueno, 'manifiesto': {'hojas': hojas}})

    def test_hoja_que_no_es_un_objeto(self):
        self._se_salta({**self.bueno, 'manifiesto': {'hojas': [1]}})

    def test_hoja_sin_sha256(self):
        self._se_salta({**self.bueno, 'manifiesto': {'hojas': [{'evidencia': 1}]}})

    def test_hoja_no_hexadecimal(self):
        for mala in ('zz', 'ab cd', 5, None):
            self._se_salta({**self.bueno, 'manifiesto': {'hojas': [{'sha256': _hash(1)}, {'sha256': mala}]}})

    def test_raiz_no_valida(self):
        for raiz in ('no-es-un-hash', None, 5):
            self._se_salta({**self.bueno, 'raiz': raiz})

    def test_archivo_que_no_es_lista(self):
        for archivo in ({'no': 'lista'}, {}, 'x', 5):
            self._archivo(archivo)
            data = self._anomalias()
            self.assertEqual((data['manifiestos'], data['anomalias']), ([], 1))

    def test_archivo_vacio_es_normal(self):
        self._archivo([])
        with self.assertNoLogs('inspecciones.services.verificacion', level='WARNING'):
            self.assertEqual(self._consultar(_hash(1))['anomalias'], 0)

    def test_sin_fila_de_la_foto_un_manifiesto_corrupto_no_se_confunde_con_un_404(self):
        # Puede que la foto esté en lo que no se pudo leer
        self.evidencia.delete()
        self._archivo([{**self.bueno, 'manifiesto': 7}])
        data = self._anomalias()
        self.assertEqual((data['manifiestos'], data['anomalias']), ([], 1))

    def _sin_la_hoja_de_la_foto(self):
        # Manifiesto bien formado, pero sus hojas ya no dan la raíz sellada: quitaron la de la foto
        return {**self.bueno, 'raiz': _raiz(_hash(1), _hash(2), _hash(3)), 'manifiesto': {'hojas': [
            {'sha256': _hash(1)}, {'sha256': _hash(3)},
        ]}}

    def test_archivado_sin_la_hoja_de_la_foto_es_una_anomalia(self):
        self._archivo([self._sin_la_hoja_de_la_foto()])
        with self.assertLogs('inspecciones.services.verificacion', level='ERROR'):
            data = self._consultar(_hash(2))
        self.assertEqual((data['manifiestos'], data['anomalias']), ([], 1))

    def test_vigente_sin_la_hoja_de_una_foto_borrada_es_una_anomalia(self):
        # Sin fila de la foto, nada más podría avisar de que el manifiesto cambió
        Inspeccion.objects.filter(pk=self.inspeccion.pk).update(
            manifiesto_raiz=_raiz(_hash(1), _hash(2), _hash(3)),
            manifiesto={'hojas': [{'sha256': _hash(1)}, {'sha256': _hash(3)}]},
        )
        with self.assertLogs('inspecciones.services.verificacion', level='ERROR'):
            data = self._consultar(_hash(2))
        self.assertEqual((data['manifiestos'], data['anomalias']), ([], 1))

    def test_foto_ausente_de_un_manifiesto_integro_sigue_siendo_404(self):
        # La raíz cuadra: la foto de verdad no está, y no hay nada que avisar
        self._archivo([self.bueno])
        with self.assertNoLogs('inspecciones.services.verificacion', level='WARNING'):
            self._no_encontrada(_hash(99))

    def test_una_hoja_quitada_cuenta_una_sola_anomalia_con_la_foto_registrada(self):
        self._evidencia(_hash(2))
        Inspeccion.objects.filter(pk=self.inspeccion.pk).update(
            manifiesto_raiz=_raiz(_hash(1), _hash(2), _hash(3)),
            manifiesto={'hojas': [{'sha256': _hash(1)}, {'sha256': _hash(3)}]},
        )
        with self.assertLogs('inspecciones.services.verificacion', level='ERROR'):
            self.assertEqual(self._consultar(_hash(2))['anomalias'], 1)

    def test_sello_archivado_vacio_no_parece_un_sello(self):
        self._archivo([{**self.bueno, 'tsa_token': ''}])
        with self.assertLogs('inspecciones.services.verificacion', level='ERROR'):
            data = self._consultar(_hash(1))
        self.assertIsNone(data['manifiestos'][0]['sello'])
        self.assertEqual(data['anomalias'], 1)

    def test_manifiesto_o_sello_vigente_sin_raiz_no_se_ignora(self):
        for campos in (
            {'manifiesto': {'hojas': [{'sha256': _hash(1)}]}},
            {'manifiesto_tsa_token': b'sello'},
        ):
            Inspeccion.objects.filter(pk=self.inspeccion.pk).update(
                **{'manifiesto': None, 'manifiesto_tsa_token': None, 'manifiesto_raiz': None, **campos},
            )
            with self.assertLogs('inspecciones.services.verificacion', level='ERROR'):
                data = self._consultar(_hash(1))
            self.assertEqual((data['manifiestos'], data['anomalias']), ([], 1))

    def test_sin_fila_de_la_foto_el_recorte_no_se_confunde_con_un_404(self):
        # La foto puede estar en lo que no se miró
        self.evidencia.delete()
        self._archivo([self.bueno] * 3)
        with patch('inspecciones.services.verificacion.MAX_HOJAS_POR_PETICION', 0):
            data = self._consultar(_hash(1))
        self.assertEqual((data['manifiestos'], data['anomalias'], data['truncado']), ([], 0, True))

    def test_foto_fuera_del_manifiesto_vigente_es_una_anomalia(self):
        # Con el manifiesto congelado no entran fotos: la fila o el manifiesto cambiaron
        self._completar()
        Evidencia.objects.filter(pk=self.evidencia.pk).update(hash_sha256=_hash(2))
        with self.assertLogs('inspecciones.services.verificacion', level='ERROR'):
            data = self._consultar(_hash(2))
        self.assertEqual((data['manifiestos'], data['anomalias']), ([], 1))

    def test_vigente_corrupto(self):
        for manifiesto in (None, {'hojas': 5}, {'hojas': []}):
            Inspeccion.objects.filter(pk=self.inspeccion.pk).update(manifiesto_raiz='a' * 64, manifiesto=manifiesto)
            data = self._anomalias()
            self.assertEqual((data['manifiestos'], data['anomalias']), ([], 1))

    def test_vigente_con_raiz_no_valida(self):
        Inspeccion.objects.filter(pk=self.inspeccion.pk).update(manifiesto_raiz='zz', manifiesto=self.bueno['manifiesto'])
        data = self._anomalias()
        self.assertEqual((data['manifiestos'], data['anomalias']), ([], 1))

    def test_token_archivado_ilegible_deja_el_manifiesto_sin_sello(self):
        for token in ('***', 5, [], None):
            self._archivo([{**self.bueno, 'tsa_token': token}])
            data = self._anomalias()
            self.assertIsNone(data['manifiestos'][0]['sello'], token)
            self.assertEqual(data['anomalias'], 1, token)

    def test_prueba_que_no_lleva_a_la_raiz_se_registra_y_se_devuelve(self):
        # Ocultarla taparía una manipulación: quien consulta verá que no cuadra
        otra = 'b' * 64
        self._archivo([{**self.bueno, 'raiz': otra}])
        data = self._anomalias()
        self.assertEqual((data['manifiestos'][0]['raiz'], data['manifiestos'][0]['total']), (otra, 1))
        self.assertEqual(data['anomalias'], 1)
