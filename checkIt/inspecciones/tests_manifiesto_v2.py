"""
Formato del manifiesto v2 (ADR-005): la referencia única que comparten el backend y
verificar.py. Las hojas v2 sellan la foto junto con sus metadatos (con sal) y una hoja
final con el checklist; v1 sigue siendo solo el hash de cada foto.
"""
import hashlib

from django.test import SimpleTestCase

from .services import merkle
from .services.verificador_paquete import (
    MAX_BYTES_HOJA,
    datos_hoja,
    hojas_del_manifiesto,
    json_canonico,
    metadatos_sha256,
    raiz,
    version_de,
)

FOTO = hashlib.sha256(b'foto 1').hexdigest()
FOTO_2 = hashlib.sha256(b'foto 2').hexdigest()
SAL = '00112233445566778899aabbccddeeff'
SAL_2 = 'ffeeddccbbaa99887766554433221100'


def hoja_foto(evidencia=11, sha256=FOTO, sal=SAL, **cambios):
    metadatos = {
        'evidencia': evidencia, 'estancia': 'Cocina', 'descripcion': None, 'punto': 3,
        'capturada_en': '2026-11-27T10:00:00+00:00', 'sal': sal,
    }
    metadatos.update(cambios)
    return {'tipo': 'foto', 'evidencia': evidencia, 'sha256': sha256, 'metadatos': metadatos}


def hoja_checklist(sal=SAL_2):
    return {
        'tipo': 'checklist', 'sal': sal,
        'checklist': {'formato': 1, 'plantilla': 'Piso', 'plantilla_version': 2, 'estancias': [
            {'nombre': 'Cocina', 'puntos': [{'id': 3, 'texto': 'Horno', 'requiere_foto': True,
                                              'resultado': 'OK', 'nota': '', 'marcado_en': None,
                                              'evidencias': [11]}]},
        ]},
    }


def manifiesto_v2(hojas=None):
    hojas = hojas if hojas is not None else [hoja_foto(), hoja_foto(12, FOTO_2, SAL_2), hoja_checklist()]
    return {'version': 2, 'inspeccion': 7, 'algoritmo': 'sha256', 'arbol': 'RFC6962-SHA256', 'hojas': hojas}


class JsonCanonicoTest(SimpleTestCase):
    def test_claves_ordenadas_sin_espacios_y_utf8(self):
        self.assertEqual(
            json_canonico({'b': 'ñ', 'a': [1, None, True], 'c': {'z': 0, 'y': ''}}),
            '{"a":[1,null,true],"b":"ñ","c":{"y":"","z":0}}'.encode('utf-8'),
        )

    def test_rechaza_decimales_aunque_esten_anidados(self):
        # Python y otros lenguajes no escriben igual los decimales: fuera del formato
        for valor in (1.5, {'a': [0.0]}, {'a': {'b': float('nan')}}):
            with self.subTest(valor=valor), self.assertRaises(ValueError):
                json_canonico(valor)

    def test_rechaza_anidamientos_desmesurados_sin_agotar_la_pila(self):
        # Un manifiesto guardado muy anidado no debe dar RecursionError (un 500 en el endpoint público)
        profundo = {}
        for _ in range(5000):
            profundo = {'a': profundo}
        with self.assertRaises(ValueError):
            json_canonico(profundo)

    def test_rechaza_claves_que_no_son_texto(self):
        with self.assertRaises(ValueError):
            json_canonico({1: 'a'})


class VersionTest(SimpleTestCase):
    def test_sin_version_es_v1(self):
        self.assertEqual(version_de({'hojas': []}), 1)
        self.assertEqual(version_de({'version': 1, 'hojas': []}), 1)
        self.assertEqual(version_de({'version': 2, 'hojas': []}), 2)

    def test_version_desconocida_o_mal_formada_es_un_error(self):
        for version in (3, 0, '2', True, None, 2.0):
            with self.subTest(version=version), self.assertRaises(ValueError):
                version_de({'version': version, 'hojas': []})


class DatosHojaV1Test(SimpleTestCase):
    def test_es_el_hash_de_la_foto(self):
        self.assertEqual(datos_hoja({'evidencia': 1, 'sha256': FOTO}, 1), bytes.fromhex(FOTO))

    def test_hash_mal_formado_es_un_error(self):
        for sha256 in (FOTO.upper(), FOTO[:-1], None, 'z' * 64):
            with self.subTest(sha256=sha256), self.assertRaises(ValueError):
                datos_hoja({'evidencia': 1, 'sha256': sha256}, 1)


