"""Reglas para textos cortos que escribe el usuario y salen en el acta (estancias, puntos
del checklist). Viven aquí y no en una app para que propiedades e inspecciones las
compartan sin importarse entre sí."""
import unicodedata

# Control, formato (bidi, ancho cero) y separadores de línea: harían que el acta
# mostrase algo distinto de lo guardado
_CATEGORIAS_PROHIBIDAS = frozenset({'Cc', 'Cf', 'Cs', 'Zl', 'Zp'})


def normalizar_texto_corto(valor):
    """NFC y sin espacios en los extremos: «ñ» compuesta cuenta como un carácter."""
    return unicodedata.normalize('NFC', valor).strip()


def motivo_texto_corto_no_valido(texto, maximo, campo):
    """Por qué no vale un texto corto ya normalizado, o None."""
    if any(unicodedata.category(c) in _CATEGORIAS_PROHIBIDAS for c in texto):
        return f"{campo} no puede contener caracteres de control ni de dirección del texto."
    if len(texto) > maximo:
        return f"{campo} no puede tener más de {maximo} caracteres."
    return None
