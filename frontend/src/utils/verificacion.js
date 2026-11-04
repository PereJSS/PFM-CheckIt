// Comprobaciones del verificador público, hechas en el navegador para no fiarse del servidor.
// Es un port de checkIt/inspecciones/services/verificador_paquete.py: los vectores de
// __tests__/vectores_verificacion.json se revalidan contra Python en tests_verificacion.py.
// La firma del sello NO se valida aquí: eso lo hace `openssl ts -verify`.

const HASH_HEX = /^[0-9a-f]{64}$/;
const GENERALIZED_TIME = /^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})(?:\.\d+)?Z$/;
// Más de lo que puede salir de un árbol o de la API: una respuesta mayor no se recorre
const MAX_RUTA = 64;
const MAX_MANIFIESTOS = 64;
const BASE64 = /^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/;

// OID de SHA-256 (2.16.840.1.101.3.4.2.1) y de id-ct-TSTInfo (1.2.840.113549.1.9.16.1.4), en DER
const OID_SHA256 = "608648016503040201";
const OID_TSTINFO = "2a864886f70d0109100104";

const aHex = (bytes) =>
  Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");

function hashABytes(hex) {
  if (typeof hex !== "string" || !HASH_HEX.test(hex)) {
    throw new Error("no es un SHA-256 en hexadecimal");
  }
  return Uint8Array.from(hex.match(/../g), (par) => Number.parseInt(par, 16));
}

async function sha256(prefijo, ...partes) {
  const largo = partes.reduce((suma, parte) => suma + parte.length, 1);
  const datos = new Uint8Array(largo);
  datos[0] = prefijo;
  let posicion = 1;
  for (const parte of partes) {
    datos.set(parte, posicion);
    posicion += parte.length;
  }
  return new Uint8Array(await crypto.subtle.digest("SHA-256", datos));
}

const hashHoja = (hoja) => sha256(0x00, hoja);
const hashNodo = (izquierda, derecha) => sha256(0x01, izquierda, derecha);
const mitad = (n) => Math.floor(n / 2);

// RFC 9162 §2.1.3.2. Ojo: la ruta no siempre prueba `total`, así que no se presenta como comprobado.
export async function verificarInclusion(hoja, indice, total, prueba, raiz) {
  if (!Number.isSafeInteger(indice) || !Number.isSafeInteger(total)) return false;
  if (indice < 0 || indice >= total || !Array.isArray(prueba) || prueba.length > MAX_RUTA) return false;

  let bytesHoja, ruta;
  try {
    bytesHoja = hashABytes(hoja);
    hashABytes(raiz);
    ruta = prueba.map(hashABytes);
  } catch {
    return false;
  }

  let fn = indice;
  let sn = total - 1;
  let r = await hashHoja(bytesHoja);
  for (const hermano of ruta) {
    if (sn === 0) return false;
    if (fn % 2 === 1 || fn === sn) {
      r = await hashNodo(hermano, r);
      while (fn % 2 === 0 && fn !== 0) {
        fn = mitad(fn);
        sn = mitad(sn);
      }
    } else {
      r = await hashNodo(r, hermano);
    }
    fn = mitad(fn);
    sn = mitad(sn);
  }
  return sn === 0 && aHex(r) === raiz;
}

// Contenido y resto de un elemento DER con la etiqueta esperada.
function der(datos, etiqueta) {
  if (datos.length < 2 || datos[0] !== etiqueta) {
    throw new Error("sello de tiempo mal formado");
  }
  let largo = datos[1];
  let posicion = 2;
  if (largo & 0x80) {
    const octetos = largo & 0x7f;
    if (octetos < 1 || octetos > 4 || datos.length < 2 + octetos) {
      throw new Error("sello de tiempo mal formado");
    }
    largo = 0;
    for (const octeto of datos.subarray(2, 2 + octetos)) largo = largo * 256 + octeto;
    posicion = 2 + octetos;
  }
  if (posicion + largo > datos.length) throw new Error("sello de tiempo truncado");
  return [datos.subarray(posicion, posicion + largo), datos.subarray(posicion + largo)];
}

const saltar = (datos) => der(datos, datos.length ? datos[0] : -1)[1];

// Campos de TSTInfo a partir de messageImprint (RFC 3161 §2.4.2), sin validar la firma.
function desdeImprint(token) {
  const [contentInfo] = der(token, 0x30);
  let resto = saltar(contentInfo); // contentType (id-signedData)
  const [signedData] = der(der(resto, 0xa0)[0], 0x30);
  resto = saltar(saltar(signedData)); // version, digestAlgorithms
  const [encap] = der(resto, 0x30);
  let tipo;
  [tipo, resto] = der(encap, 0x06);
  if (aHex(tipo) !== OID_TSTINFO) throw new Error("el sello no es un TimeStampToken");
  const [octetos] = der(der(resto, 0xa0)[0], 0x04);
  const [tstInfo] = der(octetos, 0x30);
  return saltar(saltar(tstInfo)); // version, policy
}

