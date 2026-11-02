"""
Verificador público: la prueba de que una foto está en un manifiesto sellado.

Hacen falta la foto y la clave de verificación de la inspección, que llega con el acta
(PDF, webhook o paquete). La clave es aleatoria y no sale de las fotos: sin ella no se
consulta nada, así que tener una foto no enseña ni que está en CheckIt.

Solo devuelve datos criptográficos y no da ningún veredicto: lo comprueba quien
consulta. Lo guardado se trata como no fiable, porque un endpoint público no debe dar
un 500 por un dato antiguo o corrupto. Lo que no se puede leer o no cuadra se registra
y se cuenta en `anomalias`: sin eso, un manifiesto corrupto se confundiría con una foto
que aún no ha entrado en ninguno.
"""
import base64
import binascii
import logging

from ..models import Inspeccion
from . import merkle
from .manifiesto import _HASH_HEX

logger = logging.getLogger(__name__)

# Trabajo acotado por petición: cada manifiesto cuesta recalcular su árbol
MAX_MANIFIESTOS = 20
MAX_HOJAS_POR_PETICION = 20000


class _Consulta:
    def __init__(self, hash_sha256, inspeccion_id):
        self.hash_sha256 = hash_sha256
        self.inspeccion_id = inspeccion_id
        self.hojas_restantes = MAX_HOJAS_POR_PETICION
        self.truncado = False
        self.anomalias = 0

    def anomalia(self, mensaje, *args):
        logger.error(f'Inspección %s: {mensaje}', self.inspeccion_id, *args)
        self.anomalias += 1


def buscar(clave, hash_sha256):
    """Pruebas de la foto en los manifiestos de la inspección de esa clave (ambas en hex
    minúsculas), o None si la clave no existe o la foto no es de esa inspección."""
    # Manifiesto, raíz y sellos salen de una misma fila: no pueden ser de momentos distintos.
    # Con una clave errónea no se mira la foto: ni la respuesta ni el tiempo dependen de ella.
    inspeccion = Inspeccion.objects.filter(clave_verificacion=clave).first()
    if inspeccion is None:
        return None
    # Las mayúsculas son de datos anteriores a la validación de la subida
    evidencia = inspeccion.evidencias.filter(hash_sha256__in=(hash_sha256, hash_sha256.upper())).order_by('pk').first()

    consulta = _Consulta(hash_sha256, inspeccion.pk)
    manifiestos = _manifiestos(inspeccion, consulta, foto_registrada=evidencia is not None)
    # Si algo no se pudo leer, la foto puede estar ahí: un 404 lo negaría
    if not (evidencia or manifiestos or consulta.anomalias or consulta.truncado):
        return None
    return {
        'hash': hash_sha256,
        'manifiestos': manifiestos,
        'sello_individual': _sello_individual(evidencia) if evidencia else None,
        'anomalias': consulta.anomalias,
        'truncado': consulta.truncado,
    }


def _manifiestos(inspeccion, consulta, foto_registrada):
    """Del más antiguo al vigente: el primer sello es el que prueba la anterioridad."""
    vigente = None
    if inspeccion.manifiesto_raiz:
        # Primero, para que el archivo no le agote el presupuesto. Con el manifiesto congelado
        # no entran fotos: si la foto es de la inspección y no está, algo cambió.
        vigente = _prueba(consulta, inspeccion.manifiesto, inspeccion.manifiesto_raiz, debe_estar=foto_registrada)

    archivados = []
    anteriores = inspeccion.manifiestos_anteriores
    if not isinstance(anteriores, list):
        consulta.anomalia('archivo de manifiestos mal formado')
        anteriores = []
    if len(anteriores) > MAX_MANIFIESTOS:
        consulta.truncado = True
    for entrada in anteriores[:MAX_MANIFIESTOS]:
        if not isinstance(entrada, dict):
            consulta.anomalia('entrada mal formada en el archivo')
            continue
        # Las fotos añadidas tras reabrir no están en los archivados
        prueba = _prueba(consulta, entrada.get('manifiesto'), entrada.get('raiz'), debe_estar=False)
        if prueba:
            archivados.append({**prueba, 'vigente': False, 'sello': _sello_archivado(entrada, consulta)})

    if vigente:
        archivados.append({**vigente, 'vigente': True, 'sello': _sello_vigente(inspeccion)})
    return archivados


