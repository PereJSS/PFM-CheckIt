"""
Paquete probatorio por inspección: un ZIP que un perito o un abogado puede
verificar sin CheckIt, solo con sha256sum, openssl y Python.

Lleva las fotos originales, el sello TSA de cada una, el manifiesto Merkle
congelado con el sello de su raíz, la ruta de inclusión de cada foto, los
manifiestos sellados de completados anteriores y el PDF firmado.

Se construye bajo demanda con lo ya guardado y sellado, y no se guarda. Antes
de entregarlo se recalcula el hash de cada foto: si alguna no coincide con el
manifiesto sellado, no se entrega nada.
"""
import base64
import hashlib
import json
import logging
import os
import re
import tempfile
import zipfile
from pathlib import Path

from django.utils import timezone

from ..models import Inspeccion
from . import merkle
from .almacen import abrir_pdf
from .tsa import PERFIL_EVIDENCIAS, PERFIL_MANIFIESTO, configuracion, url_segura

logger = logging.getLogger(__name__)

VERSION_PAQUETE = 1
_VERIFICADOR = Path(__file__).with_name('verificador_paquete.py')
# En memoria hasta este tamaño; por encima, a disco
_MAX_EN_MEMORIA = 16 * 1024 * 1024
_EXTENSION = re.compile(r'^\.[a-z0-9]{1,5}$')


class PaqueteNoDisponible(Exception):
    """La inspección aún no tiene todo lo que el paquete necesita."""


class IntegridadRota(Exception):
    """Lo guardado no coincide con lo sellado: el paquete no se entrega."""


def nombre_paquete(inspeccion_id):
    return f'CheckIt_Expediente_{inspeccion_id}.zip'


def _abrir_pdf_listo(inspeccion):
    if inspeccion.estado != Inspeccion.Estado.COMPLETADA:
        raise PaqueteNoDisponible('La inspección no está completada.')
    if not inspeccion.manifiesto or not inspeccion.manifiesto_tsa_token:
        raise PaqueteNoDisponible('El manifiesto de la inspección aún no está sellado por la TSA.')
    if inspeccion.pdf_estado != Inspeccion.PdfEstado.LISTO:
        raise PaqueteNoDisponible('Genera antes el informe PDF firmado.')
    if inspeccion.pdf_manifiesto_raiz != inspeccion.manifiesto_raiz:
        raise PaqueteNoDisponible('El informe PDF es de una versión anterior de la inspección; genéralo de nuevo.')
    pdf = abrir_pdf(inspeccion)
    if pdf is None:
        raise PaqueteNoDisponible('El informe PDF firmado no está disponible; genéralo de nuevo.')
    return pdf


def _leer_foto(evidencia):
    """Bytes de la foto o None si no existe. Si el storage falla, PaqueteNoDisponible:
    un corte de red no es una prueba manipulada."""
    try:
        if not evidencia.foto or not evidencia.foto.storage.exists(evidencia.foto.name):
            return None
        with evidencia.foto.open('rb') as f:
            return f.read()
    except FileNotFoundError:
        return None
    except Exception as e:
        logger.warning('No se pudo leer la foto de la evidencia %s', evidencia.pk, exc_info=True)
        raise PaqueteNoDisponible('El almacén de fotos no responde; inténtalo de nuevo en unos minutos.') from e


def _extension(evidencia):
    ext = os.path.splitext(evidencia.foto.name or '')[1].lower()
    return ext if _EXTENSION.match(ext) else '.bin'


def _iso(fecha):
    return fecha.isoformat() if fecha else None


