"""
Tests del paquete probatorio: el ZIP por inspección con fotos originales,
sellos TSA, manifiesto Merkle con la ruta de inclusión de cada foto y el PDF
firmado, verificable sin CheckIt.

Los sellos los emite la TSA de pruebas de tests_sellos (DummyTimeStamper de
pyHanko), porque verificar.py lee la huella que sella cada token.
"""
import base64
import hashlib
import io
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import zipfile
from unittest import skipUnless
from unittest.mock import patch

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from core.testing import limitar
from propiedades.models import Propiedad
from users.models import User

from .models import Evidencia, Inspeccion
from .services import merkle
from .services.manifiesto import congelar_manifiesto, reabrir_manifiesto
from .tests import _make_image
from .tests_sellos import _crear_tsa, _guardar_pem, _post_con_tsa

PDF = b'%PDF-1.7 firmado'

_TSA = []


def _tsa():
    """TSA de pruebas compartida por todo el módulo (generar la clave RSA cuesta)."""
    if not _TSA:
        _TSA.append(_crear_tsa())
    return _TSA[0]


def _sellar(hash_hex):
    from .services.tsa import solicitar_sello_tiempo
    with override_settings(TSA_URL='https://tsa.example/tsr', TSA_CERT='', TSA_MANIFIESTO_URL=''), \
            patch('inspecciones.services.tsa.requests.post', side_effect=_post_con_tsa(_tsa()[1])):
        return solicitar_sello_tiempo(hash_hex)['token']


def _foto(n):
    # Cada foto distinta: el color depende de n
    from django.core.files.uploadedfile import SimpleUploadedFile
    from PIL import Image
    buf = io.BytesIO()
    Image.new('RGB', (40, 40), color=(n * 7 % 256, 100, 200)).save(buf, format='JPEG')
    return SimpleUploadedFile(f'foto{n}.jpg', buf.getvalue(), content_type='image/jpeg')


class _ConExpediente:
    """Inspección completada con `n` fotos selladas, manifiesto sellado y PDF de esa raíz."""

    def _crear_expediente(self, n=2):
        self.admin = User.objects.create_user(username='admin_p', password='Pass1234!', role=User.Role.ADMIN)
        self.operario = User.objects.create_user(username='op_p', password='Pass1234!', role=User.Role.OPERARIO)
        self.prop = Propiedad.objects.create(owner=self.admin, nombre='Piso Centro', direccion='Calle 1')
        self.inspeccion = Inspeccion.objects.create(propiedad=self.prop, operario=self.operario)
        self.fotos = [self._evidencia(_foto(i)) for i in range(n)]
        Inspeccion.objects.filter(pk=self.inspeccion.pk).update(estado=Inspeccion.Estado.COMPLETADA)
        congelar_manifiesto(self.inspeccion.pk)
        raiz = Inspeccion.objects.values_list('manifiesto_raiz', flat=True).get(pk=self.inspeccion.pk)
        Inspeccion.objects.filter(pk=self.inspeccion.pk).update(
            manifiesto_tsa_token=_sellar(raiz), manifiesto_tsa_timestamp=timezone.now(),
            manifiesto_tsa_url='https://tsa.example/tsr', manifiesto_tsa_cualificada=False,
            manifiesto_tsa_verificado=True,
            pdf_estado=Inspeccion.PdfEstado.LISTO, pdf_manifiesto_raiz=raiz,
            pdf_ruta=default_storage.save('informes/paquete.pdf', ContentFile(PDF)),
        )
        self.inspeccion.refresh_from_db()

    def _evidencia(self, foto, hash_sha256=None, sellar=True):
        datos = foto.read()
        foto.seek(0)
        hash_real = hashlib.sha256(datos).hexdigest()
        evidencia = Evidencia.objects.create(
            inspeccion=self.inspeccion, foto=foto, descripcion=f'Foto {foto.name}',
            hash_sha256=hash_sha256 or hash_real,
            tsa_token=_sellar(hash_real) if sellar else None,
        )
        evidencia.datos = datos
        return evidencia


