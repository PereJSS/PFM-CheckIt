"""
Sellado de tiempo RFC 3161.

Hay dos perfiles de TSA, cada uno con sus variables:
- ``evidencias`` (TSA_*): sella cada foto al subirla; por defecto FreeTSA, gratis.
- ``manifiesto`` (TSA_MANIFIESTO_*): sella la raíz Merkle de la inspección y la
  firma del PDF; pensado para una TSA cualificada (eIDAS). Sin TSA_MANIFIESTO_URL
  usa el perfil ``evidencias``.

Cada respuesta se verifica antes de aceptarla: nonce, hash sellado y firma del
token. Si hay certificado de la TSA configurado (``*_CERT``), la firma se valida
contra él (fijado) y además se exige que tenga el EKU timeStamping y que fuera
válido en la hora del sello; si no, se valida contra el certificado que trae el
propio token, lo que solo prueba la integridad del token y no quién lo emitió:
ese sello nunca se marca como verificado ni como cualificado.
"""
import logging
import math
import secrets
from datetime import timezone as dt_timezone
from urllib.parse import urlsplit, urlunsplit

import requests
import rfc3161ng
import rfc3161ng.api as rfc3161ng_api
from celery.exceptions import SoftTimeLimitExceeded
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID
from django.conf import settings
from pyasn1.codec.der import decoder as der_decoder, encoder as der_encoder
from pyasn1.type import univ

logger = logging.getLogger(__name__)

TSA_TIMEOUT_DEFAULT = 3.0
TSA_URL_DEFAULT = 'https://freetsa.org/tsr'

PERFIL_EVIDENCIAS = 'evidencias'
PERFIL_MANIFIESTO = 'manifiesto'

# PKIStatus que traen token: granted (0) y grantedWithMods (1)
_ESTADOS_CONCEDIDOS = (0, 1)


class TSAError(Exception):
    """La TSA no respondió con un sello válido."""


def url_segura(url):
    """URL sin credenciales, query ni fragmento: la única forma en que se loguea o se guarda."""
    try:
        partes = urlsplit(url or '')
        host = partes.hostname or ''
        if partes.port:
            host = f'{host}:{partes.port}'
        return urlunsplit((partes.scheme, host, partes.path, '', ''))
    except ValueError:
        return '<url no válida>'


def _prefijo(perfil):
    if perfil not in (PERFIL_EVIDENCIAS, PERFIL_MANIFIESTO):
        raise ValueError(f'Perfil TSA desconocido: {perfil!r}')
    if perfil == PERFIL_MANIFIESTO and getattr(settings, 'TSA_MANIFIESTO_URL', ''):
        return 'TSA_MANIFIESTO'
    return 'TSA'


def _tsa_timeout(prefijo='TSA'):
    # Timeout estricto (por conexión y por lectura): si la TSA tarda, seguimos
    # en modo degradado (tsa_token=None). Valores no válidos vuelven al default.
    nombre = f'{prefijo}_TIMEOUT'
    raw = getattr(settings, nombre, TSA_TIMEOUT_DEFAULT)
    try:
        timeout = float(raw)
    except (TypeError, ValueError):
        timeout = None
    if timeout is None or not math.isfinite(timeout) or timeout <= 0:
        logger.warning(
            "%s no válido (%r); se usa %ss", nombre, raw, TSA_TIMEOUT_DEFAULT
        )
        return TSA_TIMEOUT_DEFAULT
    return timeout


def configuracion(perfil=PERFIL_EVIDENCIAS):
    """Ajustes efectivos de la TSA de un perfil."""
    prefijo = _prefijo(perfil)

    def ajuste(campo, defecto=''):
        return getattr(settings, f'{prefijo}_{campo}', defecto) or defecto

    usuario = ajuste('USUARIO')
    cert_cliente = ajuste('CERT_CLIENTE')
    clave_cliente = ajuste('CLAVE_CLIENTE')
    if cert_cliente and clave_cliente:
        cert = (cert_cliente, clave_cliente)
    else:
        # mTLS con un único PEM que lleva certificado y clave, o sin mTLS
        cert = cert_cliente or None
    return {
        'url': ajuste('URL', TSA_URL_DEFAULT if prefijo == 'TSA' else ''),
        'timeout': _tsa_timeout(prefijo),
        'auth': (usuario, ajuste('PASSWORD')) if usuario else None,
        'cert': cert,
        'cert_tsa': ajuste('CERT'),
        'politica': ajuste('POLITICA') or None,
        'cualificada': bool(getattr(settings, f'{prefijo}_CUALIFICADA', False)),
    }