class _Zip:
    """ZIP en un archivo temporal que va anotando el SHA-256 de cada entrada."""

    def __init__(self):
        self.archivo = tempfile.SpooledTemporaryFile(max_size=_MAX_EN_MEMORIA)
        self.zip = zipfile.ZipFile(self.archivo, 'w')
        self.sumas = []

    def añadir(self, nombre, datos, comprimir=True):
        if isinstance(datos, str):
            datos = datos.encode('utf-8')
        # Fotos y PDF ya van comprimidos: se guardan tal cual
        metodo = zipfile.ZIP_DEFLATED if comprimir else zipfile.ZIP_STORED
        self.zip.writestr(nombre, datos, compress_type=metodo)
        self.sumas.append((hashlib.sha256(datos).hexdigest(), nombre))

    def json(self, nombre, datos):
        self.añadir(nombre, json.dumps(datos, ensure_ascii=False, indent=2) + '\n')

    def cerrar(self):
        self.añadir('SHA256SUMS', ''.join(f'{suma}  {nombre}\n' for suma, nombre in self.sumas))
        self.zip.close()
        self.archivo.seek(0)
        return self.archivo

    def descartar(self):
        self.zip.close()
        self.archivo.close()


def _añadir_evidencias(paquete, inspeccion):
    """Fotos del manifiesto con su sello y su prueba; devuelve (fichas, avisos)."""
    manifiesto = inspeccion.manifiesto
    hojas = [bytes.fromhex(h['sha256']) for h in manifiesto['hojas']]
    evidencias = inspeccion.evidencias.in_bulk()
    fichas = []
    for indice, hoja in enumerate(manifiesto['hojas']):
        evidencia = evidencias.pop(hoja['evidencia'], None)
        datos = _leer_foto(evidencia) if evidencia else None
        if datos is None or hashlib.sha256(datos).hexdigest() != hoja['sha256']:
            logger.error(
                'Paquete probatorio de la inspección %s: la evidencia %s no coincide con el manifiesto sellado',
                inspeccion.pk, hoja['evidencia'],
            )
            raise IntegridadRota(
                f'La evidencia {hoja["evidencia"]} no coincide con el manifiesto sellado de la inspección {inspeccion.pk}.'
            )
        base = f'evidencias/{indice:03d}_ev{evidencia.pk}'
        archivo = base + _extension(evidencia)
        paquete.añadir(archivo, datos, comprimir=False)
        sello = None
        if evidencia.tsa_token:
            sello = base + '.tst'
            paquete.añadir(sello, bytes(evidencia.tsa_token), comprimir=False)
        prueba = base + '.prueba.json'
        paquete.json(prueba, {
            'evidencia': evidencia.pk,
            'archivo': archivo,
            'arbol': manifiesto['arbol'],
            'indice': indice,
            'total': len(hojas),
            'hoja_sha256': hoja['sha256'],
            'ruta': [h.hex() for h in merkle.prueba_inclusion(hojas, indice)],
            'raiz': manifiesto['raiz'],
        })
        fichas.append({
            'evidencia': evidencia.pk,
            'archivo': archivo,
            'descripcion': evidencia.descripcion or '',
            # Declarada por el dispositivo: fuera del manifiesto y sin sello
            'capturada_en': _iso(evidencia.capturada_en),
            'capturada_en_certificada': False,
            'avisos_hora': evidencia.avisos_hora(),
            'fecha_captura': _iso(evidencia.fecha_captura),
            'sha256': hoja['sha256'],
            'indice': indice,
            'prueba': prueba,
            'sello': sello,
            'tsa_timestamp': _iso(evidencia.tsa_timestamp),
        })

    # Evidencias antiguas sin hash válido: no están en el manifiesto ni tienen prueba
    avisos = []
    for evidencia in evidencias.values():
        datos = _leer_foto(evidencia)
        if datos is None:
            avisos.append(f'La evidencia {evidencia.pk} no está en el manifiesto y su foto no está disponible.')
            continue
        archivo = f'evidencias_sin_manifiesto/ev{evidencia.pk}{_extension(evidencia)}'
        paquete.añadir(archivo, datos, comprimir=False)
        avisos.append(f'{archivo} no está en el manifiesto sellado (sin hash válido al subirla): no tiene prueba de inclusión.')
    return fichas, avisos