def _abrir(archivo):
    return zipfile.ZipFile(io.BytesIO(archivo.read()))


def _construir(inspeccion):
    from .services.paquete import construir_paquete
    return construir_paquete(inspeccion)


class PaqueteContenidoTest(_ConExpediente, TestCase):
    def setUp(self):
        self._crear_expediente()

    def _zip(self):
        return _abrir(_construir(self.inspeccion))

    def test_contiene_las_piezas_del_expediente(self):
        nombres = set(self._zip().namelist())
        for nombre in ('LEEME.txt', 'expediente.json', 'informe_firmado.pdf', 'manifiesto/manifiesto.json',
                       'manifiesto/raiz.tst', 'verificar.py', 'verificar_sellos.sh', 'SHA256SUMS'):
            self.assertIn(nombre, nombres)
        for i, ev in enumerate(self.fotos):
            base = f'evidencias/{i:03d}_ev{ev.pk}'
            for sufijo in ('.jpg', '.tst', '.prueba.json'):
                self.assertIn(base + sufijo, nombres)

    def test_fotos_pdf_y_sellos_son_los_originales(self):
        z = self._zip()
        self.assertEqual(z.read('informe_firmado.pdf'), PDF)
        self.assertEqual(z.read('manifiesto/raiz.tst'), bytes(self.inspeccion.manifiesto_tsa_token))
        for i, ev in enumerate(self.fotos):
            self.assertEqual(z.read(f'evidencias/{i:03d}_ev{ev.pk}.jpg'), ev.datos)
            self.assertEqual(z.read(f'evidencias/{i:03d}_ev{ev.pk}.tst'), bytes(ev.tsa_token))

    def test_manifiesto_es_el_congelado(self):
        self.assertEqual(json.loads(self._zip().read('manifiesto/manifiesto.json')), self.inspeccion.manifiesto)

    def test_cada_prueba_de_inclusion_verifica_contra_la_raiz(self):
        z = self._zip()
        raiz = bytes.fromhex(self.inspeccion.manifiesto_raiz)
        for i, ev in enumerate(self.fotos):
            prueba = json.loads(z.read(f'evidencias/{i:03d}_ev{ev.pk}.prueba.json'))
            self.assertEqual((prueba['indice'], prueba['total']), (i, 2))
            self.assertEqual(prueba['raiz'], self.inspeccion.manifiesto_raiz)
            self.assertTrue(merkle.verificar_inclusion(
                hashlib.sha256(ev.datos).digest(), i, 2, [bytes.fromhex(h) for h in prueba['ruta']], raiz,
            ))

    def test_sha256sums_cubre_todos_los_archivos(self):
        z = self._zip()
        lineas = z.read('SHA256SUMS').decode().splitlines()
        sumas = dict(reversed(linea.split('  ', 1)) for linea in lineas)
        self.assertEqual(set(sumas), set(z.namelist()) - {'SHA256SUMS'})
        for nombre, suma in sumas.items():
            self.assertEqual(hashlib.sha256(z.read(nombre)).hexdigest(), suma)

    def test_expediente_resume_inspeccion_y_sello(self):
        datos = json.loads(self._zip().read('expediente.json'))
        self.assertEqual(datos['inspeccion']['id'], self.inspeccion.pk)
        self.assertEqual(datos['inspeccion']['propiedad'], 'Piso Centro')
        self.assertEqual(datos['manifiesto']['raiz'], self.inspeccion.manifiesto_raiz)
        self.assertFalse(datos['manifiesto']['tsa_cualificada'])
        self.assertEqual(len(datos['evidencias']), 2)
        self.assertEqual(datos['evidencias'][0]['sha256'], hashlib.sha256(self.fotos[0].datos).hexdigest())
        self.assertEqual(datos['avisos'], [])
        # #29: la hora del dispositivo va aparte de la de recepción y la del sello
        self.assertIn('capturada_en', datos['evidencias'][0])
        self.assertIn('fecha_captura', datos['evidencias'][0])
        self.assertIs(datos['evidencias'][0]['capturada_en_certificada'], False)
        self.assertEqual(datos['evidencias'][0]['avisos_hora'], [])

    def test_leeme_no_presenta_como_cualificado_un_sello_que_no_lo_es(self):
        leeme = self._zip().read('LEEME.txt').decode()
        self.assertIn('NO cualificado', leeme)
        self.assertIn('openssl ts -verify', leeme)
        self.assertIn(self.inspeccion.manifiesto_raiz, leeme)
        self.assertIn('DATOS PERSONALES', leeme)
        self.assertIn('Nadie la\n  certifica', leeme)

    def test_leeme_dice_cualificado_solo_si_lo_es(self):
        Inspeccion.objects.filter(pk=self.inspeccion.pk).update(manifiesto_tsa_cualificada=True)
        self.inspeccion.refresh_from_db()
        leeme = self._zip().read('LEEME.txt').decode()
        self.assertIn('cualificado (eIDAS)', leeme)
        self.assertNotIn('NO cualificado', leeme)

    def test_evidencia_sin_sello_individual_no_lleva_tst(self):
        Evidencia.objects.filter(pk=self.fotos[1].pk).update(tsa_token=None)
        nombres = self._zip().namelist()
        self.assertNotIn(f'evidencias/001_ev{self.fotos[1].pk}.tst', nombres)

    def test_evidencia_fuera_del_manifiesto_va_aparte_con_aviso(self):
        # Datos antiguos: una evidencia sin hash válido no entró en el manifiesto
        vieja = self._evidencia(_foto(9), hash_sha256='no-es-un-hash', sellar=False)
        z = self._zip()
        self.assertIn(f'evidencias_sin_manifiesto/ev{vieja.pk}.jpg', z.namelist())
        avisos = json.loads(z.read('expediente.json'))['avisos']
        self.assertEqual(len(avisos), 1)
        self.assertIn('no está en el manifiesto', avisos[0])

    def test_incluye_manifiestos_anteriores_de_reaperturas(self):
        anterior = {'manifiesto': {'raiz': 'cd' * 32}, 'raiz': 'cd' * 32,
                    'tsa_token': base64.b64encode(b'token-viejo').decode(), 'archivado': timezone.now().isoformat()}
        Inspeccion.objects.filter(pk=self.inspeccion.pk).update(manifiestos_anteriores=[anterior])
        self.inspeccion.refresh_from_db()
        z = self._zip()
        self.assertEqual(z.read('manifiestos_anteriores/01_raiz.tst'), b'token-viejo')
        datos = json.loads(z.read('manifiestos_anteriores/01_manifiesto.json'))
        self.assertEqual(datos['raiz'], 'cd' * 32)
        self.assertNotIn('tsa_token', datos)

    def test_del_certificado_de_la_tsa_solo_se_copian_los_certificados(self):
        pem = _tsa()[0]
        ruta = _guardar_pem(self, b'-----BEGIN PRIVATE KEY-----\nsecreto\n-----END PRIVATE KEY-----\n' + pem)
        with override_settings(TSA_CERT=ruta, TSA_MANIFIESTO_URL=''):
            z = self._zip()
        copiado = z.read('certificados_tsa/manifiesto.pem')
        self.assertIn(b'BEGIN CERTIFICATE', copiado)
        self.assertNotIn(b'PRIVATE KEY', copiado)
        self.assertNotIn(b'secreto', copiado)


