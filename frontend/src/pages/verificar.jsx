// Verificador público: comprueba en el navegador que una foto está en un manifiesto sellado.
// No da un veredicto: separa lo calculado aquí de lo que declara CheckIt, y la firma de los
// sellos se deja a openssl. Sin ella, un servidor que mienta podría fabricar todo lo que aquí
// coincide: por eso nada de esto se pinta en verde. La clave no va nunca en la dirección de esta página.
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { consultarVerificacion } from "../services/verificacion";
import { calculateSHA256 } from "../utils/hash";
import { base64ABytes, comprobarRespuesta, normalizarClave } from "../utils/verificacion";

const CLAVE_HEX = /^[0-9a-f]{64}$/;
const MENSAJE_FORMATO = "La clave debe tener 64 caracteres hexadecimales, tal como aparece en el acta.";
const ID_ERROR = "verificar-error";

const MENSAJES_DE_ERROR = {
  no_encontrada:
    "Con esta clave no hay prueba de esta foto. Puede que la clave esté mal copiada, que la foto no sea de esa inspección o que no sea el archivo original: una copia recomprimida (por ejemplo, reenviada por WhatsApp) tiene otra huella.",
  formato: MENSAJE_FORMATO,
  servidor: "El servicio no ha podido responder. Vuelve a intentarlo dentro de un rato.",
  red: "No se ha podido contactar con el servicio. Revisa tu conexión y vuelve a intentarlo.",
  tiempo: "El servicio tarda demasiado: puede estar despertando. Vuelve a intentarlo en un minuto.",
};

function mensajeDeError({ tipo, segundos }) {
  if (tipo === "espera") {
    return segundos === null
      ? "Demasiadas consultas: espera un momento y vuelve a probar."
      : `Demasiadas consultas: espera ${segundos} s y vuelve a probar.`;
  }
  return MENSAJES_DE_ERROR[tipo] ?? MENSAJES_DE_ERROR.servidor;
}

// Avisos de la respuesta, en un solo bloque. Vacío si no hay nada que advertir.
function avisosDe(comprobacion) {
  if (!comprobacion.datosFiables) {
    return ["La respuesta del servidor no se entiende: no se puede concluir nada de ella."];
  }
  const avisos = [];
  const sellos = comprobacion.manifiestos.map((m) => m.sello);
  if (comprobacion.selloIndividual) sellos.push(comprobacion.selloIndividual);
  const noCuadra =
    !comprobacion.hashCoincide ||
    comprobacion.manifiestos.some((m) => !m.inclusion) ||
    sellos.some((sello) => sello.estado === "no_coincide" || sello.estado === "ilegible");
  if (noCuadra) {
    avisos.push(
      "La respuesta no se sostiene: lo que ha enviado el servidor no cuadra con el cálculo hecho aquí. Revisa cada comprobación.",
    );
  }
  if (!comprobacion.horasCoinciden) {
    avisos.push(
      "La hora que declara CheckIt no es la que lleva dentro el sello. Atente a la del sello, una vez validada su firma.",
    );
  }
  if (comprobacion.horasIlegibles) {
    avisos.push("No se ha podido leer la hora dentro de algún sello: su fecha queda sin comprobar.");
  }
  if (comprobacion.horasSinDeclarar) {
    avisos.push(
      "CheckIt no declara una hora legible para algún sello: no se ha podido contrastar con la que lleva dentro.",
    );
  }
  if (comprobacion.anomalias > 0) {
    avisos.push(
      `No se ha podido determinar el estado completo: CheckIt no ha podido leer o cuadrar ${comprobacion.anomalias} de sus datos guardados. Eso no significa que la foto no esté sellada.`,
    );
  }
  if (comprobacion.truncado) {
    avisos.push("Hay más manifiestos de los que se muestran: esta consulta no los ha revisado todos.");
  }
  return avisos;
}

