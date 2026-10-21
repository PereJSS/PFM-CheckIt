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
    manifiesto = json.loads(_leer('manifiesto/manifiesto.json'))
    hojas = [bytes.fromhex(h['sha256']) for h in manifiesto['hojas']]
    calculada = raiz(hojas).hex()
    if calculada == manifiesto['raiz']:
        informe.ok(f'raíz {calculada} ({len(hojas)} hojas)')
    else:
        informe.fallo(f'la raíz recalculada {calculada} no es la del manifiesto {manifiesto["raiz"]}')
    sellada = huella_sellada(_leer('manifiesto/raiz.tst'))
    if sellada == calculada:
        informe.ok('manifiesto/raiz.tst sella esa raíz')
    else:
        informe.fallo(f'manifiesto/raiz.tst sella {sellada}, no la raíz recalculada')
    return manifiesto


def comprobar_evidencias(informe, manifiesto):
    print('\n3. Cada foto, su hoja y su ruta hasta la raíz')
    expediente = json.loads(_leer('expediente.json'))
    raiz_sellada = bytes.fromhex(manifiesto['raiz'])
    vistas = set()
    for ficha in expediente['evidencias']:
        archivo = ficha['archivo']
        try:
            datos = _leer(archivo)
            prueba = json.loads(_leer(ficha['prueba']))
        except (OSError, ValueError) as e:
            informe.fallo(f'{archivo}: {e}')
            continue
        indice = prueba['indice']
        hoja = manifiesto['hojas'][indice] if 0 <= indice < len(manifiesto['hojas']) else {}
        real = hashlib.sha256(datos).hexdigest()
        if real != hoja.get('sha256'):
            informe.fallo(f'{archivo}: su hash no es el de la hoja {indice} del manifiesto')
            continue
        ruta = [bytes.fromhex(h) for h in prueba['ruta']]
        if not verificar_inclusion(bytes.fromhex(real), indice, len(manifiesto['hojas']), ruta, raiz_sellada):
            informe.fallo(f'{archivo}: la ruta de inclusión no lleva a la raíz sellada')
            continue
        if ficha.get('sello') and huella_sellada(_leer(ficha['sello'])) != real:
            informe.fallo(f'{ficha["sello"]}: no sella el hash de {archivo}')
            continue
        vistas.add(indice)
        informe.ok(f'{archivo} (hoja {indice})')
    faltan = set(range(len(manifiesto['hojas']))) - vistas
    if faltan:
        informe.fallo(f'hojas del manifiesto sin foto verificada: {sorted(faltan)}')


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
    except (OSError, ValueError, KeyError, TypeError) as e:
        informe.fallo(f'paquete incompleto o mal formado: {e}')
    if informe.fallos:
        print(f'\nRESULTADO: FALLO ({informe.fallos} comprobaciones no superadas)')
        return 1
    print('\nRESULTADO: OK en hashes, árbol y huellas de los sellos.')
    print('Falta comprobar la firma de los sellos con openssl y la del PDF (ver LEEME.txt).')
    return 0


if __name__ == '__main__':
    sys.exit(main())
