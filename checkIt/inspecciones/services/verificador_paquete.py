#!/usr/bin/env python3
"""
Verificador del paquete probatorio de CheckIt.

Uso: python3 -I verificar.py <carpeta del paquete descomprimido>

Solo usa la biblioteca estándar de Python 3.8+ y no se conecta a CheckIt ni a
ningún otro sitio. Comprueba:

1. Que SHA256SUMS lista todos los archivos del paquete y que cada uno coincide.
2. Que la raíz del manifiesto se recalcula a partir de sus hojas (árbol RFC 6962)
   y que es la huella que lleva el sello de tiempo manifiesto/raiz.tst.
3. Que cada foto tiene el hash de su hoja, que su ruta de inclusión lleva a esa
   raíz y que su sello individual (si lo tiene) es de ese hash.

En la versión 2 del manifiesto cada hoja sella también los metadatos de la foto
(estancia, descripción, punto del checklist, hora del dispositivo) y la última, el
checklist: cambiar cualquiera de ellos en manifiesto.json cambia la raíz.

No comprueba la firma de los sellos ni la del PDF: eso se hace con openssl y con
un validador PAdES (ver LEEME.txt). Sin esa comprobación, quien rehiciera todo el
paquete con otros sellos también pasaría este script.

En CheckIt este archivo vive en inspecciones/services/verificador_paquete.py; el
árbol es una copia de services/merkle.py para que el paquete no dependa de nada.
"""
import hashlib
import json
import os
import stat
import sys

# Archivos sin los que el paquete no prueba nada
ESENCIALES = ('expediente.json', 'informe_firmado.pdf', 'manifiesto/manifiesto.json', 'manifiesto/raiz.tst')
# OID de SHA-256 (2.16.840.1.101.3.4.2.1) y de id-ct-TSTInfo (1.2.840.113549.1.9.16.1.4), en DER
_OID_SHA256 = bytes.fromhex('608648016503040201')
_OID_TSTINFO = bytes.fromhex('2a864886f70d0109100104')


def _sha256(datos):
    return hashlib.sha256(datos).digest()


def _hash_hoja(hoja):
    return _sha256(b'\x00' + hoja)


def _hash_nodo(izquierda, derecha):
    return _sha256(b'\x01' + izquierda + derecha)


def raiz(hojas):
    n = len(hojas)
    if n == 0:
        return _sha256(b'')
    if n == 1:
        return _hash_hoja(hojas[0])
    k = 1
    while k * 2 < n:
        k *= 2
    return _hash_nodo(raiz(hojas[:k]), raiz(hojas[k:]))


def verificar_inclusion(hoja, indice, total, prueba, raiz_esperada):
    # RFC 9162 §2.1.3.2
    if not 0 <= indice < total:
        return False
    fn, sn = indice, total - 1
    r = _hash_hoja(hoja)
    for hermano in prueba:
        if sn == 0:
            return False
        if fn % 2 == 1 or fn == sn:
            r = _hash_nodo(hermano, r)
            while fn % 2 == 0 and fn != 0:
                fn >>= 1
                sn >>= 1
        else:
            r = _hash_nodo(r, hermano)
        fn >>= 1
        sn >>= 1
    return sn == 0 and r == raiz_esperada


# Formato de las hojas (ADR-005). Esta es la referencia: el backend de CheckIt importa
# estas funciones, así que el servidor y verificar.py no pueden dar bytes distintos.
# v1: el dato de cada hoja es el SHA-256 de la foto.
# v2: foto = 0x01 ‖ sha256(foto) ‖ sha256(metadatos con sal); checklist (última hoja,
# opcional) = 0x02 ‖ sha256({checklist, sal}). El primer byte separa los tipos.
VERSIONES = (1, 2)
_HEX = frozenset('0123456789abcdef')
# Topes: un manifiesto guardado o un paquete hostil no deben agotar la pila ni la CPU
MAX_PROFUNDIDAD = 32
MAX_BYTES_HOJA = 1024 * 1024