function fechaLegible(texto) {
  const fecha = new Date(texto);
  if (typeof texto !== "string" || Number.isNaN(fecha.getTime())) return "no indicada";
  return `${fecha.toLocaleString("es-ES", { timeZone: "UTC" })} UTC`;
}

function descargarSello(token, archivo) {
  const url = URL.createObjectURL(new Blob([base64ABytes(token)], { type: "application/timestamp-reply" }));
  const enlace = document.createElement("a");
  enlace.href = url;
  enlace.download = archivo;
  document.body.appendChild(enlace);
  enlace.click();
  enlace.remove();
  // Revocarla en el acto deja la descarga vacía en algunos navegadores
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function Comprobacion({ correcta, children }) {
  return (
    <li className={`flex gap-2 text-sm ${correcta ? "text-slate-800" : "text-red-700 font-medium"}`}>
      <span aria-hidden="true">{correcta ? "✓" : "✕"}</span>
      <span>{children}</span>
    </li>
  );
}

const TEXTO_SELLO = {
  coincide: "La huella del sello coincide con esta raíz; su firma sigue sin comprobar.",
  no_coincide: "El sello no es de esta raíz.",
  ilegible: "El sello no se puede leer.",
  ausente: "Este manifiesto aún no tiene sello.",
};

const CLASE_ACCION =
  "inline-flex items-center min-h-11 px-2 -ml-2 mr-3 text-sm font-semibold text-blue-700 underline hover:text-blue-800";

// Solo con un sello que coincide: así la huella del comando es un hash ya comprobado.
function FirmaConOpenssl({ sello, huella, archivo }) {
  const [aviso, setAviso] = useState("");
  const comando = `openssl ts -verify -digest ${huella} -token_in -in ${archivo} -CAfile certificado-de-la-tsa.pem`;

  const copiar = async () => {
    try {
      await navigator.clipboard.writeText(comando);
      setAviso("Comando copiado.");
    } catch {
      setAviso("No se ha podido copiar: selecciona el comando a mano.");
    }
  };

  const descargar = () => {
    descargarSello(sello.token, archivo);
    setAviso(`Sello descargado: ${archivo}.`);
  };

  return (
    <div className="mt-3 rounded-lg bg-amber-50 border border-amber-200 px-3 py-2.5">
      <p className="text-sm text-amber-900">
        La firma de este sello no se valida en este navegador. Hazlo con openssl, usando el certificado
        de la TSA que viene en certificados_tsa/ del paquete probatorio o que publica la propia TSA:
      </p>
      <code className="block mt-2 text-xs break-all select-all text-slate-800">{comando}</code>
      <p className="mt-2 text-sm text-amber-900">Y esto muestra la hora firmada, que el comando anterior no imprime:</p>
      <code className="block mt-2 text-xs break-all select-all text-slate-800">
        {`openssl ts -reply -in ${archivo} -token_in -text`}
      </code>
      <div className="mt-1">
        <button type="button" onClick={copiar} className={CLASE_ACCION}>
          Copiar el comando
        </button>
        <button type="button" onClick={descargar} className={CLASE_ACCION}>
          Descargar el sello ({archivo})
        </button>
      </div>
      <p role="status" className="text-xs text-slate-700 min-h-4">
        {aviso}
      </p>
    </div>
  );
}

function HoraInterna({ sello }) {
  if (!sello.horaInterna) return null;
  return (
    <p className={`mt-3 text-sm ${sello.horaCoincide === false ? "text-red-700 font-medium" : "text-slate-800"}`}>
      Hora dentro del sello: {fechaLegible(sello.horaInterna)}. La cubre su firma, aún sin comprobar.
    </p>
  );
}

function Declarado({ sello }) {
  return (
    <div className="mt-3 text-xs text-slate-600">
      <p className="font-medium">Declarado por CheckIt, sin comprobar aquí:</p>
      <ul className="mt-1 space-y-0.5">
        <li>Hora del sello: {fechaLegible(sello.timestamp)}</li>
        {typeof sello.url === "string" && sello.url && <li className="break-all">TSA: {sello.url}</li>}
        {"cualificada" in sello && <li>TSA cualificada: {sello.cualificada === true ? "sí" : "no"}</li>}
        {"verificado" in sello && (
          <li>Firma comprobada por CheckIt al recibirlo: {sello.verificado === true ? "sí" : "no"}</li>
        )}
      </ul>
    </div>
  );
}

function TarjetaManifiesto({ manifiesto, numero, total }) {
  const { sello } = manifiesto;
  const archivo = `sello-manifiesto-${numero}.tst`;
  return (
    <article className="bg-white rounded-2xl shadow-sm border border-slate-200 p-5">
      <h3 className="text-sm font-semibold text-slate-800">
        Manifiesto {numero} de {total}
      </h3>
      <p className="text-xs text-slate-600 mb-3">
        {manifiesto.vigente ? "Vigente" : "Archivado: la inspección se reabrió después"}
      </p>
      <ul className="space-y-1">
        <Comprobacion correcta={manifiesto.inclusion}>
          {manifiesto.inclusion
            ? "La foto está incluida en este manifiesto."
            : "La foto no está incluida: la ruta no lleva a la raíz."}
        </Comprobacion>
        <Comprobacion correcta={sello.estado === "coincide"}>{TEXTO_SELLO[sello.estado]}</Comprobacion>
      </ul>
      {manifiesto.raiz && (
        <p className="mt-3 text-xs text-slate-600 break-all">
          Raíz: <code>{manifiesto.raiz}</code>
        </p>
      )}
      <HoraInterna sello={sello} />
      {sello.estado !== "ausente" && <Declarado sello={sello} />}
      {sello.estado === "coincide" && <FirmaConOpenssl sello={sello} huella={manifiesto.raiz} archivo={archivo} />}
    </article>
  );
}

function TarjetaSelloIndividual({ sello, huella }) {
  return (
    <article className="bg-white rounded-2xl shadow-sm border border-slate-200 p-5">
      <h3 className="text-sm font-semibold text-slate-800 mb-3">Sello individual</h3>
      <ul className="space-y-1">
        <Comprobacion correcta={sello.estado === "coincide"}>
          {sello.estado === "coincide"
            ? "La huella de este sello coincide con la de tu foto; su firma sigue sin comprobar."
            : sello.estado === "no_coincide"
              ? "Este sello no es de la huella de tu foto."
              : "Este sello está dañado y no se puede interpretar."}
        </Comprobacion>
      </ul>
      <HoraInterna sello={sello} />
      <Declarado sello={sello} />
      {sello.estado === "coincide" && (
        <FirmaConOpenssl sello={sello} huella={huella} archivo="sello-foto.tst" />
      )}
    </article>
  );
}

function Resultado({ huella, comprobacion }) {
  const encabezado = useRef(null);
  // Aparece bajo el formulario: sin esto, ni un lector de pantalla ni quien usa el móvil lo notan
  useEffect(() => {
    encabezado.current?.focus();
  }, []);

  const avisos = avisosDe(comprobacion);
  const { manifiestos, selloIndividual, datosFiables } = comprobacion;
  const sinManifiestos =
    datosFiables &&
    comprobacion.hashCoincide &&
    manifiestos.length === 0 &&
    comprobacion.anomalias === 0 &&
    !comprobacion.truncado;
  const primero = manifiestos[0];
  const primerSelloProbatorio =
    datosFiables &&
    manifiestos.length > 1 &&
    !comprobacion.truncado &&
    primero.inclusion &&
    primero.sello.estado === "coincide";

  return (
    <section aria-labelledby="verificar-resultado" className="mt-6 space-y-4">
      <h2
        id="verificar-resultado"
        ref={encabezado}
        tabIndex={-1}
        className="text-base font-semibold text-slate-800 focus:outline-none"
      >
        Resultado
      </h2>

      {avisos.length > 0 && (
        <div role="alert" className="px-4 py-3 rounded-lg bg-red-50 border border-red-200 text-sm text-red-700 space-y-1">
          {avisos.map((aviso) => (
            <p key={aviso}>{aviso}</p>
          ))}
        </div>
      )}

      {comprobacion.todoCoincide && (
        <p className="px-4 py-3 rounded-lg bg-slate-100 border border-slate-300 text-sm text-slate-800">
          Las comprobaciones hechas en este navegador coinciden, pero solo cierran la prueba cuando validas
          la firma de los sellos con openssl: sin ella, un servidor que mintiera podría haberlas fabricado.
        </p>
      )}

      {sinManifiestos && (
        <p className="px-4 py-3 rounded-lg bg-slate-100 border border-slate-300 text-sm text-slate-800">
          La foto consta en esta inspección, pero no está en ningún manifiesto sellado.
        </p>
      )}

      <p className="text-xs text-slate-600 break-all">
        Huella SHA-256 de tu foto, calculada aquí: <code>{huella}</code>
      </p>

      {primerSelloProbatorio && (
        <p className="text-xs text-slate-600">
          CheckIt declara este orden, del más antiguo al vigente. Si es así, el primer sello es el que prueba la
          anterioridad, una vez validada su firma.
        </p>
      )}

      {datosFiables &&
        manifiestos.map((manifiesto, posicion) => (
          <TarjetaManifiesto
            key={posicion}
            manifiesto={manifiesto}
            numero={posicion + 1}
            total={manifiestos.length}
          />
        ))}

      {datosFiables && selloIndividual && <TarjetaSelloIndividual sello={selloIndividual} huella={huella} />}
    </section>
  );
}

export default function Verificar() {
  const [foto, setFoto] = useState(null);
  const [clave, setClave] = useState("");
  const [fase, setFase] = useState("inicio");
  const [error, setError] = useState(null);
  const [campoErroneo, setCampoErroneo] = useState(null);
  const [resultado, setResultado] = useState(null);
  const campoFoto = useRef(null);
  const campoClave = useRef(null);

  const ocupada = fase === "calculando" || fase === "consultando";

  useEffect(() => {
    const anterior = document.title;
    document.title = "Verificar una foto · CheckIt";
    return () => {
      document.title = anterior;
    };
  }, []);

  const errorDeCampo = (campo, referencia, mensaje) => {
    setCampoErroneo(campo);
    setError(mensaje);
    referencia.current?.focus();
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (ocupada) return;
    setError(null);
    setCampoErroneo(null);
    setResultado(null);

    if (!foto) {
      errorDeCampo("foto", campoFoto, "Elige la foto que quieres comprobar.");
      return;
    }
    const claveLimpia = normalizarClave(clave);
    if (!CLAVE_HEX.test(claveLimpia)) {
      errorDeCampo("clave", campoClave, MENSAJE_FORMATO);
      return;
    }

    setFase("calculando");
    try {
      let huella;
      try {
        huella = await calculateSHA256(foto);
      } catch {
        setError("No se ha podido leer la foto. Prueba de nuevo o con otro navegador.");
        return;
      }

      setFase("consultando");
      const respuesta = await consultarVerificacion(claveLimpia, huella);
      if (respuesta.tipo !== "ok") {
        setError(mensajeDeError(respuesta));
        return;
      }
      setResultado({ huella, comprobacion: await comprobarRespuesta(huella, respuesta.datos) });
    } catch {
      setError("No se ha podido completar la comprobación en este navegador. Prueba con otro.");
    } finally {
      setFase("inicio");
    }
  };

  const describeFoto = campoErroneo === "foto" ? `verificar-foto-ayuda ${ID_ERROR}` : "verificar-foto-ayuda";
  const describeClave = campoErroneo === "clave" ? `verificar-clave-ayuda ${ID_ERROR}` : "verificar-clave-ayuda";

  return (
    <div className="min-h-screen bg-slate-50 px-4 py-10">
      <main className="w-full max-w-2xl mx-auto">
        <div className="text-center mb-8">
          <span className="text-3xl font-bold tracking-tight text-slate-900">CheckIt</span>
          <p className="mt-1 text-sm text-slate-600">Plataforma de inspecciones certificadas</p>
        </div>

        <div className="bg-white rounded-2xl shadow-sm border border-slate-200 p-6 sm:p-8">
          <h1 className="text-lg font-semibold text-slate-800 mb-2">Verificar una foto</h1>
          <p className="text-sm text-slate-600 mb-1">
            Comprueba, con la clave que figura en el acta, que una foto está en un manifiesto sellado.
            No necesitas cuenta.
          </p>
          <p className="text-sm text-slate-600 mb-6">
            Tu foto no sale de este navegador: aquí se calcula su huella y solo se envían esa huella y la clave.
          </p>

          <form onSubmit={handleSubmit} noValidate className="flex flex-col gap-4">
            <div>
              <label htmlFor="verificar-foto" className="block text-xs font-medium text-slate-600 mb-1.5">
                Foto
              </label>
              <p id="verificar-foto-ayuda" className="text-xs text-slate-600 mb-1.5">
                El archivo original del paquete probatorio.
              </p>
              <input
                id="verificar-foto"
                ref={campoFoto}
                type="file"
                aria-invalid={campoErroneo === "foto"}
                aria-describedby={describeFoto}
                disabled={ocupada}
                onChange={(e) => {
                  setFoto(e.target.files?.[0] ?? null);
                  setResultado(null);
                }}
                className="w-full rounded-lg border border-slate-500 text-sm text-slate-700 file:mr-3 file:px-3 file:py-3 file:border-0 file:bg-slate-100 file:text-slate-700 file:font-medium"
              />
            </div>

            <div>
              <label htmlFor="verificar-clave" className="block text-xs font-medium text-slate-600 mb-1.5">
                Clave de verificación
              </label>
              <p id="verificar-clave-ayuda" className="text-xs text-slate-600 mb-1.5">
                64 caracteres, tal como aparece en el acta.
              </p>
              <input
                id="verificar-clave"
                ref={campoClave}
                type="text"
                value={clave}
                aria-invalid={campoErroneo === "clave"}
                aria-describedby={describeClave}
                readOnly={ocupada}
                onChange={(e) => {
                  setClave(e.target.value);
                  setResultado(null);
                }}
                autoComplete="off"
                autoCapitalize="off"
                spellCheck={false}
                className="w-full px-3 py-2.5 rounded-lg border border-slate-500 text-sm text-slate-900 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent transition"
              />
            </div>

            {/* aria-disabled y no disabled: un botón deshabilitado pierde el foco de quien acaba de pulsarlo */}
            <button
              type="submit"
              aria-disabled={ocupada}
              className="mt-2 w-full py-2.5 rounded-lg bg-blue-600 text-white text-sm font-semibold hover:bg-blue-700 active:bg-blue-800 aria-disabled:opacity-50 transition"
            >
              {ocupada ? "Comprobando..." : "Comprobar"}
            </button>
          </form>

          {error && (
            <div
              id={ID_ERROR}
              role="alert"
              className="mt-4 px-4 py-3 rounded-lg bg-red-50 border border-red-200 text-sm text-red-700"
            >
              {error}
            </div>
          )}

          <div aria-live="polite" className="mt-3 text-xs text-slate-600 text-center min-h-4">
            {fase === "calculando" && "Calculando la huella de la foto..."}
            {fase === "consultando" && "Consultando... puede tardar hasta un minuto si el servicio está despertando."}
          </div>
        </div>

        {resultado && <Resultado huella={resultado.huella} comprobacion={resultado.comprobacion} />}

        <p className="mt-6 text-center text-xs text-slate-600">
          <Link to="/login" className="inline-block py-2 underline hover:text-slate-800">
            Acceso a la plataforma
          </Link>
        </p>
      </main>
    </div>
  );
}