// Hash SHA-256 (hex) que sella un TimeStampToken DER (RFC 3161, messageImprint).
export function huellaSellada(token) {
  const [imprint] = der(desdeImprint(token), 0x30);
  const [algoritmo, hashed] = der(imprint, 0x30);
  const [oid] = der(algoritmo, 0x06);
  if (aHex(oid) !== OID_SHA256) throw new Error("el sello no es de un hash SHA-256");
  return aHex(der(hashed, 0x04)[0]);
}

// Hora (ISO, UTC, sin fracción) que lleva dentro el sello: genTime, cubierta por su firma.
export function horaSellada(token) {
  const [, trasImprint] = der(desdeImprint(token), 0x30);
  const [genTime] = der(saltar(trasImprint), 0x18); // tras serialNumber
  const partes = String.fromCharCode(...genTime).match(GENERALIZED_TIME);
  if (!partes) throw new Error("hora del sello mal formada");
  const [, anyo, mes, dia, hora, minuto, segundo] = partes;
  const fecha = new Date(Date.UTC(+anyo, +mes - 1, +dia, +hora, +minuto, +segundo));
  if (fecha.getUTCMonth() !== +mes - 1 || fecha.getUTCDate() !== +dia) {
    throw new Error("hora del sello mal formada");
  }
  return fecha.toISOString().replace(".000Z", "Z");
}

export function base64ABytes(texto) {
  if (typeof texto !== "string" || !BASE64.test(texto)) {
    throw new Error("no es base64");
  }
  return Uint8Array.from(atob(texto), (caracter) => caracter.charCodeAt(0));
}

// La clave se copia de un PDF: puede traer espacios y saltos de línea.
export const normalizarClave = (texto) => String(texto).replace(/\s+/g, "").toLowerCase();

const esObjeto = (valor) => typeof valor === "object" && valor !== null;

const aSegundos = (fecha) => Math.floor(new Date(fecha).getTime() / 1000);

function comprobarSello(sello, huellaEsperada) {
  if (!esObjeto(sello)) return { estado: "ausente", horaInterna: null, horaCoincide: null };
  let estado;
  let horaInterna = null;
  try {
    const token = base64ABytes(sello.token);
    estado = huellaSellada(token) === huellaEsperada ? "coincide" : "no_coincide";
    horaInterna = horaSellada(token);
  } catch {
    if (estado === undefined) estado = "ilegible";
  }
  // Solo se compara si el servidor declara una hora que se entienda
  const declarada = typeof sello.timestamp === "string" ? aSegundos(sello.timestamp) : NaN;
  const horaCoincide =
    horaInterna === null || Number.isNaN(declarada) ? null : declarada === aSegundos(horaInterna);
  return { ...sello, estado, horaInterna, horaCoincide };
}

async function comprobarManifiesto(foto, manifiesto) {
  const datos = esObjeto(manifiesto) ? manifiesto : {};
  return {
    vigente: datos.vigente === true,
    raiz: typeof datos.raiz === "string" ? datos.raiz : null,
    inclusion: await verificarInclusion(foto, datos.indice, datos.total, datos.prueba, datos.raiz),
    sello: comprobarSello(datos.sello, datos.raiz),
  };
}

// `foto` es el hash calculado aquí; de la respuesta solo se cree lo que se puede recalcular.
export async function comprobarRespuesta(foto, datos) {
  const respuesta = esObjeto(datos) ? datos : {};
  const manejable =
    Array.isArray(respuesta.manifiestos) && respuesta.manifiestos.length <= MAX_MANIFIESTOS;
  const lista = manejable ? respuesta.manifiestos : [];
  const manifiestos = await Promise.all(lista.map((m) => comprobarManifiesto(foto, m)));
  const selloIndividual = esObjeto(respuesta.sello_individual)
    ? comprobarSello(respuesta.sello_individual, foto)
    : null;

  const datosFiables =
    manejable &&
    Number.isInteger(respuesta.anomalias) &&
    respuesta.anomalias >= 0 &&
    typeof respuesta.truncado === "boolean";
  const hashCoincide = respuesta.hash === foto;
  const sellos = manifiestos.map((m) => m.sello);
  if (selloIndividual) sellos.push(selloIndividual);
  const horasCoinciden = sellos.every((sello) => sello.horaCoincide !== false);
  // No poder comparar la hora no es coincidir: el sello sirve para fechar. Solo cuentan los
  // sellos cuya huella coincide; de los demás ya se avisa por otro lado.
  const validos = sellos.filter((sello) => sello.estado === "coincide");
  const horasIlegibles = validos.some((sello) => sello.horaInterna === null);
  const horasSinDeclarar = validos.some((sello) => sello.horaInterna !== null && sello.horaCoincide === null);

  return {
    hashCoincide,
    horasCoinciden,
    horasIlegibles,
    horasSinDeclarar,
    datosFiables,
    anomalias: datosFiables ? respuesta.anomalias : null,
    truncado: respuesta.truncado === true,
    manifiestos,
    selloIndividual,
    todoCoincide:
      datosFiables &&
      hashCoincide &&
      horasCoinciden &&
      !horasIlegibles &&
      !horasSinDeclarar &&
      respuesta.anomalias === 0 &&
      respuesta.truncado === false &&
      manifiestos.length > 0 &&
      manifiestos.every((m) => m.inclusion && m.sello.estado === "coincide") &&
      (selloIndividual === null || selloIndividual.estado === "coincide"),
  };
}