def _es_hex(valor, largo):
    return isinstance(valor, str) and len(valor) == largo and _HEX.issuperset(valor)


def _sin_decimales(valor, profundidad=0):
    if profundidad > MAX_PROFUNDIDAD:
        raise ValueError('JSON demasiado anidado')
    if isinstance(valor, float):
        raise ValueError('el formato canónico no admite decimales')
    if isinstance(valor, dict):
        for clave, contenido in valor.items():
            if not isinstance(clave, str):
                raise ValueError('las claves tienen que ser texto')
            _sin_decimales(contenido, profundidad + 1)
    elif isinstance(valor, (list, tuple)):
        for contenido in valor:
            _sin_decimales(contenido, profundidad + 1)


def json_canonico(valor):
    """Bytes canónicos de un JSON: claves ordenadas por punto de código, sin espacios,
    UTF-8, sin decimales y con un tope de anidamiento. Es un formato propio: coincide con
    RFC 8785 mientras las claves no tengan caracteres fuera del plano básico."""
    _sin_decimales(valor)
    datos = json.dumps(
        valor, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False,
    ).encode('utf-8')
    if len(datos) > MAX_BYTES_HOJA:
        raise ValueError('hoja demasiado grande')
    return datos


def version_de(manifiesto):
    """Versión del manifiesto; sin el campo es v1. Una versión que no se conoce es un error:
    no se sabe qué sella."""
    if not isinstance(manifiesto, dict):
        raise ValueError('el manifiesto no es un objeto')
    version = manifiesto.get('version', 1)
    # type() y no isinstance: True no es la versión 1
    if type(version) is not int or version not in VERSIONES:
        raise ValueError(f'versión de manifiesto no admitida: {version!r}')
    return version


def _hash_foto(hoja):
    sha256 = hoja.get('sha256')
    if not _es_hex(sha256, 64):
        raise ValueError('hoja sin un SHA-256 en hexadecimal minúsculo')
    return bytes.fromhex(sha256)


def datos_hoja(hoja, version):
    """Bytes que entran en el árbol por esta hoja (antes del prefijo 0x00 de RFC 6962)."""
    if not isinstance(hoja, dict):
        raise ValueError('hoja mal formada')
    if version == 1:
        return _hash_foto(hoja)
    if version != 2:
        raise ValueError(f'versión de manifiesto no admitida: {version!r}')
    tipo = hoja.get('tipo')
    if tipo == 'foto':
        metadatos = hoja.get('metadatos')
        if not isinstance(metadatos, dict) or not _es_hex(metadatos.get('sal'), 32):
            raise ValueError('hoja de foto sin metadatos o sin una sal válida')
        # La evidencia con la que se busca la foto tiene que ser la sellada
        evidencia = hoja.get('evidencia')
        if type(evidencia) is not int or type(metadatos.get('evidencia')) is not int \
                or metadatos['evidencia'] != evidencia:
            raise ValueError('la evidencia de la hoja no es la de sus metadatos')
        return b'\x01' + _hash_foto(hoja) + _sha256(json_canonico(metadatos))
    if tipo == 'checklist':
        if not isinstance(hoja.get('checklist'), dict) or not _es_hex(hoja.get('sal'), 32):
            raise ValueError('hoja del checklist sin checklist o sin una sal válida')
        return b'\x02' + _sha256(json_canonico({'checklist': hoja['checklist'], 'sal': hoja['sal']}))
    raise ValueError(f'tipo de hoja desconocido: {tipo!r}')


def metadatos_sha256(hoja):
    """Hash (hex) de los metadatos de una hoja de foto v2; None en las demás. Con la sal
    dentro no deja adivinar los metadatos, así que se puede enseñar sin ellos."""
    if not isinstance(hoja, dict) or hoja.get('tipo') != 'foto':
        return None
    datos_hoja(hoja, 2)
    return hashlib.sha256(json_canonico(hoja['metadatos'])).hexdigest()