class _ConPaqueteExtraido(_ConExpediente):
    fotos_por_crear = 2

    def setUp(self):
        self._crear_expediente(self.fotos_por_crear)
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        _abrir(_construir(self.inspeccion)).extractall(self.dir.name)

    def _ruta(self, nombre):
        return os.path.join(self.dir.name, *nombre.split('/'))

    def _verificar(self):
        # Como un perito: desde otra carpeta, en modo aislado y con la carpeta como argumento
        return subprocess.run(
            [sys.executable, '-I', self._ruta('verificar.py'), self.dir.name],
            capture_output=True, text=True, timeout=30,
        )


class PaqueteVerificadorTest(_ConPaqueteExtraido, TestCase):
    """verificar.py se ejecuta como lo haría un perito: fuera de CheckIt, solo con Python."""

    def _falla(self, texto=None):
        resultado = self._verificar()
        self.assertNotEqual(resultado.returncode, 0, resultado.stdout)
        if texto:
            self.assertIn(texto, resultado.stdout)
        return resultado

    def test_paquete_intacto_verifica(self):
        resultado = self._verificar()
        self.assertEqual(resultado.returncode, 0, resultado.stdout + resultado.stderr)
        self.assertIn('RESULTADO: OK', resultado.stdout)
        self.assertIn('manifiesto/raiz.tst sella esa raíz', resultado.stdout)

    def test_foto_alterada_no_verifica(self):
        with open(self._ruta(f'evidencias/000_ev{self.fotos[0].pk}.jpg'), 'ab') as f:
            f.write(b'retoque')
        self._falla(f'000_ev{self.fotos[0].pk}.jpg')

    def test_manifiesto_alterado_no_verifica(self):
        with open(self._ruta('manifiesto/manifiesto.json')) as f:
            manifiesto = json.load(f)
        manifiesto['hojas'].reverse()
        with open(self._ruta('manifiesto/manifiesto.json'), 'w') as f:
            json.dump(manifiesto, f)
        self._falla()

    def _rehacer_sumas(self):
        nombres = []
        for carpeta, _, archivos in os.walk(self.dir.name):
            for archivo in archivos:
                nombre = os.path.relpath(os.path.join(carpeta, archivo), self.dir.name).replace(os.sep, '/')
                if nombre != 'SHA256SUMS':
                    nombres.append(nombre)
        with open(self._ruta('SHA256SUMS'), 'w') as f:
            for nombre in sorted(nombres):
                with open(self._ruta(nombre), 'rb') as g:
                    f.write(f'{hashlib.sha256(g.read()).hexdigest()}  {nombre}\n')

    def test_paquete_rehecho_con_otras_fotos_no_verifica_contra_el_sello(self):
        # El atacante cambia una foto y rehace hojas, raíz, pruebas y SHA256SUMS, pero no puede resellar
        nueva = _foto(50).read()
        with open(self._ruta(f'evidencias/000_ev{self.fotos[0].pk}.jpg'), 'wb') as f:
            f.write(nueva)
        hojas = [hashlib.sha256(nueva).digest(), hashlib.sha256(self.fotos[1].datos).digest()]
        with open(self._ruta('manifiesto/manifiesto.json')) as f:
            manifiesto = json.load(f)
        manifiesto['hojas'][0]['sha256'] = hojas[0].hex()
        manifiesto['raiz'] = merkle.raiz(hojas).hex()
        with open(self._ruta('manifiesto/manifiesto.json'), 'w') as f:
            json.dump(manifiesto, f)
        for i, ev in enumerate(self.fotos):
            nombre = f'evidencias/{i:03d}_ev{ev.pk}.prueba.json'
            with open(self._ruta(nombre)) as f:
                prueba = json.load(f)
            prueba['raiz'] = manifiesto['raiz']
            prueba['ruta'] = [h.hex() for h in merkle.prueba_inclusion(hojas, i)]
            with open(self._ruta(nombre), 'w') as f:
                json.dump(prueba, f)
        # Sin el sello de la foto cambiada, para que solo falle el de la raíz
        os.remove(self._ruta(f'evidencias/000_ev{self.fotos[0].pk}.tst'))
        with open(self._ruta('expediente.json')) as f:
            expediente = json.load(f)
        expediente['evidencias'][0]['sello'] = None
        with open(self._ruta('expediente.json'), 'w') as f:
            json.dump(expediente, f)
        self._rehacer_sumas()
        self._falla('raiz.tst sella')

    def test_sello_de_foto_de_otro_hash_no_verifica(self):
        otro = self._ruta(f'evidencias/001_ev{self.fotos[1].pk}.tst')
        shutil.copy(otro, self._ruta(f'evidencias/000_ev{self.fotos[0].pk}.tst'))
        self._rehacer_sumas()
        self._falla('no sella el hash')

    def test_sha256sums_sin_un_esencial_no_verifica(self):
        with open(self._ruta('SHA256SUMS')) as f:
            lineas = [linea for linea in f if not linea.rstrip().endswith('informe_firmado.pdf')]
        with open(self._ruta('SHA256SUMS'), 'w') as f:
            f.writelines(lineas)
        self._falla('informe_firmado.pdf: falta en SHA256SUMS')

    def test_archivo_no_listado_no_verifica(self):
        with open(self._ruta('evidencias/extra.jpg'), 'wb') as f:
            f.write(b'colado')
        self._falla('no en SHA256SUMS')

    def test_un_hashlib_falso_junto_al_script_no_se_importa(self):
        with open(self._ruta('hashlib.py'), 'w') as f:
            f.write('raise SystemExit("importado hashlib.py del paquete")\n')
        resultado = self._verificar()
        self.assertNotIn('importado hashlib.py del paquete', resultado.stdout + resultado.stderr)
        # Y el archivo colado se detecta
        self.assertIn('hashlib.py', resultado.stdout)

    def test_enlace_simbolico_no_se_sigue(self):
        nombre = f'evidencias/000_ev{self.fotos[0].pk}.jpg'
        os.remove(self._ruta(nombre))
        os.symlink('/dev/zero', self._ruta(nombre))
        self._falla('no es un archivo normal')