def _añadir_manifiestos_anteriores(paquete, inspeccion):
    resumen = []
    for n, anterior in enumerate(inspeccion.manifiestos_anteriores or [], start=1):
        datos = {k: v for k, v in anterior.items() if k != 'tsa_token'}
        nombre = f'manifiestos_anteriores/{n:02d}_manifiesto.json'
        paquete.json(nombre, datos)
        sello = None
        if anterior.get('tsa_token'):
            sello = f'manifiestos_anteriores/{n:02d}_raiz.tst'
            paquete.añadir(sello, base64.b64decode(anterior['tsa_token']), comprimir=False)
        resumen.append({'raiz': anterior.get('raiz'), 'archivado': anterior.get('archivado'),
                        'manifiesto': nombre, 'sello': sello})
    return resumen


_BLOQUE_CERT = re.compile(rb'-----BEGIN CERTIFICATE-----.+?-----END CERTIFICATE-----', re.S)


def _añadir_certificados_tsa(paquete):
    """Certificados de TSA fijados en la configuración actual, por perfil ({perfil: archivo}).

    Del PEM solo se copian los bloques CERTIFICATE: nunca una clave que viniera en el mismo archivo.
    """
    por_perfil, por_contenido = {}, {}
    for perfil in (PERFIL_MANIFIESTO, PERFIL_EVIDENCIAS):
        ruta = configuracion(perfil)['cert_tsa']
        # Sin TSA propia, el manifiesto se sella con la de las fotos
        if not ruta and perfil == PERFIL_MANIFIESTO and not configuracion(perfil)['url']:
            ruta = configuracion(PERFIL_EVIDENCIAS)['cert_tsa']
        if not ruta:
            continue
        try:
            with open(ruta, 'rb') as f:
                pem = b'\n'.join(_BLOQUE_CERT.findall(f.read())) + b'\n'
        except OSError:
            logger.warning('No se pudo leer el certificado de la TSA del perfil %s', perfil)
            continue
        if pem.strip() and pem not in por_contenido:
            por_contenido[pem] = f'certificados_tsa/{perfil}.pem'
            paquete.añadir(por_contenido[pem], pem)
        if pem.strip():
            por_perfil[perfil] = por_contenido[pem]
    return por_perfil


def _expediente(inspeccion, fichas, anteriores, certificados, avisos):
    operario = inspeccion.operario
    return {
        'paquete': {'version': VERSION_PAQUETE, 'generado': timezone.now().isoformat(), 'generador': 'CheckIt'},
        'inspeccion': {
            'id': inspeccion.pk,
            'propiedad': inspeccion.propiedad.nombre,
            'direccion': inspeccion.propiedad.direccion,
            'operario': (operario.get_full_name() or operario.username) if operario else None,
            'fecha_creacion': _iso(inspeccion.fecha_creacion),
        },
        'informe': 'informe_firmado.pdf',
        'manifiesto': {
            'archivo': 'manifiesto/manifiesto.json',
            'sello': 'manifiesto/raiz.tst',
            'raiz': inspeccion.manifiesto_raiz,
            'hojas': len(inspeccion.manifiesto['hojas']),
            'tsa_url': url_segura(inspeccion.manifiesto_tsa_url),
            'tsa_timestamp': _iso(inspeccion.manifiesto_tsa_timestamp),
            'tsa_cualificada': inspeccion.manifiesto_tsa_cualificada,
            'tsa_verificado': inspeccion.manifiesto_tsa_verificado,
        },
        'evidencias': fichas,
        'manifiestos_anteriores': anteriores,
        'certificados_tsa': sorted(set(certificados.values())),
        'avisos': avisos,
    }


_SIN_CERT = '<certificado de la TSA>'


def _comando_sello(datos, sello, ca):
    return f'openssl ts -verify {datos} -token_in -in {sello} -CAfile {ca}'


