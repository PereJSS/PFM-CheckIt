// Avisos de la PWA (#15): hay una versión nueva, o la app ya funciona sin conexión.
// - Nunca recarga sola: el operario puede estar a mitad de una inspección.
// - Va en el flujo, arriba, y no fija: así no tapa el elemento con foco (WCAG 2.4.11) ni
//   el modal de la cámara, y queda al principio del orden de tabulación. No mueve el foco.
// - La zona que se anuncia (role="status") está siempre montada y solo con el texto: un
//   lector de pantalla no anuncia una región que aparece ya llena.
import { useEffect, useRef, useState } from "react";
import { useRegisterSW } from "virtual:pwa-register/react";
import { getAllPendingEvidences, getMarcasPendientes } from "../services/offline";

// La app puede pasar el día abierta: sin esto solo buscaría versiones al navegar
export const INTERVALO_COMPROBACION_MS = 60 * 60 * 1000;
// En el móvil los temporizadores se congelan en segundo plano: también al volver a la app,
// pero no más de una vez en este margen
export const MARGEN_AL_VOLVER_MS = 15 * 60 * 1000;

const boton =
  "min-h-11 px-4 rounded-lg font-semibold focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white";

export default function AvisoActualizacion() {
  const montado = useRef(false);
  const temporizador = useRef(null);
  const registroRef = useRef(null);
  const ultimaComprobacion = useRef(0);
  const recargaPendiente = useRef(null);

  const comprobar = () => {
    const registro = registroRef.current;
    if (!registro || !navigator.onLine) return;
    ultimaComprobacion.current = Date.now();
    registro.update().catch((e) => console.error("No se pudo buscar una versión nueva", e));
  };

  const {
    needRefresh: [hayVersion, setHayVersion],
    offlineReady: [listaSinConexion, setListaSinConexion],
    updateServiceWorker,
  } = useRegisterSW({
    onRegisteredSW(_url, registro) {
      // Llega de forma asíncrona: si ya no está montado, no deja un temporizador vivo
      if (!registro || !montado.current) return;
      registroRef.current = registro;
      ultimaComprobacion.current = Date.now();
      clearInterval(temporizador.current);
      temporizador.current = setInterval(comprobar, INTERVALO_COMPROBACION_MS);
    },
    onRegisterError(e) {
      console.error("No se pudo registrar el service worker", e);
    },
  });
  // null mientras se cuentan: el aviso sale con su texto completo y se anuncia una vez
  const [pendientes, setPendientes] = useState(null);

  const quitarRecarga = () => {
    if (recargaPendiente.current) {
      navigator.serviceWorker?.removeEventListener("controllerchange", recargaPendiente.current);
      recargaPendiente.current = null;
    }
  };

  useEffect(() => {
    montado.current = true;
    const alVolver = () => {
      if (document.visibilityState !== "visible") return;
      if (Date.now() - ultimaComprobacion.current < MARGEN_AL_VOLVER_MS) return;
      comprobar();
    };
    document.addEventListener("visibilitychange", alVolver);
    return () => {
      montado.current = false;
      clearInterval(temporizador.current);
      document.removeEventListener("visibilitychange", alVolver);
      quitarRecarga();
    };
    // Solo al montar: las funciones usan refs
  }, []);

  useEffect(() => {
    if (!hayVersion) return undefined;
    let vigente = true;
    Promise.all([getAllPendingEvidences(), getMarcasPendientes()])
      .then(([fotos, marcas]) => {
        if (vigente) setPendientes(fotos.length + marcas.length);
      })
      .catch((e) => {
        console.error("No se pudieron contar los pendientes", e);
        if (vigente) setPendientes(0);
      });
    return () => {
      vigente = false;
      setPendientes(null);
    };
  }, [hayVersion]);

  // El plugin solo recarga si workbox-window marca el cambio como actualización, y en una
  // pestaña abierta desde la primera visita no lo hace: se recarga aquí, una sola vez y
  // solo tras pulsar (otra pestaña que actualice no debe recargar esta)
  const actualizar = () => {
    quitarRecarga();
    const recargar = () => {
      quitarRecarga();
      window.location.reload();
    };
    recargaPendiente.current = recargar;
    navigator.serviceWorker?.addEventListener("controllerchange", recargar);
    Promise.resolve(updateServiceWorker(true)).catch((e) => {
      quitarRecarga();
      console.error("No se pudo activar la versión nueva", e);
    });
  };

  const masTarde = () => {
    setHayVersion(false);
    setListaSinConexion(false);
  };

  const conVersion = hayVersion && pendientes !== null;
  const conSinConexion = !hayVersion && listaSinConexion;
  const visible = conVersion || conSinConexion;

  let texto = "";
  if (conVersion) {
    texto = "Hay una versión nueva de CheckIt. Termina lo que estés haciendo y actualiza.";
    if (pendientes === 1) texto += " El elemento pendiente de subir se conserva en este móvil.";
    if (pendientes > 1) texto += ` Los ${pendientes} elementos pendientes de subir se conservan en este móvil.`;
  } else if (conSinConexion) {
    texto = "CheckIt ya funciona sin conexión en este dispositivo.";
  }

  return (
    <section
      aria-label={visible ? "Avisos de CheckIt" : undefined}
      className={visible ? "bg-slate-900 text-white px-4 py-3" : undefined}
    >
      <div className={visible ? "mx-auto max-w-2xl flex flex-wrap items-center gap-3" : undefined}>
        <p role="status" aria-atomic="true" className={visible ? "flex-1 min-w-[12rem]" : "sr-only"}>
          {texto}
        </p>
        {conVersion && (
          <>
            <button type="button" className={`${boton} bg-white text-slate-900 hover:bg-slate-100`} onClick={actualizar}>
              Actualizar
            </button>
            <button type="button" className={`${boton} border-2 border-white hover:bg-slate-800`} onClick={masTarde}>
              Más tarde
            </button>
          </>
        )}
        {conSinConexion && (
          <button
            type="button"
            className={`${boton} border-2 border-white hover:bg-slate-800`}
            onClick={() => setListaSinConexion(false)}
          >
            Entendido
          </button>
        )}
      </div>
    </section>
  );
}