def hojas_del_manifiesto(manifiesto):
    """Datos de todas las hojas, en orden. ValueError si alguna no se puede interpretar."""
    version = version_de(manifiesto)
    hojas = manifiesto.get('hojas')
    if not isinstance(hojas, list):
        raise ValueError('las hojas del manifiesto no son una lista')
    datos = [datos_hoja(hoja, version) for hoja in hojas]
    if version == 2:
        checklists = [i for i, hoja in enumerate(hojas) if hoja['tipo'] == 'checklist']
        if checklists and checklists != [len(hojas) - 1]:
            raise ValueError('la hoja del checklist tiene que ser una sola y la última')
        evidencias = [hoja['evidencia'] for hoja in hojas if hoja['tipo'] == 'foto']
        if len(set(evidencias)) != len(evidencias):
            raise ValueError('dos hojas de la misma evidencia')
    return datos


def _der(datos, etiqueta_esperada):
    """Contenido y resto de un elemento DER con la etiqueta esperada."""
    if len(datos) < 2 or datos[0] != etiqueta_esperada:
        raise ValueError('sello de tiempo mal formado')
    largo, pos = datos[1], 2
    if largo & 0x80:
        n = largo & 0x7F
        if not 1 <= n <= 4:
            raise ValueError('sello de tiempo mal formado')
        largo, pos = int.from_bytes(datos[2:2 + n], 'big'), 2 + n
    if pos + largo > len(datos):
        raise ValueError('sello de tiempo truncado')
    return datos[pos:pos + largo], datos[pos + largo:]


def _saltar(datos):
    """Resto tras el primer elemento DER, sea cual sea su etiqueta."""
    return _der(datos, datos[0] if datos else -1)[1]


def huella_sellada(token):
    """Hash SHA-256 (hex) que sella un TimeStampToken DER (RFC 3161, messageImprint).

    No valida la firma: eso lo hace openssl ts -verify.
    """
    content_info, _ = _der(token, 0x30)
    resto = _saltar(content_info)                       # contentType (id-signedData)
    signed_data, _ = _der(_der(resto, 0xA0)[0], 0x30)
    resto = _saltar(_saltar(signed_data))               # version, digestAlgorithms
    encap, _ = _der(resto, 0x30)
    tipo, resto = _der(encap, 0x06)
    if tipo != _OID_TSTINFO:
        raise ValueError('el sello no es un TimeStampToken')
    octetos, _ = _der(_der(resto, 0xA0)[0], 0x04)
    tst_info, _ = _der(octetos, 0x30)
    resto = _saltar(_saltar(tst_info))                  # version, policy
    imprint, _ = _der(resto, 0x30)
    algoritmo, hashed = _der(imprint, 0x30)
    oid, _ = _der(algoritmo, 0x06)
    if oid != _OID_SHA256:
        raise ValueError('el sello no es de un hash SHA-256')
    return _der(hashed, 0x04)[0].hex()


def _ruta_segura(nombre):
    """Solo rutas relativas dentro del paquete."""
    partes = nombre.replace('\\', '/').split('/')
    if nombre.startswith('/') or '..' in partes or ':' in nombre:
        raise ValueError(f'ruta no permitida: {nombre}')
    return os.path.join(*partes)


def _sin_duplicadas(pares):
    objeto = {}
    for clave, valor in pares:
        if clave in objeto:
            raise ValueError(f'clave duplicada en el JSON: {clave!r}')
        objeto[clave] = valor
    return objeto


def _leer_json(nombre):
    # Con claves repetidas, otro lector podría quedarse con otra versión del dato
    return json.loads(_leer(nombre), object_pairs_hook=_sin_duplicadas)