class PaqueteVerificadorTamañosTest(TestCase):
    """Rutas de inclusión con árboles no completos (1, 3 y 5 hojas)."""

    def test_verifica_con_distinto_numero_de_fotos(self):
        for n in (1, 3, 5):
            with self.subTest(fotos=n):
                caso = type('Caso', (_ConPaqueteExtraido,), {'fotos_por_crear': n})()
                caso.addCleanup = self.addCleanup
                User.objects.all().delete()
                caso.setUp()
                resultado = caso._verificar()
                self.assertEqual(resultado.returncode, 0, resultado.stdout + resultado.stderr)


@skipUnless(shutil.which('openssl'), 'sin openssl')
class PaqueteOpensslTest(_ConPaqueteExtraido, TestCase):
    """Los comandos de openssl del paquete, tal cual, contra los sellos de la TSA de pruebas."""

    def setUp(self):
        ruta = _guardar_pem(self, _tsa()[0])
        with override_settings(TSA_CERT=ruta, TSA_MANIFIESTO_URL=''):
            super().setUp()

    def _comandos(self, nombre):
        with open(self._ruta(nombre)) as f:
            return [linea.strip() for linea in f if linea.strip().startswith('openssl ts -verify')]

    def test_los_sellos_verifican_con_los_comandos_del_paquete(self):
        leeme = self._comandos('LEEME.txt')
        script = self._comandos('verificar_sellos.sh')
        self.assertEqual(len(leeme), 1)
        self.assertEqual(len(script), 3)  # la raíz y las dos fotos
        for comando in leeme + script:
            resultado = subprocess.run(shlex.split(comando), cwd=self.dir.name, capture_output=True, text=True,
                                       timeout=30)
            self.assertIn('Verification: OK', resultado.stdout, comando + resultado.stderr)

    def test_no_verifica_contra_una_raiz_distinta(self):
        otro = shlex.split(self._comandos('LEEME.txt')[0].replace(self.inspeccion.manifiesto_raiz, 'ab' * 32))
        resultado = subprocess.run(otro, cwd=self.dir.name, capture_output=True, text=True, timeout=30)
        self.assertNotIn('Verification: OK', resultado.stdout)