def _script_sellos(inspeccion, fichas, certificados):
    """verificar_sellos.sh: un comando de openssl por sello del paquete."""
    ca_manifiesto = certificados.get(PERFIL_MANIFIESTO, _SIN_CERT)
    ca_fotos = certificados.get(PERFIL_EVIDENCIAS, _SIN_CERT)
    lineas = [
        '#!/bin/sh',
        '# Verifica con openssl la firma de cada sello de tiempo del paquete. Ver LEEME.txt.',
        '# Cada comando debe terminar en "Verification: OK".',
        'set -e',
        _comando_sello(f'-digest {inspeccion.manifiesto_raiz}', 'manifiesto/raiz.tst', ca_manifiesto),
    ]
    lineas += [_comando_sello(f'-data {f["archivo"]}', f['sello'], ca_fotos) for f in fichas if f['sello']]
    return '\n'.join(lineas) + '\n'


def _leeme(inspeccion, fichas, certificados):
    raiz = inspeccion.manifiesto_raiz
    tsa = url_segura(inspeccion.manifiesto_tsa_url) or 'TSA no registrada'
    if inspeccion.manifiesto_tsa_cualificada:
        tipo_sello = f'Sello de tiempo cualificado (eIDAS), emitido por {tsa}.'
    else:
        tipo_sello = (f'Sello de tiempo NO cualificado, emitido por {tsa}. Es una prueba técnica '
                      'admisible y verificable, pero no tiene la presunción legal de un sello cualificado.')
    ca = certificados.get(PERFIL_MANIFIESTO, _SIN_CERT)
    sin_cert = '' if certificados else (
        '   El paquete no incluye el certificado de la TSA: descárgalo de la web del prestador.\n'
    )
    return f"""PAQUETE PROBATORIO DE CHECKIT
Inspección {inspeccion.pk} · {inspeccion.propiedad.nombre} ({inspeccion.propiedad.direccion})
Generado el {timezone.now().strftime('%Y-%m-%d %H:%M %Z')}

{tipo_sello}

QUÉ CONTIENE
- informe_firmado.pdf: el acta de la inspección, firmada (PAdES con sello de tiempo).
- evidencias/: cada foto tal como se subió, su sello de tiempo (.tst) si lo tiene y
  su prueba de inclusión en el manifiesto (.prueba.json).
- manifiesto/manifiesto.json: la lista ordenada de los hashes SHA-256 de las fotos,
  congelada al completar la inspección, y su raíz Merkle (árbol RFC 6962).
- manifiesto/raiz.tst: el sello de tiempo RFC 3161 de esa raíz.
- manifiestos_anteriores/: los manifiestos sellados antes de reabrir la inspección, si los hubo.
- expediente.json: el resumen de todo lo anterior.
- SHA256SUMS: el hash de cada archivo del paquete.
- verificar.py: comprueba hashes, árbol, pruebas de inclusión y la huella de cada sello.
- verificar_sellos.sh: un comando de openssl por cada sello de tiempo del paquete.
- certificados_tsa/: los certificados de la TSA, si estaban configurados.

QUÉ DEMUESTRA
Cada foto tiene un hash SHA-256. Ese hash es una hoja del manifiesto, y su prueba de
inclusión lleva hasta la raíz {raiz}.
La TSA selló esa raíz en una fecha concreta, así que ninguna foto se pudo cambiar,
añadir ni quitar después de esa fecha sin que la verificación falle.

Las horas de cada foto (expediente.json) no valen lo mismo:
- tsa_timestamp: la da la TSA. Prueba que la foto ya existía a esa hora.
- fecha_captura: la hora en que el servidor de CheckIt recibió la foto.
- capturada_en: la hora que declara el dispositivo al hacer la foto. Nadie la
  certifica: el reloj del móvil puede estar mal o haberse cambiado. Solo es creíble
  si es anterior a las otras dos y no muy lejana.

CÓMO VERIFICARLO
El paso decisivo es el 2: la firma de la TSA sobre la raíz no la puede rehacer quien
haya manipulado el paquete. Los demás pasos atan cada foto a esa raíz.

1. Hashes, árbol y huellas de los sellos (Python 3.8 o posterior, sin dependencias):

   python3 -I verificar.py .

   verificar.py viene dentro del paquete: si desconfías de quien te lo entregó, compáralo
   con la copia de referencia de CheckIt o usa solo los pasos 2 y 3, que no dependen de él.
   La integridad de los archivos también se comprueba con: sha256sum -c SHA256SUMS

2. Sello de tiempo de la raíz del manifiesto (OpenSSL 1.1 o posterior):

   openssl ts -verify -digest {raiz} -token_in -in manifiesto/raiz.tst -CAfile {ca}

{sin_cert}   La raíz debe ser la que calcula verificar.py a partir de las fotos.
   Si la TSA usa un certificado intermedio, añade -untrusted <certificado de la TSA>.
   Los .tst son TimeStampToken (por eso -token_in), no TimeStampResp.
   verificar_sellos.sh repite esta comprobación para cada sello del paquete.

3. Firma del PDF: ábrelo con un validador PAdES (por ejemplo, Adobe Acrobat Reader o
   pyhanko sign validate informe_firmado.pdf). El PDF muestra la misma raíz.

DATOS PERSONALES
El paquete contiene el nombre del operario, la dirección de la vivienda, las
descripciones de las fotos y las fotos tal como se tomaron, con sus metadatos (EXIF,
que pueden incluir la ubicación). Trátalo según la normativa de protección de datos.
"""