def _leer(nombre):
    ruta = _ruta_segura(nombre)
    # Ni enlaces simbólicos ni dispositivos: un paquete hostil podría apuntar a /dev/zero
    if not stat.S_ISREG(os.lstat(ruta).st_mode):
        raise ValueError(f'{nombre} no es un archivo normal')
    with open(ruta, 'rb') as f:
        return f.read()


def _archivos_del_paquete():
    archivos = set()
    for carpeta, _, nombres in os.walk('.'):
        for nombre in nombres:
            archivos.add(os.path.relpath(os.path.join(carpeta, nombre), '.').replace(os.sep, '/'))
    return archivos


class Informe:
    def __init__(self):
        self.fallos = 0

    def ok(self, texto):
        print(f'OK     {texto}')

    def fallo(self, texto):
        self.fallos += 1
        print(f'FALLO  {texto}')


def comprobar_sumas(informe):
    print('\n1. Integridad de los archivos (SHA256SUMS)')
    lineas = [linea for linea in _leer('SHA256SUMS').decode('utf-8').splitlines() if linea.strip()]
    listados = {linea.split('  ', 1)[1] for linea in lineas}
    for nombre in ESENCIALES:
        if nombre not in listados:
            informe.fallo(f'{nombre}: falta en SHA256SUMS')
    for nombre in sorted(_archivos_del_paquete() - listados - {'SHA256SUMS'}):
        informe.fallo(f'{nombre}: está en el paquete pero no en SHA256SUMS')
    for linea in lineas:
        suma, nombre = linea.split('  ', 1)
        try:
            real = hashlib.sha256(_leer(nombre)).hexdigest()
        except (OSError, ValueError) as e:
            informe.fallo(f'{nombre}: {e}')
            continue
        if real == suma:
            informe.ok(nombre)
        else:
            informe.fallo(f'{nombre}: el hash no coincide con SHA256SUMS')


def comprobar_manifiesto(informe):
    print('\n2. Raíz del manifiesto Merkle')
    manifiesto = _leer_json('manifiesto/manifiesto.json')
    version = version_de(manifiesto)
    hojas = hojas_del_manifiesto(manifiesto)
    calculada = raiz(hojas).hex()
    cuadra = calculada == manifiesto['raiz']
    if cuadra:
        informe.ok(f'raíz {calculada} ({len(hojas)} hojas, versión {version})')
    else:
        informe.fallo(f'la raíz recalculada {calculada} no es la del manifiesto {manifiesto["raiz"]}')
    sellada = huella_sellada(_leer('manifiesto/raiz.tst'))
    if sellada == calculada:
        informe.ok('manifiesto/raiz.tst sella esa raíz')
    else:
        cuadra = False
        informe.fallo(f'manifiesto/raiz.tst sella {sellada}, no la raíz recalculada')
    # El checklist no tiene foto: queda probado si su hoja entra en la raíz sellada
    for indice, hoja in enumerate(manifiesto['hojas']):
        if version == 2 and hoja['tipo'] == 'checklist' and cuadra:
            informe.ok(f'checklist sellado (hoja {indice})')
    return manifiesto