class PaquetePrecondicionesTest(_ConExpediente, TestCase):
    def setUp(self):
        self._crear_expediente()

    def _falla(self, motivo):
        from .services.paquete import PaqueteNoDisponible
        self.inspeccion.refresh_from_db()
        with self.assertRaises(PaqueteNoDisponible) as ctx:
            _construir(self.inspeccion)
        self.assertIn(motivo, str(ctx.exception))

    def test_exige_inspeccion_completada(self):
        Inspeccion.objects.filter(pk=self.inspeccion.pk).update(estado=Inspeccion.Estado.EN_PROGRESO)
        self._falla('completada')

    def test_exige_manifiesto_sellado(self):
        Inspeccion.objects.filter(pk=self.inspeccion.pk).update(manifiesto_tsa_token=None)
        self._falla('sellado')

    def test_exige_pdf_listo(self):
        Inspeccion.objects.filter(pk=self.inspeccion.pk).update(pdf_estado=Inspeccion.PdfEstado.PENDIENTE)
        self._falla('informe')

    def test_exige_que_el_pdf_exista(self):
        default_storage.delete(self.inspeccion.pdf_ruta)
        self._falla('informe')

    def test_exige_que_el_pdf_sea_de_la_raiz_vigente(self):
        Inspeccion.objects.filter(pk=self.inspeccion.pk).update(pdf_manifiesto_raiz='ab' * 32)
        self._falla('versión anterior')

    def test_storage_caido_no_se_presenta_como_integridad_rota(self):
        from .services.paquete import PaqueteNoDisponible
        default_storage.exists('')  # default_storage es perezoso: se fuerza a crear el storage real
        with patch.object(default_storage._wrapped, 'exists', side_effect=ConnectionError('R2 no responde')):
            with self.assertRaises(PaqueteNoDisponible) as ctx:
                # El PDF se abre antes: se deja pasar y falla al leer las fotos
                with patch('inspecciones.services.paquete.abrir_pdf', return_value=io.BytesIO(PDF)):
                    _construir(self.inspeccion)
        self.assertIn('no responde', str(ctx.exception))

    def test_si_la_inspeccion_cambia_mientras_se_construye_no_se_entrega(self):
        from .services import paquete
        original = paquete._añadir_manifiestos_anteriores

        def reabrir_a_mitad(*args):
            reabrir_manifiesto(self.inspeccion.pk)
            return original(*args)

        with patch.object(paquete, '_añadir_manifiestos_anteriores', side_effect=reabrir_a_mitad):
            self._falla('ha cambiado')

    def test_foto_cambiada_en_el_storage_rompe_la_integridad(self):
        from .services.paquete import IntegridadRota
        nombre = self.fotos[0].foto.name
        default_storage.delete(nombre)
        default_storage.save(nombre, ContentFile(b'otra foto'))
        with self.assertRaises(IntegridadRota) as ctx:
            _construir(self.inspeccion)
        self.assertIn(str(self.fotos[0].pk), str(ctx.exception))

    def test_foto_desaparecida_rompe_la_integridad(self):
        from .services.paquete import IntegridadRota
        default_storage.delete(self.fotos[1].foto.name)
        with self.assertRaises(IntegridadRota):
            _construir(self.inspeccion)

    def test_evidencia_borrada_del_manifiesto_rompe_la_integridad(self):
        from .services.paquete import IntegridadRota
        Evidencia.objects.filter(pk=self.fotos[0].pk).delete()
        with self.assertRaises(IntegridadRota):
            _construir(self.inspeccion)