def _prueba(consulta, manifiesto, raiz, debe_estar):
    """Ruta de inclusión de la foto, o None si no está en ese manifiesto o no se puede leer."""
    if not (isinstance(raiz, str) and _HASH_HEX.fullmatch(raiz)):
        consulta.anomalia('manifiesto con una raíz no válida')
        return None
    try:
        declaradas = manifiesto['hojas']
        if not isinstance(declaradas, list):
            raise TypeError('hojas no es una lista')
        if len(declaradas) > consulta.hojas_restantes:
            logger.warning('Inspección %s: verificación recortada, sin presupuesto de hojas', consulta.inspeccion_id)
            consulta.truncado = True
            return None
        # Antes de recorrerlas: un manifiesto corrupto también cuesta
        consulta.hojas_restantes -= len(declaradas)
        hojas, indice = [], None
        for n, hoja in enumerate(declaradas):
            sha256 = hoja['sha256']
            if not _HASH_HEX.fullmatch(sha256):
                raise ValueError(f'hoja {n} no es un SHA-256')
            hojas.append(bytes.fromhex(sha256))
            if indice is None and sha256 == consulta.hash_sha256:
                indice = n
        version, arbol = manifiesto.get('version'), manifiesto.get('arbol')
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        consulta.anomalia('manifiesto mal formado: %r', exc)
        return None
    if indice is None:
        if debe_estar:
            consulta.anomalia('foto registrada fuera de su manifiesto vigente')
        return None

    prueba = merkle.prueba_inclusion(hojas, indice)
    if not merkle.verificar_inclusion(hojas[indice], indice, len(hojas), prueba, bytes.fromhex(raiz)):
        # Se devuelve igual: ocultarlo taparía una manipulación
        consulta.anomalia('la prueba no lleva a la raíz %s', raiz)
    return {
        'version': version if isinstance(version, int) else None,
        'arbol': _texto(arbol),
        'raiz': raiz, 'indice': indice, 'total': len(hojas), 'prueba': [nodo.hex() for nodo in prueba],
    }


def _sello_individual(evidencia):
    if not evidencia.tsa_token:
        return None
    return {'token': _b64(evidencia.tsa_token), 'timestamp': _iso(evidencia.tsa_timestamp)}


def _sello_vigente(inspeccion):
    if not inspeccion.manifiesto_tsa_token:
        return None
    return {
        'token': _b64(inspeccion.manifiesto_tsa_token),
        'timestamp': _iso(inspeccion.manifiesto_tsa_timestamp),
        'url': inspeccion.manifiesto_tsa_url,
        'cualificada': inspeccion.manifiesto_tsa_cualificada,
        'verificado': inspeccion.manifiesto_tsa_verificado,
    }


def _sello_archivado(entrada, consulta):
    # Solo se archivan manifiestos sellados: un archivado sin token válido es una anomalía
    token = entrada.get('tsa_token')
    try:
        base64.b64decode(token, validate=True)
    except (binascii.Error, TypeError, ValueError):
        consulta.anomalia('sello archivado ilegible')
        return None
    return {
        'token': token,
        'timestamp': _texto(entrada.get('tsa_timestamp')),
        'url': _texto(entrada.get('tsa_url')) or '',
        'cualificada': entrada.get('tsa_cualificada') is True,
        'verificado': entrada.get('tsa_verificado') is True,
    }


def _texto(valor):
    return valor if isinstance(valor, str) else None


def _b64(token):
    return base64.b64encode(bytes(token)).decode('ascii')


def _iso(fecha):
    return fecha.isoformat() if fecha else None