def comprobar_evidencias(informe, manifiesto):
    print('\n3. Cada foto, su hoja y su ruta hasta la raíz')
    expediente = _leer_json('expediente.json')
    raiz_sellada = bytes.fromhex(manifiesto['raiz'])
    version = version_de(manifiesto)
    hojas = hojas_del_manifiesto(manifiesto)
    # Solo las hojas de foto necesitan una foto
    vistas = {i for i, h in enumerate(manifiesto['hojas']) if version == 2 and h['tipo'] != 'foto'}
    for ficha in expediente['evidencias']:
        archivo = ficha['archivo']
        try:
            datos = _leer(archivo)
            prueba = json.loads(_leer(ficha['prueba']))
        except (OSError, ValueError) as e:
            informe.fallo(f'{archivo}: {e}')
            continue
        indice = prueba['indice']
        if type(indice) is not int or not 0 <= indice < len(hojas):
            informe.fallo(f'{archivo}: su prueba apunta a una hoja que no existe')
            continue
        hoja = manifiesto['hojas'][indice]
        if version == 2 and (hoja['tipo'] != 'foto' or hoja['evidencia'] != ficha.get('evidencia')):
            informe.fallo(f'{archivo}: la hoja {indice} no es la de esta foto')
            continue
        real = hashlib.sha256(datos).hexdigest()
        if real != hoja.get('sha256'):
            informe.fallo(f'{archivo}: su hash no es el de la hoja {indice} del manifiesto')
            continue
        if version == 2:
            # Lo que enseña el expediente tiene que ser lo sellado, no otra cosa
            distintos = [c for c in CAMPOS_SELLADOS if ficha.get(c) != hoja['metadatos'].get(c)]
            if distintos:
                informe.fallo(f'{archivo}: {", ".join(distintos)} de expediente.json no es lo sellado')
                continue
        ruta = [bytes.fromhex(h) for h in prueba['ruta']]
        if not verificar_inclusion(hojas[indice], indice, len(hojas), ruta, raiz_sellada):
            informe.fallo(f'{archivo}: la ruta de inclusión no lleva a la raíz sellada')
            continue
        if ficha.get('sello') and huella_sellada(_leer(ficha['sello'])) != real:
            informe.fallo(f'{ficha["sello"]}: no sella el hash de {archivo}')
            continue
        vistas.add(indice)
        informe.ok(f'{archivo} (hoja {indice})')
    faltan = set(range(len(hojas))) - vistas
    if faltan:
        informe.fallo(f'hojas del manifiesto sin foto verificada: {sorted(faltan)}')
    if version == 2:
        comprobar_checklist(informe, manifiesto, expediente)


# Metadatos de cada foto que expediente.json enseña y que en v2 van sellados
CAMPOS_SELLADOS = ('estancia', 'descripcion', 'punto', 'capturada_en')
# Campos del checklist de expediente.json que explican, no que se sellan
_NOTAS_CHECKLIST = ('sellado', 'nota')


def comprobar_checklist(informe, manifiesto, expediente):
    """v2: el checklist de expediente.json es el de la hoja sellada, y solo hay uno si la hay."""
    hoja = next((h for h in manifiesto['hojas'] if h['tipo'] == 'checklist'), None)
    mostrado = expediente.get('checklist')
    if hoja is None and mostrado is None:
        return
    if hoja is None:
        informe.fallo('expediente.json enseña un checklist que no está en el manifiesto sellado')
    elif not isinstance(mostrado, dict):
        informe.fallo('el manifiesto sella un checklist que expediente.json no enseña')
    else:
        sin_notas = {k: v for k, v in mostrado.items() if k not in _NOTAS_CHECKLIST}
        if json_canonico(sin_notas) == json_canonico(hoja['checklist']):
            informe.ok('el checklist de expediente.json es el sellado')
        else:
            informe.fallo('el checklist de expediente.json no es el sellado')


def main():
    if len(sys.argv) != 2:
        print('Uso: python3 -I verificar.py <carpeta del paquete descomprimido>')
        return 2
    os.chdir(sys.argv[1])
    informe = Informe()
    try:
        comprobar_sumas(informe)
        manifiesto = comprobar_manifiesto(informe)
        comprobar_evidencias(informe, manifiesto)
    except (OSError, ValueError, KeyError, TypeError, RecursionError) as e:
        informe.fallo(f'paquete incompleto o mal formado: {e}')
    if informe.fallos:
        print(f'\nRESULTADO: FALLO ({informe.fallos} comprobaciones no superadas)')
        return 1
    print('\nRESULTADO: OK en hashes, árbol y huellas de los sellos.')
    print('Falta comprobar la firma de los sellos con openssl y la del PDF (ver LEEME.txt).')
    return 0


if __name__ == '__main__':
    sys.exit(main())