class PdfYReaperturaTest(_ConExpediente, TestCase):
    def setUp(self):
        self._crear_expediente()

    def test_reabrir_deja_el_pdf_pendiente(self):
        reabrir_manifiesto(self.inspeccion.pk)
        self.inspeccion.refresh_from_db()
        self.assertEqual(self.inspeccion.pdf_estado, Inspeccion.PdfEstado.PENDIENTE)

    @patch('inspecciones.services.pdf_signer.firmar_pdf_reclamacion')
    @patch('inspecciones.services.pdf_generator.generar_informe_base')
    def test_el_pdf_guarda_la_raiz_que_lleva(self, mock_base, mock_firma):
        from .tasks import generar_pdf_async
        mock_base.side_effect = lambda insp, ruta: open(ruta, 'wb').close() or ruta
        mock_firma.side_effect = lambda entrada, salida: open(salida, 'wb').write(PDF)
        # Como claim_report: reserva GENERANDO antes de encolar
        Inspeccion.objects.filter(pk=self.inspeccion.pk).update(
            pdf_manifiesto_raiz=None, pdf_estado=Inspeccion.PdfEstado.GENERANDO,
        )
        generar_pdf_async.apply(args=[self.inspeccion.pk])
        self.inspeccion.refresh_from_db()
        self.assertEqual(self.inspeccion.pdf_estado, Inspeccion.PdfEstado.LISTO)
        self.assertEqual(self.inspeccion.pdf_manifiesto_raiz, self.inspeccion.manifiesto_raiz)

    @patch('inspecciones.services.pdf_signer.firmar_pdf_reclamacion')
    @patch('inspecciones.services.pdf_generator.generar_informe_base')
    def test_si_la_raiz_cambia_durante_la_generacion_el_pdf_se_descarta(self, mock_base, mock_firma):
        from .tasks import generar_pdf_async
        mock_base.side_effect = lambda insp, ruta: open(ruta, 'wb').close() or ruta

        def firmar_y_reabrir(entrada, salida):
            Inspeccion.objects.filter(pk=self.inspeccion.pk).update(manifiesto_raiz='ef' * 32)
            open(salida, 'wb').write(PDF)

        mock_firma.side_effect = firmar_y_reabrir
        Inspeccion.objects.filter(pk=self.inspeccion.pk).update(pdf_estado=Inspeccion.PdfEstado.GENERANDO)
        generar_pdf_async.apply(args=[self.inspeccion.pk])
        self.inspeccion.refresh_from_db()
        self.assertEqual(self.inspeccion.pdf_estado, Inspeccion.PdfEstado.PENDIENTE)
        self.assertNotEqual(self.inspeccion.pdf_manifiesto_raiz, 'ef' * 32)


