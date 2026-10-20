"""
Tests de los sellos de tiempo verificados (TSA configurable), del manifiesto
Merkle por inspección y de la firma del PDF con sello de tiempo (PAdES-B-T).

Las respuestas TSA las genera una TSA de pruebas real (DummyTimeStamper de
pyHanko con un certificado efímero), así que la verificación criptográfica se
ejercita de verdad y solo se simula el transporte HTTP.
"""
import asyncio
import datetime
import hashlib
import os
import tempfile
from unittest.mock import MagicMock, patch

from asn1crypto import keys as asn1_keys, tsp, x509 as asn1_x509
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from django.test import TestCase, override_settings
from pyhanko.sign.timestamps.dummy_client import DummyTimeStamper
from rest_framework import status
from rest_framework.test import APITestCase

from propiedades.models import Propiedad
from users.models import User

from .models import Evidencia, Inspeccion
from .services import merkle
from .tests import _make_image


# ---------------------------------------------------------------------------
# TSA de pruebas
# ---------------------------------------------------------------------------

def _certificado(nombre, eku=None):
    clave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    sujeto = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, nombre)])
    ahora = datetime.datetime.now(datetime.timezone.utc)
    builder = (
        x509.CertificateBuilder()
        .subject_name(sujeto)
        .issuer_name(sujeto)
        .public_key(clave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(ahora - datetime.timedelta(days=1))
        .not_valid_after(ahora + datetime.timedelta(days=30))
    )
    if eku:
        builder = builder.add_extension(x509.ExtendedKeyUsage([eku]), critical=True)
    return clave, builder.sign(clave, hashes.SHA256())


def _crear_tsa(nombre='TSA de pruebas'):
    """TSA efímera con EKU timeStamping. Devuelve (certificado PEM, DummyTimeStamper)."""
    clave, cert = _certificado(nombre, ExtendedKeyUsageOID.TIME_STAMPING)
    der_clave = clave.private_bytes(
        serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
    )
    dummy = DummyTimeStamper(
        tsa_cert=asn1_x509.Certificate.load(cert.public_bytes(serialization.Encoding.DER)),
        tsa_key=asn1_keys.PrivateKeyInfo.load(der_clave),
    )
    return cert.public_bytes(serialization.Encoding.PEM), dummy


def _respuesta_http(contenido, status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.content = contenido
    resp.text = ''
    return resp


def _post_con_tsa(dummy, manipular=None):
    """Simula requests.post contra la TSA de pruebas; `manipular` altera la petición antes de sellar."""
    def fake_post(url, data=None, **kwargs):
        req = tsp.TimeStampReq.load(data)
        if manipular:
            req = manipular(req)
        res = asyncio.run(dummy.async_request_tsa_response(req))
        return _respuesta_http(res.dump())
    return fake_post


def _guardar_pem(test, pem):
    fd, ruta = tempfile.mkstemp(suffix='.pem')
    with os.fdopen(fd, 'wb') as f:
        f.write(pem)
    test.addCleanup(os.remove, ruta)
    return ruta


HASH_A = hashlib.sha256(b'foto-a').hexdigest()
HASH_B = hashlib.sha256(b'foto-b').hexdigest()


# ---------------------------------------------------------------------------
# TSA configurable y verificada
# ---------------------------------------------------------------------------

class TSAVerificadaTest(TestCase):
    def setUp(self):
        self.pem, self.dummy = _crear_tsa()
        self.ruta_cert = _guardar_pem(self, self.pem)

    def _solicitar(self, *args, **kwargs):
        from .services.tsa import solicitar_sello_tiempo
        return solicitar_sello_tiempo(*args, **kwargs)

    def test_token_verificado_con_certificado_configurado(self):
        with patch('inspecciones.services.tsa.requests.post', side_effect=_post_con_tsa(self.dummy)), \
                self.settings(TSA_CERT=self.ruta_cert):
            datos = self._solicitar(HASH_A)
        self.assertIsNotNone(datos)
        self.assertTrue(datos['verificado'])
        self.assertIsNotNone(datos['timestamp'])
        self.assertTrue(datos['token'])

    def test_sin_certificado_comprueba_integridad_pero_no_marca_verificado(self):
        with patch('inspecciones.services.tsa.requests.post', side_effect=_post_con_tsa(self.dummy)), \
                self.settings(TSA_CERT=''):
            datos = self._solicitar(HASH_A)
        self.assertIsNotNone(datos)
        self.assertFalse(datos['verificado'])

    def test_rechaza_token_de_otra_tsa(self):
        _, otra = _crear_tsa('TSA impostora')
        with patch('inspecciones.services.tsa.requests.post', side_effect=_post_con_tsa(otra)), \
                self.settings(TSA_CERT=self.ruta_cert):
            with self.assertLogs('inspecciones.services.tsa', level='ERROR'):
                self.assertIsNone(self._solicitar(HASH_A))

    def test_rechaza_token_de_otro_hash(self):
        def cambiar_hash(req):
            req['message_imprint']['hashed_message'] = hashlib.sha256(b'otra').digest()
            return req
        with patch('inspecciones.services.tsa.requests.post',
                   side_effect=_post_con_tsa(self.dummy, cambiar_hash)):
            with self.assertLogs('inspecciones.services.tsa', level='ERROR'):
                self.assertIsNone(self._solicitar(HASH_A))

    def test_rechaza_nonce_distinto(self):
        def cambiar_nonce(req):
            req['nonce'] = req['nonce'].native + 1
            return req
        with patch('inspecciones.services.tsa.requests.post',
                   side_effect=_post_con_tsa(self.dummy, cambiar_nonce)):
            with self.assertLogs('inspecciones.services.tsa', level='ERROR'):
                self.assertIsNone(self._solicitar(HASH_A))

    def test_peticion_lleva_nonce_y_pide_certificado(self):
        peticiones = []

        def capturar(req):
            peticiones.append(req)
            return req
        with patch('inspecciones.services.tsa.requests.post',
                   side_effect=_post_con_tsa(self.dummy, capturar)):
            self._solicitar(HASH_A)
        self.assertIsNotNone(peticiones[0]['nonce'].native)
        self.assertTrue(peticiones[0]['cert_req'].native)
        self.assertEqual(peticiones[0]['message_imprint']['hashed_message'].native, bytes.fromhex(HASH_A))

    @override_settings(TSA_USUARIO='cliente', TSA_PASSWORD='secreto',
                       TSA_CERT_CLIENTE='/certs/cliente.crt', TSA_CLAVE_CLIENTE='/certs/cliente.key')
    def test_envia_basic_auth_y_certificado_cliente(self):
        with patch('inspecciones.services.tsa.requests.post', return_value=_respuesta_http(b'', 503)) as post:
            self._solicitar(HASH_A)
        kwargs = post.call_args.kwargs
        self.assertEqual(kwargs['auth'], ('cliente', 'secreto'))
        self.assertEqual(kwargs['cert'], ('/certs/cliente.crt', '/certs/cliente.key'))

    def test_sin_credenciales_no_envia_auth_ni_cert(self):
        with patch('inspecciones.services.tsa.requests.post', return_value=_respuesta_http(b'', 503)) as post:
            self._solicitar(HASH_A)
        kwargs = post.call_args.kwargs
        self.assertIsNone(kwargs['auth'])
        self.assertIsNone(kwargs['cert'])

    @override_settings(TSA_URL='https://gratis.example/tsr', TSA_CUALIFICADA=False,
                       TSA_MANIFIESTO_URL='https://cualificada.example/tsr',
                       TSA_MANIFIESTO_USUARIO='gestora', TSA_MANIFIESTO_PASSWORD='clave',
                       TSA_MANIFIESTO_CUALIFICADA=True)
    def test_perfil_manifiesto_usa_su_propia_tsa(self):
        # "cualificada" exige además el certificado fijado (ver TSAEndurecidaTest)
        with patch('inspecciones.services.tsa.requests.post', side_effect=_post_con_tsa(self.dummy)) as post, \
                self.settings(TSA_MANIFIESTO_CERT=self.ruta_cert):
            datos = self._solicitar(HASH_A, perfil='manifiesto')
        self.assertEqual(post.call_args.args[0], 'https://cualificada.example/tsr')
        self.assertEqual(post.call_args.kwargs['auth'], ('gestora', 'clave'))
        self.assertTrue(datos['cualificada'])
        self.assertEqual(datos['url'], 'https://cualificada.example/tsr')

    @override_settings(TSA_URL='https://gratis.example/tsr', TSA_MANIFIESTO_URL='')
    def test_perfil_manifiesto_sin_url_propia_usa_la_tsa_general(self):
        with patch('inspecciones.services.tsa.requests.post', side_effect=_post_con_tsa(self.dummy)) as post:
            datos = self._solicitar(HASH_A, perfil='manifiesto')
        self.assertEqual(post.call_args.args[0], 'https://gratis.example/tsr')
        self.assertFalse(datos['cualificada'])

    def test_estado_rechazado_devuelve_none(self):
        # TimeStampResp ::= SEQUENCE { status PKIStatusInfo { status rejection(2) } }, sin token
        rechazo = bytes.fromhex('30053003020102')
        with patch('inspecciones.services.tsa.requests.post', return_value=_respuesta_http(rechazo)):
            with self.assertLogs('inspecciones.services.tsa', level='WARNING'):
                self.assertIsNone(self._solicitar(HASH_A))


# ---------------------------------------------------------------------------
# Árbol Merkle (RFC 6962)
# ---------------------------------------------------------------------------

def _hoja(b):
    return hashlib.sha256(b'\x00' + b).digest()


def _nodo(i, d):
    return hashlib.sha256(b'\x01' + i + d).digest()


class MerkleTest(TestCase):
    def setUp(self):
        self.hojas = [hashlib.sha256(bytes([i])).digest() for i in range(7)]

    def test_arbol_vacio_es_hash_de_cadena_vacia(self):
        self.assertEqual(merkle.raiz([]), hashlib.sha256(b'').digest())

    def test_una_hoja_lleva_prefijo_de_hoja(self):
        self.assertEqual(merkle.raiz(self.hojas[:1]), _hoja(self.hojas[0]))

    def test_tres_hojas_divide_por_la_mayor_potencia_de_dos(self):
        a, b, c = self.hojas[:3]
        esperada = _nodo(_nodo(_hoja(a), _hoja(b)), _hoja(c))
        self.assertEqual(merkle.raiz(self.hojas[:3]), esperada)

    def test_raiz_depende_del_orden(self):
        self.assertNotEqual(merkle.raiz(self.hojas[:2]), merkle.raiz(self.hojas[1::-1]))

    def test_hoja_y_nodo_no_colisionan(self):
        # Sin separación de dominio, un nodo interno podría hacerse pasar por hoja
        a, b = self.hojas[:2]
        self.assertNotEqual(merkle.raiz([_hoja(a) + _hoja(b)]), merkle.raiz([a, b]))

    def test_pruebas_de_inclusion_verifican_para_todos_los_tamanos(self):
        for n in range(1, 8):
            hojas = self.hojas[:n]
            r = merkle.raiz(hojas)
            for i in range(n):
                with self.subTest(n=n, i=i):
                    prueba = merkle.prueba_inclusion(hojas, i)
                    self.assertTrue(merkle.verificar_inclusion(hojas[i], i, n, prueba, r))

    def test_prueba_no_verifica_otra_hoja_ni_otra_posicion(self):
        hojas = self.hojas[:5]
        r = merkle.raiz(hojas)
        prueba = merkle.prueba_inclusion(hojas, 2)
        self.assertFalse(merkle.verificar_inclusion(hojas[3], 2, 5, prueba, r))
        self.assertFalse(merkle.verificar_inclusion(hojas[2], 1, 5, prueba, r))

    def test_indice_fuera_de_rango(self):
        with self.assertRaises(IndexError):
            merkle.prueba_inclusion(self.hojas[:3], 3)


# ---------------------------------------------------------------------------
# Manifiesto por inspección
# ---------------------------------------------------------------------------

class _ConInspeccion:
    def _crear_inspeccion(self):
        self.admin = User.objects.create_user(username='admin_m', password='Pass1234!', role=User.Role.ADMIN)
        self.operario = User.objects.create_user(username='op_m', password='Pass1234!', role=User.Role.OPERARIO)
        prop = Propiedad.objects.create(owner=self.admin, nombre='P', direccion='C')
        self.inspeccion = Inspeccion.objects.create(propiedad=prop, operario=self.operario)

    def _evidencia(self, hash_sha256):
        return Evidencia.objects.create(inspeccion=self.inspeccion, foto=_make_image(), hash_sha256=hash_sha256)

    def _completar(self):
        # update() no dispara señales: el test controla cuándo se sella
        Inspeccion.objects.filter(pk=self.inspeccion.pk).update(estado=Inspeccion.Estado.COMPLETADA)


def _sello(cualificada=True, verificado=True):
    from django.utils import timezone
    return {
        'token': b'token-manifiesto', 'timestamp': timezone.now(),
        'url': 'https://cualificada.example/tsr', 'cualificada': cualificada, 'verificado': verificado,
    }


class ManifiestoTest(_ConInspeccion, TestCase):
    def setUp(self):
        self._crear_inspeccion()

    def test_construir_ordena_por_evidencia_e_ignora_hashes_invalidos(self):
        from .services.manifiesto import construir_manifiesto
        e1 = self._evidencia(HASH_B)
        self._evidencia(None)
        self._evidencia('no-es-un-hash')
        e2 = self._evidencia(HASH_A.upper())
        m = construir_manifiesto(self.inspeccion)
        self.assertEqual(m['version'], 1)
        self.assertEqual(m['inspeccion'], self.inspeccion.pk)
        self.assertEqual(m['arbol'], 'RFC6962-SHA256')
        self.assertEqual(m['hojas'], [
            {'evidencia': e1.pk, 'sha256': HASH_B},
            {'evidencia': e2.pk, 'sha256': HASH_A},
        ])
        esperada = merkle.raiz([bytes.fromhex(HASH_B), bytes.fromhex(HASH_A)]).hex()
        self.assertEqual(m['raiz'], esperada)

    @patch('inspecciones.services.manifiesto.solicitar_sello_tiempo')
    def test_sellar_guarda_manifiesto_y_sella_la_raiz_con_el_perfil_manifiesto(self, mock_tsa):
        from .services.manifiesto import sellar_manifiesto
        self._evidencia(HASH_A)
        self._evidencia(HASH_B)
        mock_tsa.return_value = _sello()
        self._completar()
        self.assertTrue(sellar_manifiesto(self.inspeccion.pk))
        self.inspeccion.refresh_from_db()
        raiz = merkle.raiz([bytes.fromhex(HASH_A), bytes.fromhex(HASH_B)]).hex()
        self.assertEqual(self.inspeccion.manifiesto_raiz, raiz)
        self.assertEqual(self.inspeccion.manifiesto['raiz'], raiz)
        self.assertEqual(bytes(self.inspeccion.manifiesto_tsa_token), b'token-manifiesto')
        self.assertIsNotNone(self.inspeccion.manifiesto_tsa_timestamp)
        self.assertTrue(self.inspeccion.manifiesto_tsa_cualificada)
        self.assertEqual(self.inspeccion.manifiesto_tsa_url, 'https://cualificada.example/tsr')
        mock_tsa.assert_called_with(raiz, perfil='manifiesto')

    @patch('inspecciones.services.manifiesto.solicitar_sello_tiempo')
    def test_no_sella_inspecciones_sin_completar(self, mock_tsa):
        from .services.manifiesto import sellar_manifiesto
        self._evidencia(HASH_A)
        self.assertFalse(sellar_manifiesto(self.inspeccion.pk))
        mock_tsa.assert_not_called()
        self.inspeccion.refresh_from_db()
        self.assertIsNone(self.inspeccion.manifiesto)

    @patch('inspecciones.services.manifiesto.solicitar_sello_tiempo', return_value=None)
    def test_tsa_caida_deja_manifiesto_congelado_y_pendiente(self, mock_tsa):
        from .services.manifiesto import sellar_manifiesto
        self._evidencia(HASH_A)
        self._completar()
        self.assertFalse(sellar_manifiesto(self.inspeccion.pk))
        self.inspeccion.refresh_from_db()
        self.assertIsNotNone(self.inspeccion.manifiesto_raiz)
        self.assertIsNone(self.inspeccion.manifiesto_tsa_token)
        self.assertIsNotNone(self.inspeccion.manifiesto_tsa_ultimo_intento)

    @patch('inspecciones.services.manifiesto.solicitar_sello_tiempo')
    def test_manifiesto_sellado_no_se_rehace(self, mock_tsa):
        from .services.manifiesto import sellar_manifiesto
        self._evidencia(HASH_A)
        self._completar()
        mock_tsa.return_value = _sello()
        sellar_manifiesto(self.inspeccion.pk)
        self.inspeccion.refresh_from_db()
        raiz = self.inspeccion.manifiesto_raiz
        mock_tsa.reset_mock()
        self.assertTrue(sellar_manifiesto(self.inspeccion.pk))
        mock_tsa.assert_not_called()
        self.inspeccion.refresh_from_db()
        self.assertEqual(self.inspeccion.manifiesto_raiz, raiz)

    @patch('inspecciones.services.manifiesto.solicitar_sello_tiempo')
    def test_reintento_sella_el_manifiesto_congelado_sin_recalcularlo(self, mock_tsa):
        from .services.manifiesto import sellar_manifiesto
        self._evidencia(HASH_A)
        self._completar()
        mock_tsa.return_value = None
        sellar_manifiesto(self.inspeccion.pk)
        self.inspeccion.refresh_from_db()
        raiz = self.inspeccion.manifiesto_raiz
        # Aparece otra evidencia (p. ej. creada desde el admin de Django): no altera lo congelado
        self._evidencia(HASH_B)
        mock_tsa.return_value = _sello()
        self.assertTrue(sellar_manifiesto(self.inspeccion.pk))
        mock_tsa.assert_called_with(raiz, perfil='manifiesto')
        self.inspeccion.refresh_from_db()
        self.assertEqual(self.inspeccion.manifiesto_raiz, raiz)

    @patch('inspecciones.services.manifiesto.solicitar_sello_tiempo')
    def test_no_pisa_un_sello_puesto_por_otro_proceso(self, mock_tsa):
        from .services.manifiesto import sellar_manifiesto
        self._evidencia(HASH_A)
        self._completar()

        def fake(raiz, perfil):
            Inspeccion.objects.filter(pk=self.inspeccion.pk).update(manifiesto_tsa_token=b'otro')
            return _sello()
        mock_tsa.side_effect = fake
        self.assertTrue(sellar_manifiesto(self.inspeccion.pk))
        self.inspeccion.refresh_from_db()
        self.assertEqual(bytes(self.inspeccion.manifiesto_tsa_token), b'otro')


class ManifiestoAlCompletarTest(_ConInspeccion, APITestCase):
    def setUp(self):
        self._crear_inspeccion()

    @patch('inspecciones.tasks.sellar_manifiesto_async.apply_async')
    def test_completar_encola_el_sellado_tras_el_commit(self, mock_delay):
        self.client.force_authenticate(self.operario)
        with self.captureOnCommitCallbacks(execute=True):
            resp = self.client.patch(
                f'/api/v1/inspecciones/{self.inspeccion.pk}/', {'estado': 'COMPLETADA'}, format='json',
            )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        mock_delay.assert_called_once_with(args=[self.inspeccion.pk], retry=False)

    @patch('inspecciones.tasks.sellar_manifiesto_async.apply_async')
    def test_otros_guardados_no_encolan(self, mock_delay):
        with self.captureOnCommitCallbacks(execute=True):
            self.inspeccion.pdf_estado = Inspeccion.PdfEstado.GENERANDO
            self.inspeccion.save(update_fields=['pdf_estado'])
            self.inspeccion.estado = Inspeccion.Estado.EN_PROGRESO
            self.inspeccion.save()
        mock_delay.assert_not_called()

    @patch('inspecciones.views.solicitar_sello_tiempo', return_value=None)
    def test_no_admite_evidencias_en_inspeccion_completada(self, _):
        self._completar()
        self.client.force_authenticate(self.operario)
        resp = self.client.post(
            f'/api/v1/inspecciones/{self.inspeccion.pk}/evidencias/',
            {'foto': _make_image(), 'hash_sha256': HASH_A}, format='multipart',
        )
        self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT)
        self.assertFalse(Evidencia.objects.filter(inspeccion=self.inspeccion).exists())


@override_settings(TSA_RESELLO_PAUSA_SEG=0)
class ReselladoManifiestosTest(_ConInspeccion, TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self._crear_inspeccion()

    @patch('inspecciones.tasks.solicitar_sello_tiempo', return_value=None)
    @patch('inspecciones.services.manifiesto.solicitar_sello_tiempo')
    def test_resello_periodico_reintenta_manifiestos_pendientes(self, mock_manifiesto, _):
        mock_manifiesto.return_value = _sello()
        self._evidencia(HASH_A)
        self._completar()
        from .tasks import resellar_evidencias_pendientes
        with self.assertLogs('inspecciones.tasks', level='WARNING'):
            resultado = resellar_evidencias_pendientes()
        self.inspeccion.refresh_from_db()
        self.assertIsNotNone(self.inspeccion.manifiesto_tsa_token)
        self.assertEqual(resultado['manifiestos_sellados'], 1)

    @patch('inspecciones.services.manifiesto.solicitar_sello_tiempo')
    def test_resello_ignora_inspecciones_abiertas(self, mock_manifiesto):
        self._evidencia(HASH_A)
        from .tasks import resellar_evidencias_pendientes
        with patch('inspecciones.tasks.solicitar_sello_tiempo', return_value=_sello()):
            resultado = resellar_evidencias_pendientes()
        mock_manifiesto.assert_not_called()
        self.assertEqual(resultado['manifiestos_sellados'], 0)

    @patch('inspecciones.services.manifiesto.solicitar_sello_tiempo')
    def test_generar_pdf_sella_antes_el_manifiesto(self, mock_manifiesto):
        mock_manifiesto.return_value = _sello()
        self._evidencia(HASH_A)
        self._completar()
        from .tasks import generar_pdf_async
        with patch('inspecciones.services.pdf_signer.firmar_pdf_reclamacion',
                   side_effect=lambda entrada, salida: os.replace(entrada, salida)):
            generar_pdf_async(self.inspeccion.pk)
        self.inspeccion.refresh_from_db()
        self.assertIsNotNone(self.inspeccion.manifiesto_tsa_token)
        self.assertEqual(self.inspeccion.pdf_estado, Inspeccion.PdfEstado.LISTO)


# ---------------------------------------------------------------------------
# PDF: sección del manifiesto y firma PAdES-B-T
# ---------------------------------------------------------------------------

def _texto(flowables):
    from reportlab.platypus import Paragraph, Table
    partes = []
    for f in flowables:
        if isinstance(f, Paragraph):
            partes.append(f.getPlainText())
        elif isinstance(f, Table):
            for fila in f._cellvalues:
                partes.extend(c.getPlainText() if isinstance(c, Paragraph) else str(c) for c in fila)
    return '\n'.join(partes)


class PdfManifiestoTest(_ConInspeccion, TestCase):
    def setUp(self):
        self._crear_inspeccion()

    def _seccion(self):
        from .services.pdf_generator import _seccion_manifiesto, _styles
        self.inspeccion.refresh_from_db()
        return _texto(_seccion_manifiesto(self.inspeccion, _styles()))

    def test_muestra_raiz_y_sello_cualificado(self):
        from django.utils import timezone
        Inspeccion.objects.filter(pk=self.inspeccion.pk).update(
            manifiesto_raiz='ab' * 32, manifiesto={'hojas': [{}, {}]}, manifiesto_tsa_token=b'tok',
            manifiesto_tsa_timestamp=timezone.now(), manifiesto_tsa_cualificada=True,
            manifiesto_tsa_url='https://cualificada.example/tsr',
        )
        texto = self._seccion()
        self.assertIn('Manifiesto', texto)
        self.assertIn('ab' * 32, texto.replace('\n', '').replace(' ', ''))
        self.assertIn('cualificado', texto)
        self.assertIn('cualificada.example', texto)

    def test_sello_no_cualificado_no_se_presenta_como_cualificado(self):
        from django.utils import timezone
        Inspeccion.objects.filter(pk=self.inspeccion.pk).update(
            manifiesto_raiz='ab' * 32, manifiesto={'hojas': [{}]}, manifiesto_tsa_token=b'tok',
            manifiesto_tsa_timestamp=timezone.now(), manifiesto_tsa_cualificada=False,
            manifiesto_tsa_url='https://freetsa.org/tsr',
        )
        self.assertIn('no cualificado', self._seccion())

    def test_indica_manifiesto_pendiente(self):
        self.assertIn('pendiente', self._seccion().lower())

    def test_informe_completo_se_genera_con_la_seccion(self):
        from .services.pdf_generator import generar_informe_base
        with tempfile.TemporaryDirectory() as d:
            ruta = generar_informe_base(self.inspeccion, os.path.join(d, 'a.pdf'))
            self.assertGreater(os.path.getsize(ruta), 0)


class FirmaPadesTest(TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        from reportlab.pdfgen import canvas
        self.entrada = os.path.join(self.dir.name, 'entrada.pdf')
        c = canvas.Canvas(self.entrada)
        c.drawString(100, 750, 'Informe de prueba')
        c.save()
        self.salida = os.path.join(self.dir.name, 'salida.pdf')
        _, self.dummy = _crear_tsa()

    def _firma(self):
        from pyhanko.pdf_utils.reader import PdfFileReader
        with open(self.salida, 'rb') as f:
            firma = PdfFileReader(f).embedded_signatures[0]
            atributos = firma.signer_info['unsigned_attrs'].native or []
            return firma.sig_object['/SubFilter'], firma.signer_cert, atributos

    @patch.dict(os.environ, {'PDF_FIRMA_SELLO_TIEMPO': 'true'})
    def test_firma_pades_incluye_sello_de_tiempo_de_la_tsa(self):
        from .services import pdf_signer
        with patch('inspecciones.services.tsa.requests.post', side_effect=_post_con_tsa(self.dummy)):
            pdf_signer.firmar_pdf_reclamacion(self.entrada, self.salida)
        subfilter, _, atributos = self._firma()
        self.assertEqual(subfilter, '/ETSI.CAdES.detached')
        self.assertTrue(any(a['type'] == 'signature_time_stamp_token' for a in atributos))

    @patch.dict(os.environ, {'PDF_FIRMA_SELLO_TIEMPO': 'true'})
    def test_tsa_caida_firma_sin_sello_y_avisa(self):
        import requests
        from .services import pdf_signer
        with patch('inspecciones.services.tsa.requests.post', side_effect=requests.exceptions.Timeout()):
            with self.assertLogs('inspecciones.services.pdf_signer', level='WARNING'):
                pdf_signer.firmar_pdf_reclamacion(self.entrada, self.salida)
        _, _, atributos = self._firma()
        self.assertFalse(any(a['type'] == 'signature_time_stamp_token' for a in atributos))

    @patch.dict(os.environ, {'PDF_FIRMA_SELLO_TIEMPO': 'false'})
    def test_sello_desactivado_no_llama_a_la_tsa(self):
        from .services import pdf_signer
        with patch('inspecciones.services.tsa.requests.post') as post:
            pdf_signer.firmar_pdf_reclamacion(self.entrada, self.salida)
        post.assert_not_called()

    def test_carga_firmante_pkcs12(self):
        from cryptography.hazmat.primitives.serialization import pkcs12
        from .services import pdf_signer
        clave, cert = _certificado('Gestora S.L.')
        ruta = os.path.join(self.dir.name, 'sello.p12')
        with open(ruta, 'wb') as f:
            f.write(pkcs12.serialize_key_and_certificates(
                b'sello', clave, cert, None, serialization.BestAvailableEncryption(b'pass'),
            ))
        with patch.dict(os.environ, {'PDF_SIGNING_PKCS12_PATH': ruta, 'PDF_SIGNING_PKCS12_PASSWORD': 'pass',
                                     'PDF_FIRMA_SELLO_TIEMPO': 'false'}):
            pdf_signer.firmar_pdf_reclamacion(self.entrada, self.salida)
        _, firmante, _ = self._firma()
        self.assertEqual(firmante.subject.native['common_name'], 'Gestora S.L.')


# ---------------------------------------------------------------------------
# Correcciones de las revisiones (seguridad y Django)
# ---------------------------------------------------------------------------

def _crear_tsa_con(nombre='TSA', eku=ExtendedKeyUsageOID.TIME_STAMPING, dias=(-1, 30)):
    """Como _crear_tsa, con EKU y ventana de validez configurables."""
    clave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    sujeto = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, nombre)])
    ahora = datetime.datetime.now(datetime.timezone.utc)
    builder = (
        x509.CertificateBuilder().subject_name(sujeto).issuer_name(sujeto)
        .public_key(clave.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(ahora + datetime.timedelta(days=dias[0]))
        .not_valid_after(ahora + datetime.timedelta(days=dias[1]))
    )
    if eku:
        builder = builder.add_extension(x509.ExtendedKeyUsage([eku]), critical=True)
    cert = builder.sign(clave, hashes.SHA256())
    der_clave = clave.private_bytes(
        serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
    )
    dummy = DummyTimeStamper(
        tsa_cert=asn1_x509.Certificate.load(cert.public_bytes(serialization.Encoding.DER)),
        tsa_key=asn1_keys.PrivateKeyInfo.load(der_clave),
    )
    return cert.public_bytes(serialization.Encoding.PEM), dummy


class TSAEndurecidaTest(TestCase):
    def _solicitar(self, *args, **kwargs):
        from .services.tsa import solicitar_sello_tiempo
        return solicitar_sello_tiempo(*args, **kwargs)

    @override_settings(TSA_MANIFIESTO_URL='https://cualificada.example/tsr',
                       TSA_MANIFIESTO_CUALIFICADA=True, TSA_MANIFIESTO_CERT='')
    def test_sin_certificado_fijado_no_se_declara_cualificado(self):
        _, dummy = _crear_tsa()
        with patch('inspecciones.services.tsa.requests.post', side_effect=_post_con_tsa(dummy)):
            datos = self._solicitar(HASH_A, perfil='manifiesto')
        self.assertFalse(datos['verificado'])
        self.assertFalse(datos['cualificada'])

    def test_http_sin_certificado_fijado_se_rechaza(self):
        _, dummy = _crear_tsa()
        with self.settings(TSA_URL='http://tsa.example/tsr', TSA_CERT=''), \
                patch('inspecciones.services.tsa.requests.post', side_effect=_post_con_tsa(dummy)) as post:
            with self.assertLogs('inspecciones.services.tsa', level='WARNING'):
                self.assertIsNone(self._solicitar(HASH_A))
        post.assert_not_called()

    def test_http_con_certificado_fijado_se_admite(self):
        pem, dummy = _crear_tsa()
        with self.settings(TSA_URL='http://tsa.example/tsr', TSA_CERT=_guardar_pem(self, pem)), \
                patch('inspecciones.services.tsa.requests.post', side_effect=_post_con_tsa(dummy)):
            datos = self._solicitar(HASH_A)
        self.assertTrue(datos['verificado'])

    def test_certificado_fijado_sin_eku_timestamping_se_rechaza(self):
        pem, dummy = _crear_tsa_con(eku=ExtendedKeyUsageOID.CODE_SIGNING)
        with self.settings(TSA_CERT=_guardar_pem(self, pem)), \
                patch('inspecciones.services.tsa.requests.post', side_effect=_post_con_tsa(dummy)):
            with self.assertLogs('inspecciones.services.tsa', level='ERROR'):
                self.assertIsNone(self._solicitar(HASH_A))

    def test_certificado_fijado_caducado_en_la_hora_del_sello_se_rechaza(self):
        pem, dummy = _crear_tsa_con(dias=(-30, -1))
        with self.settings(TSA_CERT=_guardar_pem(self, pem)), \
                patch('inspecciones.services.tsa.requests.post', side_effect=_post_con_tsa(dummy)):
            with self.assertLogs('inspecciones.services.tsa', level='ERROR'):
                self.assertIsNone(self._solicitar(HASH_A))

    @override_settings(TSA_URL='https://usuario:secreto@tsa.example/tsr?clave=otra')
    def test_logs_y_url_guardada_sin_credenciales(self):
        resp = _respuesta_http(b'', 503)
        resp.text = 'eco de cabeceras: Authorization: Basic c2VjcmV0bw=='
        with patch('inspecciones.services.tsa.requests.post', return_value=resp):
            with self.assertLogs('inspecciones.services.tsa', level='WARNING') as logs:
                self._solicitar(HASH_A)
        salida = '\n'.join(logs.output)
        self.assertNotIn('secreto', salida)
        self.assertNotIn('otra', salida)
        self.assertNotIn('Authorization', salida)
        self.assertIn('tsa.example', salida)
        from .services.tsa import url_segura
        self.assertEqual(url_segura('https://u:p@tsa.example:8443/tsr?k=v#f'), 'https://tsa.example:8443/tsr')

    def test_soft_time_limit_no_se_traga(self):
        from celery.exceptions import SoftTimeLimitExceeded
        with patch('inspecciones.services.tsa.requests.post', side_effect=SoftTimeLimitExceeded()):
            with self.assertRaises(SoftTimeLimitExceeded):
                self._solicitar(HASH_A)


class ManifiestoCicloDeVidaTest(_ConInspeccion, APITestCase):
    def setUp(self):
        self._crear_inspeccion()

    def _patch_estado(self, usuario, estado):
        self.client.force_authenticate(usuario)
        with patch('inspecciones.tasks.sellar_manifiesto_async.apply_async'), \
                self.captureOnCommitCallbacks(execute=True):
            resp = self.client.patch(f'/api/v1/inspecciones/{self.inspeccion.pk}/', {'estado': estado}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.content)
        self.inspeccion.refresh_from_db()

    def test_se_congela_en_la_misma_transaccion_que_la_transicion(self):
        self._evidencia(HASH_A)
        self._patch_estado(self.operario, 'COMPLETADA')
        esperada = merkle.raiz([bytes.fromhex(HASH_A)]).hex()
        self.assertEqual(self.inspeccion.manifiesto_raiz, esperada)

    @patch('inspecciones.services.manifiesto.solicitar_sello_tiempo')
    def test_reabrir_archiva_el_sellado_y_recompletar_sella_el_conjunto_nuevo(self, mock_tsa):
        from .services.manifiesto import sellar_manifiesto
        mock_tsa.return_value = _sello()
        self._evidencia(HASH_A)
        self._patch_estado(self.operario, 'COMPLETADA')
        sellar_manifiesto(self.inspeccion.pk)
        self.inspeccion.refresh_from_db()
        raiz_vieja = self.inspeccion.manifiesto_raiz

        self._patch_estado(self.admin, 'EN_PROGRESO')
        self.assertIsNone(self.inspeccion.manifiesto_raiz)
        self.assertIsNone(self.inspeccion.manifiesto_tsa_token)
        self.assertEqual(len(self.inspeccion.manifiestos_anteriores), 1)
        archivado = self.inspeccion.manifiestos_anteriores[0]
        self.assertEqual(archivado['raiz'], raiz_vieja)
        import base64
        self.assertEqual(base64.b64decode(archivado['tsa_token']), b'token-manifiesto')

        self._evidencia(HASH_B)
        self._patch_estado(self.operario, 'COMPLETADA')
        self.assertTrue(sellar_manifiesto(self.inspeccion.pk))
        self.inspeccion.refresh_from_db()
        nueva = merkle.raiz([bytes.fromhex(HASH_A), bytes.fromhex(HASH_B)]).hex()
        self.assertEqual(self.inspeccion.manifiesto_raiz, nueva)
        mock_tsa.assert_called_with(nueva, perfil='manifiesto')

    def test_api_no_expone_token_ni_manifiesto_completo(self):
        self._evidencia(HASH_A)
        self._patch_estado(self.operario, 'COMPLETADA')
        self.client.force_authenticate(self.admin)
        datos = self.client.get(f'/api/v1/inspecciones/{self.inspeccion.pk}/').json()
        self.assertIn('manifiesto_raiz', datos)
        for campo in ('manifiesto', 'manifiesto_tsa_token', 'manifiestos_anteriores', 'manifiesto_tsa_ultimo_intento'):
            self.assertNotIn(campo, datos)

    @patch('inspecciones.views.solicitar_sello_tiempo', return_value=None)
    def test_hash_mal_formado_se_rechaza(self, mock_tsa):
        self.client.force_authenticate(self.operario)
        for valor in ('no-es-un-hash', 'g' * 64, 'a' * 63):
            with self.subTest(valor=valor):
                resp = self.client.post(
                    f'/api/v1/inspecciones/{self.inspeccion.pk}/evidencias/',
                    {'foto': _make_image(), 'hash_sha256': valor}, format='multipart',
                )
                self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        mock_tsa.assert_not_called()
        self.assertFalse(Evidencia.objects.exists())

    @patch('inspecciones.services.manifiesto.solicitar_sello_tiempo')
    def test_guarda_si_el_sello_se_verifico(self, mock_tsa):
        from .services.manifiesto import sellar_manifiesto
        mock_tsa.return_value = _sello(cualificada=False, verificado=True)
        self._completar()
        sellar_manifiesto(self.inspeccion.pk)
        self.inspeccion.refresh_from_db()
        self.assertTrue(self.inspeccion.manifiesto_tsa_verificado)

    @patch('inspecciones.services.manifiesto.solicitar_sello_tiempo', return_value=None)
    def test_pdf_lleva_la_raiz_aunque_la_tsa_falle(self, _):
        self._evidencia(HASH_A)
        self._completar()
        raices = []

        def capturar(inspeccion, ruta):
            raices.append(inspeccion.manifiesto_raiz)
            with open(ruta, 'wb') as f:
                f.write(b'%PDF-1.4')
            return ruta
        from .tasks import generar_pdf_async
        with patch('inspecciones.services.pdf_generator.generar_informe_base', side_effect=capturar), \
                patch('inspecciones.services.pdf_signer.firmar_pdf_reclamacion',
                      side_effect=lambda entrada, salida: os.replace(entrada, salida)):
            generar_pdf_async(self.inspeccion.pk)
        self.assertEqual(raices, [merkle.raiz([bytes.fromhex(HASH_A)]).hex()])


class ClaveFirmaCheckTest(TestCase):
    def _avisos(self):
        from .checks import check_clave_firma_por_defecto
        return [a.id for a in check_clave_firma_por_defecto(None)]

    @override_settings(DEBUG=False)
    def test_avisa_con_la_clave_por_defecto_en_produccion(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('PDF_SIGNING_PKCS12_PATH', None)
            os.environ.pop('PDF_SIGNING_KEY_PATH', None)
            self.assertEqual(self._avisos(), ['inspecciones.W001'])

    @override_settings(DEBUG=False)
    def test_sin_aviso_con_pkcs12_o_clave_propia(self):
        with patch.dict(os.environ, {'PDF_SIGNING_PKCS12_PATH': '/etc/secrets/sello.p12'}):
            self.assertEqual(self._avisos(), [])
        with patch.dict(os.environ, {'PDF_SIGNING_KEY_PATH': '/etc/secrets/firma.key'}):
            os.environ.pop('PDF_SIGNING_PKCS12_PATH', None)
            self.assertEqual(self._avisos(), [])

    @override_settings(DEBUG=True)
    def test_sin_aviso_en_desarrollo(self):
        self.assertEqual(self._avisos(), [])