def _comprobar_que_no_cambio(inspeccion):
    """La construcción tarda: si mientras tanto se reabrió o se regeneró el PDF, no se entrega."""
    actual = Inspeccion.objects.filter(pk=inspeccion.pk).values(
        'estado', 'manifiesto_raiz', 'pdf_ruta', 'pdf_manifiesto_raiz',
    ).first()
    usado = {
        'estado': inspeccion.estado, 'manifiesto_raiz': inspeccion.manifiesto_raiz,
        'pdf_ruta': inspeccion.pdf_ruta, 'pdf_manifiesto_raiz': inspeccion.pdf_manifiesto_raiz,
    }
    if actual != usado:
        raise PaqueteNoDisponible('La inspección ha cambiado mientras se preparaba el paquete; inténtalo de nuevo.')


def construir_paquete(inspeccion):
    """El ZIP del expediente, abierto y al principio. Lanza PaqueteNoDisponible o IntegridadRota."""
    with _abrir_pdf_listo(inspeccion) as pdf:
        pdf_datos = pdf.read()
    paquete = _Zip()
    try:
        fichas, avisos = _añadir_evidencias(paquete, inspeccion)
        paquete.json('manifiesto/manifiesto.json', inspeccion.manifiesto)
        paquete.añadir('manifiesto/raiz.tst', bytes(inspeccion.manifiesto_tsa_token), comprimir=False)
        anteriores = _añadir_manifiestos_anteriores(paquete, inspeccion)
        certificados = _añadir_certificados_tsa(paquete)
        paquete.añadir('informe_firmado.pdf', pdf_datos, comprimir=False)
        paquete.json('expediente.json', _expediente(inspeccion, fichas, anteriores, certificados, avisos))
        paquete.añadir('LEEME.txt', _leeme(inspeccion, fichas, certificados))
        paquete.añadir('verificar_sellos.sh', _script_sellos(inspeccion, fichas, certificados))
        paquete.añadir('verificar.py', _VERIFICADOR.read_bytes())
        _comprobar_que_no_cambio(inspeccion)
        return paquete.cerrar()
    except Exception:
        paquete.descartar()
        raise