def enviar_peticion(payload, perfil=PERFIL_EVIDENCIAS):
    """POST de una TimeStampReq DER a la TSA del perfil. Devuelve el cuerpo (TimeStampResp DER).

    Lanza TSAError si la TSA responde con un estado HTTP no exitoso y deja pasar
    las excepciones de red de requests (Timeout incluido).
    """
    conf = configuracion(perfil)
    # Por http un intermediario podría fabricar el token: solo se admite con el certificado fijado
    if urlsplit(conf['url']).scheme != 'https' and not conf['cert_tsa']:
        raise TSAError(f"TSA sin https ni certificado fijado ({url_segura(conf['url'])}); no se usa")
    response = requests.post(
        conf['url'],
        data=payload,
        headers={'Content-Type': 'application/timestamp-query'},
        auth=conf['auth'],
        cert=conf['cert'],
        timeout=conf['timeout'],
    )
    if response.status_code != 200:
        # Sin el cuerpo: algunas TSA devuelven un eco de la petición con sus cabeceras
        raise TSAError(
            f"TSA estado no exitoso url={url_segura(conf['url'])} status={response.status_code}"
        )
    return response.content


def _leer_cert_tsa(ruta):
    if not ruta:
        return None
    with open(ruta, 'rb') as f:
        return f.read()


def validar_cert_tsa(cert_bytes):
    """Carga el certificado de la TSA (PEM o DER) y exige EKU timeStamping. Lanza ValueError."""
    if b'-----BEGIN CERTIFICATE-----' in cert_bytes:
        cert = x509.load_pem_x509_certificate(cert_bytes)
    else:
        cert = x509.load_der_x509_certificate(cert_bytes)
    try:
        eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    except x509.ExtensionNotFound:
        raise ValueError('el certificado de la TSA no tiene EKU timeStamping')
    if ExtendedKeyUsageOID.TIME_STAMPING not in eku:
        raise ValueError('el certificado de la TSA no tiene EKU timeStamping')
    return cert


def _validar_cert_fijado(cert_bytes, gen_time):
    """El certificado fijado debe ser de sellado de tiempo y válido en la hora del sello."""
    cert = validar_cert_tsa(cert_bytes)
    if gen_time is None:
        raise ValueError('el token no trae genTime')
    if gen_time.tzinfo is None:
        gen_time = gen_time.replace(tzinfo=dt_timezone.utc)
    if not cert.not_valid_before_utc <= gen_time <= cert.not_valid_after_utc:
        raise ValueError('el certificado de la TSA no era válido en la hora del sello')


_HASHES_ADMITIDOS = {'sha256', 'sha384', 'sha512'}


def _comprobar_sello(tst, certificado, digest, nonce, data=None):
    """Lo mismo que rfc3161ng.check_timestamp, pero verificando la firma según el tipo de
    clave de la TSA: rfc3161ng solo admite RSA y FreeTSA firma con curva elíptica desde
    octubre de 2026. Lanza ValueError (o InvalidSignature) si algo no cuadra."""
    if digest is None:
        digest = hashlib.sha256(data or b'').digest()
    signed_data = tst.content
    cert = rfc3161ng_api.load_certificate(signed_data, certificado)
    if int(tst.tst_info['nonce']) != int(nonce):
        raise ValueError('nonce distinto o ausente')
    imprint = tst.tst_info.message_imprint
    if imprint.hash_algorithm[0] != rfc3161ng_api.get_hash_oid('sha256') or bytes(imprint.hashed_message) != digest:
        raise ValueError('la huella sellada no es la del documento')
    if len(signed_data['signerInfos']) != 1:
        # Otro verificador (openssl, el perito) podría mirar un firmante distinto
        raise ValueError('el token debe tener exactamente una firma')
    signer_info = signed_data['signerInfos'][0]
    if tst.content['contentInfo']['contentType'] != rfc3161ng.id_ct_TSTInfo:
        raise ValueError('el contenido firmado no es un TSTInfo')

    contenido = bytes(der_decoder.decode(
        bytes(tst.content['contentInfo']['content']), asn1Spec=univ.OctetString())[0])
    algoritmo = signer_info['digestAlgorithm']['algorithm']
    clase_hash = rfc3161ng_api.get_hash_class_from_oid(algoritmo)
    nombre_hash = rfc3161ng_api.get_hash_from_oid(algoritmo)
    if nombre_hash not in _HASHES_ADMITIDOS:
        raise ValueError(f'hash de la firma no admitido: {nombre_hash}')
    atributos = signer_info['authenticatedAttributes']
    if not len(atributos):
        # RFC 5652: sin atributos firmados la firma cubre el contenido. Las TSA siempre los
        # ponen y validarlos así abre más superficie: se rechaza
        raise ValueError('token sin atributos firmados')
    digests = [a for a in atributos if a[0] == rfc3161ng_api.id_attribute_messageDigest]
    if len(digests) != 1:
        raise ValueError('el token debe llevar exactamente un messageDigest firmado')
    firmado = bytes(der_decoder.decode(bytes(digests[0][1][0]), asn1Spec=univ.OctetString())[0])
    if firmado != clase_hash(contenido).digest():
        raise ValueError('el messageDigest firmado no corresponde al TSTInfo')
    conjunto = univ.SetOf()
    for i, atributo in enumerate(atributos):
        conjunto.setComponentByPosition(i, atributo)
    datos_firmados = der_encoder.encode(conjunto)

    firma = bytes(signer_info['encryptedDigest'])
    algoritmo_hash = getattr(hashes, nombre_hash.upper())()
    clave = cert.public_key()
    if isinstance(clave, rsa.RSAPublicKey):
        clave.verify(firma, datos_firmados, padding.PKCS1v15(), algoritmo_hash)
    elif isinstance(clave, ec.EllipticCurvePublicKey):
        clave.verify(firma, datos_firmados, ec.ECDSA(algoritmo_hash))
    else:
        raise ValueError(f'tipo de clave de la TSA no admitido: {type(clave).__name__}')