class DatosHojaV2Test(SimpleTestCase):
    def test_hoja_de_foto_une_foto_y_metadatos_con_su_tipo(self):
        hoja = hoja_foto()
        esperado = (
            b'\x01' + bytes.fromhex(FOTO)
            + hashlib.sha256(json_canonico(hoja['metadatos'])).digest()
        )
        self.assertEqual(datos_hoja(hoja, 2), esperado)
        self.assertEqual(len(esperado), 65)
        self.assertEqual(metadatos_sha256(hoja), hashlib.sha256(json_canonico(hoja['metadatos'])).hexdigest())

    def test_vector_fijo_de_hoja_de_foto(self):
        # Bytes exactos: si cambia, cambian todas las raíces v2 ya selladas
        self.assertEqual(
            json_canonico(hoja_foto()['metadatos']),
            b'{"capturada_en":"2026-11-27T10:00:00+00:00","descripcion":null,"estancia":"Cocina",'
            b'"evidencia":11,"punto":3,"sal":"00112233445566778899aabbccddeeff"}',
        )

    def test_cualquier_metadato_cambia_la_hoja(self):
        base = datos_hoja(hoja_foto(), 2)
        for cambio in ({'estancia': 'Baño'}, {'descripcion': 'Mancha'}, {'punto': None},
                       {'capturada_en': None}, {'sal': SAL_2}):
            with self.subTest(cambio=cambio):
                self.assertNotEqual(datos_hoja(hoja_foto(**cambio), 2), base)

    def test_hoja_del_checklist(self):
        hoja = hoja_checklist()
        esperado = b'\x02' + hashlib.sha256(
            json_canonico({'checklist': hoja['checklist'], 'sal': hoja['sal']})
        ).digest()
        self.assertEqual(datos_hoja(hoja, 2), esperado)
        self.assertEqual(len(esperado), 33)
        self.assertIsNone(metadatos_sha256(hoja))

    def test_hoja_v2_mal_formada_es_un_error(self):
        sin_metadatos = hoja_foto()
        del sin_metadatos['metadatos']
        otra_evidencia = hoja_foto()
        otra_evidencia['metadatos']['evidencia'] = 99
        casos = {
            'tipo desconocido': {**hoja_foto(), 'tipo': 'video'},
            'sin tipo': {k: v for k, v in hoja_foto().items() if k != 'tipo'},
            'sin metadatos': sin_metadatos,
            'sal corta': hoja_foto(sal=SAL[:-2]),
            'sal en mayúsculas': hoja_foto(sal=SAL.upper()),
            'sin sal': {**hoja_foto(), 'metadatos': {'evidencia': 11}},
            # La evidencia que se busca en el paquete tiene que ser la sellada
            'evidencia distinta de la sellada': otra_evidencia,
            'checklist sin sal': {k: v for k, v in hoja_checklist().items() if k != 'sal'},
            'checklist que no es un objeto': {**hoja_checklist(), 'checklist': []},
            'decimal en los metadatos': hoja_foto(punto=1.0),
            'no es un objeto': ['foto'],
        }
        for nombre, hoja in casos.items():
            with self.subTest(nombre), self.assertRaises(ValueError):
                datos_hoja(hoja, 2)


class TopesTest(SimpleTestCase):
    def test_hoja_demasiado_grande(self):
        with self.assertRaises(ValueError):
            datos_hoja(hoja_foto(descripcion='x' * (MAX_BYTES_HOJA + 1)), 2)

    def test_checklist_grande_pero_razonable(self):
        # Cientos de puntos con nota caben
        hoja = hoja_checklist()
        hoja['checklist']['estancias'][0]['puntos'] *= 500
        self.assertEqual(len(datos_hoja(hoja, 2)), 33)


class HojasDelManifiestoTest(SimpleTestCase):
    def test_v1_son_los_hashes_de_las_fotos(self):
        manifiesto = {'version': 1, 'hojas': [{'evidencia': 1, 'sha256': FOTO}, {'evidencia': 2, 'sha256': FOTO_2}]}
        self.assertEqual(hojas_del_manifiesto(manifiesto), [bytes.fromhex(FOTO), bytes.fromhex(FOTO_2)])

    def test_v2_da_la_raiz_del_mismo_arbol(self):
        manifiesto = manifiesto_v2()
        hojas = hojas_del_manifiesto(manifiesto)
        self.assertEqual(hojas, [datos_hoja(h, 2) for h in manifiesto['hojas']])
        # verificar.py y el backend calculan la misma raíz
        self.assertEqual(raiz(hojas), merkle.raiz(hojas))

    def test_v2_sin_checklist_es_valido(self):
        self.assertEqual(len(hojas_del_manifiesto(manifiesto_v2([hoja_foto()]))), 1)

    def test_el_checklist_solo_puede_ser_la_ultima_hoja_y_una_vez(self):
        for hojas in ([hoja_checklist(), hoja_foto()], [hoja_foto(), hoja_checklist(), hoja_checklist(SAL)]):
            with self.subTest(hojas=[h['tipo'] for h in hojas]), self.assertRaises(ValueError):
                hojas_del_manifiesto(manifiesto_v2(hojas))

    def test_v2_no_admite_dos_hojas_de_la_misma_evidencia(self):
        with self.assertRaises(ValueError):
            hojas_del_manifiesto(manifiesto_v2([hoja_foto(), hoja_foto(sha256=FOTO_2, sal=SAL_2)]))

    def test_hojas_que_no_son_una_lista(self):
        with self.assertRaises(ValueError):
            hojas_del_manifiesto({'version': 2, 'hojas': {'0': hoja_foto()}})