class PaqueteDescargaTest(_ConExpediente, APITestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()  # el throttle guarda contadores en caché
        self.addCleanup(cache.clear)
        self._crear_expediente()
        self.url = f'/api/v1/inspecciones/{self.inspeccion.pk}/paquete-probatorio/'

    def test_admin_propietario_descarga_el_zip(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp['Content-Type'], 'application/zip')
        self.assertIn('no-store', resp['Cache-Control'])
        self.assertIn(f'CheckIt_Expediente_{self.inspeccion.pk}.zip', resp['Content-Disposition'])
        z = zipfile.ZipFile(io.BytesIO(b''.join(resp.streaming_content)))
        self.assertIn('SHA256SUMS', z.namelist())

    def test_operario_no_puede_descargarlo(self):
        self.client.force_authenticate(self.operario)
        self.assertEqual(self.client.get(self.url).status_code, status.HTTP_403_FORBIDDEN)

    def test_otro_admin_recibe_404(self):
        otro = User.objects.create_user(username='admin_otro', password='Pass1234!', role=User.Role.ADMIN)
        self.client.force_authenticate(otro)
        self.assertEqual(self.client.get(self.url).status_code, status.HTTP_404_NOT_FOUND)

    def test_sin_autenticar_no_puede_descargarlo(self):
        self.assertEqual(self.client.get(self.url).status_code, status.HTTP_401_UNAUTHORIZED)

    def test_precondicion_incumplida_devuelve_409_con_motivo(self):
        Inspeccion.objects.filter(pk=self.inspeccion.pk).update(pdf_estado=Inspeccion.PdfEstado.PENDIENTE)
        self.client.force_authenticate(self.admin)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT)
        self.assertIn('informe', resp.json()['error'])

    def test_integridad_rota_devuelve_409_y_no_entrega_nada(self):
        default_storage.delete(self.fotos[0].foto.name)
        default_storage.save(self.fotos[0].foto.name, ContentFile(b'otra foto'))
        self.client.force_authenticate(self.admin)
        with self.assertLogs('inspecciones.services.paquete', level='ERROR'):
            resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT)
        self.assertIn('integridad', resp.json()['error'])

    def test_tiene_cuota_propia(self):
        self.client.force_authenticate(self.admin)
        with limitar(paquete='2/hour'):
            codigos = [self.client.get(self.url).status_code for _ in range(3)]
        self.assertEqual(codigos, [status.HTTP_200_OK] * 2 + [status.HTTP_429_TOO_MANY_REQUESTS])