def _digest_y_datos(evidencia_hash):
    # Si llega un hash hexadecimal válido (64 chars), lo usamos como digest real.
    # Si no, hacemos fallback a data bytes para no romper flujo de negocio.
    if not evidencia_hash:
        return None, None
    hash_txt = str(evidencia_hash).strip()
    if len(hash_txt) == 64:
        try:
            return bytes.fromhex(hash_txt), None
        except ValueError:
            pass
    return None, hash_txt.encode('utf-8')


def solicitar_sello_tiempo(evidencia_hash, perfil=PERFIL_EVIDENCIAS):
    """Pide y verifica un sello RFC 3161 del hash. Devuelve None en modo degradado.

    El resultado incluye ``token`` (DER), ``timestamp``, ``url`` de la TSA,
    ``verificado`` (si la firma se validó contra el certificado fijado de la
    TSA) y ``cualificada`` (configuración del perfil, y solo si está verificado).
    """
    conf = configuracion(perfil)
    try:
        # Antes de pedir el sello: si no se puede leer, ningún sello pasaría la verificación
        cert_tsa = _leer_cert_tsa(conf['cert_tsa'])
    except OSError as e:
        logger.critical(
            "TSA mal configurada: no se puede leer el certificado fijado %s (%s); no se sella nada",
            conf['cert_tsa'], type(e).__name__,
        )
        return None
    try:
        digest, data = _digest_y_datos(evidencia_hash)
        nonce = secrets.randbits(63)
        req = rfc3161ng.make_timestamp_request(
            data=data,
            digest=digest,
            hashname='sha256',
            include_tsa_certificate=True,
            nonce=nonce,
            tsa_policy_id=conf['politica'],
        )
        contenido = enviar_peticion(der_encoder.encode(req), perfil)

        tsr = rfc3161ng.decode_timestamp_response(contenido)
        estado = int(tsr['status']['status'])
        if estado not in _ESTADOS_CONCEDIDOS or not tsr.time_stamp_token.isValue:
            logger.warning("TSA rechazó la petición url=%s status=%s", conf['url'], estado)
            return None

        tst = tsr.time_stamp_token
        tsa_token = der_encoder.encode(tst)
        timestamp = None
        try:
            timestamp = rfc3161ng.get_timestamp(tsa_token)
        except Exception as parse_err:
            logger.warning("TSA token sin timestamp parseable: %s", parse_err)

        try:
            # Sin certificado fijado (b''), valida contra el que trae el token
            _comprobar_sello(tst, cert_tsa or b'', digest, nonce, data)
            if cert_tsa:
                _validar_cert_fijado(cert_tsa, timestamp)
        except Exception as err:
            logger.error("Sello TSA rechazado en la verificación url=%s: %s", url_segura(conf['url']), err)
            return None

        verificado = bool(cert_tsa)
        return {
            "token": tsa_token,
            "timestamp": timestamp,
            "url": url_segura(conf['url']),
            "cualificada": conf['cualificada'] and verificado,
            "verificado": verificado,
        }
    except SoftTimeLimitExceeded:
        # El límite de Celery lo gestiona quien llama (corta el lote)
        raise
    except requests.exceptions.Timeout:
        logger.warning(
            "TSA sin respuesta en %ss url=%s; sin sello (modo degradado)",
            conf['timeout'],
            url_segura(conf['url']),
        )
        return None
    except TSAError as e:
        logger.warning("%s", e)
        return None
    except Exception as e:
        logger.error(
            "Error al contactar con la TSA url=%s: %s", url_segura(conf['url']), type(e).__name__,
            exc_info=not isinstance(e, requests.exceptions.RequestException),
        )
        return None
