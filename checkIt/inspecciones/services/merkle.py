"""
Árbol Merkle SHA-256 al estilo de RFC 6962 (Certificate Transparency, §2.1).

Las hojas se hashean con el prefijo 0x00 y los nodos internos con 0x01, de modo
que un nodo interno no puede hacerse pasar por hoja. Con n hojas, el árbol se
parte por la mayor potencia de dos menor que n. Así la raíz es reproducible
por cualquier verificador con solo la lista ordenada de hashes.
"""
import hashlib


def _sha256(datos):
    return hashlib.sha256(datos).digest()


def _hash_hoja(hoja):
    return _sha256(b'\x00' + hoja)


def _hash_nodo(izquierda, derecha):
    return _sha256(b'\x01' + izquierda + derecha)


def _corte(n):
    # Mayor potencia de dos estrictamente menor que n (n >= 2)
    k = 1
    while k * 2 < n:
        k *= 2
    return k


def raiz(hojas):
    """Raíz (bytes) del árbol de `hojas`, una lista ordenada de bytes."""
    n = len(hojas)
    if n == 0:
        return _sha256(b'')
    if n == 1:
        return _hash_hoja(hojas[0])
    k = _corte(n)
    return _hash_nodo(raiz(hojas[:k]), raiz(hojas[k:]))


def prueba_inclusion(hojas, indice):
    """Ruta de auditoría (hashes hermanos, de la hoja hacia la raíz) de la hoja `indice`."""
    n = len(hojas)
    if not 0 <= indice < n:
        raise IndexError(f'Hoja {indice} fuera de rango (hay {n})')
    if n == 1:
        return []
    k = _corte(n)
    if indice < k:
        return prueba_inclusion(hojas[:k], indice) + [raiz(hojas[k:])]
    return prueba_inclusion(hojas[k:], indice - k) + [raiz(hojas[:k])]


def verificar_inclusion(hoja, indice, total, prueba, raiz_esperada):
    """Comprueba que `hoja` ocupa la posición `indice` de un árbol de `total` hojas con esa raíz."""
    if not 0 <= indice < total:
        return False
    # Algoritmo de RFC 9162 §2.1.3.2: recalcula la raíz recorriendo la ruta desde la hoja
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
