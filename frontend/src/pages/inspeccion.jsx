import { useState, useEffect, useRef } from "react";
import InspectionForm from "../components/inspectionForm";
import api, { salir } from "../services/api";
import {
  getRejectedEvidences,
  reintentarEvidencia,
  removeOfflineEvidence,
  syncPendingEvidences,
  syncPendingMarcas,
  EVENTO_BD_BLOQUEADA,
} from "../services/offline";
import { marcarPunto } from "../services/checklists";
import { formularioEvidencia } from "../services/formularioEvidencia";
import { horaDeArchivo } from "../utils/hora";

// Diccionarios para mostrar estado con etiqueta legible y estilo visual consistente.
const ESTADO_BADGE = {
  PENDIENTE: "bg-slate-100 text-slate-600 ring-slate-200",
  EN_PROGRESO: "bg-amber-50 text-amber-700 ring-amber-200",
  COMPLETADA: "bg-emerald-50 text-emerald-700 ring-emerald-200",
};
const ESTADO_LABEL = {
  PENDIENTE: "Pendiente",
  EN_PROGRESO: "En curso",
  COMPLETADA: "Completada",
};

// Sube una foto guardada en el móvil con el mismo multipart que una subida normal.
// capturedAt null = archivo elegido, sin hora fiable; las guardadas antes del #29 no
// tienen la clave y el archivo conserva su lastModified.
const subirGuardada = (evidence) =>
  api.post(
    `/inspecciones/${evidence.inspectionId}/evidencias/`,
    formularioEvidencia({
      file: evidence.file,
      room: evidence.room,
      description: evidence.description,
      hash: evidence.hash,
      punto: evidence.punto,
      capturedAt:
        "capturedAt" in evidence
          ? evidence.capturedAt
          : horaDeArchivo(evidence.file),
    }),
  );

export default function InspectionPage() {
  // Estado principal de la pantalla del operario.
  const [inspecciones, setInspecciones] = useState([]);
  const [seleccionada, setSeleccionada] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [syncInfo, setSyncInfo] = useState(null);
  // Sube tras cada sincronización con cambios: el formulario abierto recarga su checklist
  const [recargas, setRecargas] = useState(0);
  // Fotos guardadas en el móvil que el servidor rechazó (no se reintentan solas)
  const [rechazadas, setRechazadas] = useState([]);
  // Fallos al leer o escribir las fotos guardadas en el móvil (IndexedDB)
  const [avisoLocal, setAvisoLocal] = useState(null);
  const montada = useRef(false);
  // Solo vale la última lectura: una anterior podría traer una foto ya descartada
  const ultimaLectura = useRef(0);
  const tituloRechazadas = useRef(null);

  const cargarRechazadas = () => {
    const lectura = ++ultimaLectura.current;
    return getRejectedEvidences()
      .then((lista) => {
        if (montada.current && lectura === ultimaLectura.current) {
          setRechazadas(lista);
        }
      })
      .catch((e) => {
        // Se deja la lista que había: vaciarla escondería fotos que siguen en el móvil
        console.error("No se pudieron leer las fotos guardadas", e);
        if (montada.current) {
          setAvisoLocal("No se pudieron comprobar las fotos guardadas en este móvil.");
        }
      });
  };

  // Carga el listado de inspecciones asignadas al operario autenticado.
  const cargar = () =>
    api
      .get("/inspecciones/")
      .then((res) => setInspecciones(res.data))
      .catch(() => setError("No se pudieron cargar las inspecciones."))
      .finally(() => setLoading(false));

  // Si hay conexión, sube las fotos guardadas en el móvil
  // Marcas del checklist hechas sin red; las que el servidor rechaza se avisan todas
  const sincronizarMarcas = () =>
    syncPendingMarcas((m) =>
      marcarPunto(m.inspectionId, m.puntoId, {
        resultado: m.resultado,
        nota: m.nota || "",
        marcado_en_dispositivo: m.marcadoEnDispositivo,
      }),
    )
      .then((r) => {
        if (!montada.current) return;
        if (r.syncedCount > 0 || r.rejected.length > 0) setRecargas((n) => n + 1);
        if (r.rejected.length === 0) return;
        const n = r.rejected.length;
        const motivos = [...new Set(r.rejected.map((x) => x.motivo))].join(" / ");
        setAvisoLocal(
          `${n === 1 ? "1 marca del checklist no se aplicó" : `${n} marcas del checklist no se aplicaron`}: ${motivos}`,
        );
      })
      .catch((e) => {
        console.error("Falló la sincronización de las marcas del checklist", e);
        if (montada.current) {
          setAvisoLocal("No se pudieron sincronizar las marcas del checklist guardadas en este móvil; se volverá a intentar.");
        }
      });

  const sincronizar = () => {
    if (!navigator.onLine) return Promise.resolve();
    return Promise.all([sincronizarMarcas(), sincronizarFotos()]);
  };

  const sincronizarFotos = () =>
    syncPendingEvidences(subirGuardada)
      .then((result) => {
        if (!montada.current) return;
        if (result.rejected.length > 0) cargarRechazadas();
        if (result.syncedCount > 0) {
          setRecargas((n) => n + 1);
          // Tras sincronizar, refresca para reflejar estado/contador actualizado.
          setSyncInfo(
            `Se sincronizaron ${result.syncedCount} evidencias pendientes.`,
          );
          cargar();
        }
      })
      .catch((e) => {
        console.error("Falló la sincronización de las fotos guardadas", e);
        if (montada.current) {
          setAvisoLocal(
            "No se pudieron sincronizar las fotos guardadas en este móvil; se volverá a intentar.",
          );
        }
      });

  const descartar = async (r) => {
    const confirmado = window.confirm(
      `¿Descartar la foto de ${r.room || "sin estancia"}? Se borrará de este móvil y no se podrá recuperar.`,
    );
    if (!confirmado) return;
    try {
      await removeOfflineEvidence(r.id);
    } catch (e) {
      console.error("No se pudo descartar la foto", e);
      setAvisoLocal("No se pudo descartar la foto. Inténtalo de nuevo.");
      return;
    }
    await cargarRechazadas();
    // El botón pulsado desaparece: el foco vuelve al aviso si sigue en pantalla
    tituloRechazadas.current?.focus();
  };

  // Para rechazos que ya no aplican (p. ej. un permiso que se arregló)
  const reintentar = async (r) => {
    try {
      await reintentarEvidencia(r.id);
    } catch (e) {
      console.error("No se pudo volver a poner en cola la foto", e);
      setAvisoLocal("No se pudo reintentar la foto. Inténtalo de nuevo.");
      return;
    }
    await cargarRechazadas();
    tituloRechazadas.current?.focus();
    sincronizar();
  };

  useEffect(() => {
    montada.current = true;
    // Hidratación inicial de datos al entrar en la página.
    cargar();
    cargarRechazadas();
    // Reintenta sincronización al recuperar conectividad del dispositivo.
    sincronizar();
    window.addEventListener("online", sincronizar);
    const avisarBloqueo = () =>
      setAvisoLocal("CheckIt está abierto en otra pestaña con una versión anterior: ciérrala para guardar datos en este móvil.");
    window.addEventListener(EVENTO_BD_BLOQUEADA, avisarBloqueo);

    // Limpieza del listener al desmontar la página.
    return () => {
      montada.current = false;
      window.removeEventListener("online", sincronizar);
      window.removeEventListener(EVENTO_BD_BLOQUEADA, avisarBloqueo);
    };
    // Solo al montar: las funciones leen estado mediante refs y setters estables
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Solo se muestran inspecciones en curso o pendientes de completar.
  const activas = inspecciones.filter(
    (i) => i.estado === "PENDIENTE" || i.estado === "EN_PROGRESO",
  );

  return (
    <div className="min-h-screen bg-slate-50">
      <header className="bg-white border-b border-slate-200 px-6 py-4 flex items-center justify-between">
        <span className="text-xl font-bold tracking-tight text-slate-900">
          CheckIt
        </span>
        <div className="flex items-center gap-3">
          <span className="text-xs font-medium text-slate-600 bg-slate-100 px-2 py-0.5 rounded-full">
            Operario
          </span>
          <button
            // Anula el refresh en el servidor, limpia tokens y va a /login
            onClick={salir}
            className="text-sm text-slate-500 hover:text-slate-800 transition font-medium"
          >
            Cerrar sesión
          </button>
        </div>
      </header>

      <main className="max-w-lg mx-auto px-4 py-10">
        {/* Mensajes de sincronización y estado de carga/errores. */}
        {syncInfo && (
          <p className="mb-3 text-sm text-emerald-700">{syncInfo}</p>
        )}
        {avisoLocal && (
          <p role="alert" className="mb-3 text-sm text-red-600">
            {avisoLocal}
          </p>
        )}
        {rechazadas.length > 0 && (
          <section
            aria-labelledby="titulo-rechazadas"
            className="mb-4 px-4 py-3 rounded-lg border border-red-200 bg-red-50 text-sm text-red-700"
          >
            {/* Solo el título es región viva: la lista y los botones no se releen */}
            <div role="alert">
              <h2
                id="titulo-rechazadas"
                ref={tituloRechazadas}
                tabIndex={-1}
                className="font-semibold focus:outline-none"
              >
                El servidor no aceptó{" "}
                {rechazadas.length === 1
                  ? "una foto guardada"
                  : `${rechazadas.length} fotos guardadas`}{" "}
                en este móvil
              </h2>
            </div>
            <p className="mt-1">
              No aparecerán en el acta. Puedes reintentarlas o descartarlas y
              volver a tomarlas.
            </p>
            <ul className="mt-2 flex flex-col gap-2">
              {rechazadas.map((r, i) => {
                const cual = `la foto ${i + 1} de ${rechazadas.length} (${r.room || "sin estancia"})`;
                return (
                  <li key={r.id} className="flex items-start justify-between gap-3">
                    <span>
                      <span className="font-medium">{r.room || "Sin estancia"}</span>
                      : {r.motivo}
                    </span>
                    <span className="flex shrink-0 gap-3">
                      <button
                        type="button"
                        onClick={() => reintentar(r)}
                        aria-label={`Reintentar ${cual}`}
                        className="text-red-700 underline hover:text-red-900"
                      >
                        Reintentar
                      </button>
                      <button
                        type="button"
                        onClick={() => descartar(r)}
                        aria-label={`Descartar ${cual}`}
                        className="text-red-700 underline hover:text-red-900"
                      >
                        Descartar
                      </button>
                    </span>
                  </li>
                );
              })}
            </ul>
          </section>
        )}
        {loading && (
          <p className="text-sm text-slate-500">Cargando inspecciones…</p>
        )}
        {error && <p className="text-sm text-red-600">{error}</p>}

        {!loading && !error && (
          <>
            {/* ── Vista detalle de una inspección ── */}
            {seleccionada ? (
              <>
                <div className="mb-6 flex items-start justify-between gap-4">
                  <div>
                    <h1 className="text-xl font-semibold text-slate-900">
                      {seleccionada.propiedad_nombre} · #{seleccionada.id}
                    </h1>
                    <p className="mt-1 text-sm text-slate-500">
                      Registra los daños encontrados durante el check-out
                    </p>
                  </div>
                  <button
                    onClick={() => {
                      // Al volver, recarga para ver cambios hechos dentro del formulario.
                      setSeleccionada(null);
                      cargar();
                    }}
                    className="shrink-0 text-sm text-slate-500 hover:text-slate-800 font-medium transition"
                  >
                    ← Volver
                  </button>
                </div>
                <InspectionForm key={seleccionada.id} inspectionId={seleccionada.id} recarga={recargas} />
              </>
            ) : (
              /* ── Lista de inspecciones activas ── */
              <>
                <div className="mb-6">
                  <h1 className="text-2xl font-semibold text-slate-900">
                    Mis inspecciones
                  </h1>
                  <p className="mt-1 text-sm text-slate-500">
                    Selecciona una para comenzar
                  </p>
                </div>

                {activas.length === 0 ? (
                  <p className="text-sm text-slate-500">
                    No tienes inspecciones pendientes asignadas.
                  </p>
                ) : (
                  // Cada tarjeta abre el formulario de captura para esa inspección.
                  <ul className="space-y-3">
                    {activas.map((insp) => (
                      <li key={insp.id}>
                        <button
                          onClick={() => setSeleccionada(insp)}
                          className="w-full text-left bg-white border border-slate-200 rounded-xl px-5 py-4 shadow-sm hover:border-blue-400 hover:shadow-md transition group"
                        >
                          <div className="flex items-center justify-between gap-3">
                            <div className="min-w-0">
                              <p className="font-medium text-slate-800 truncate group-hover:text-blue-600 transition">
                                {insp.propiedad_nombre}
                              </p>
                              <p className="text-xs text-slate-600 mt-0.5">
                                #{insp.id} ·{" "}
                                {new Date(
                                  insp.fecha_creacion,
                                ).toLocaleDateString("es-ES")}
                              </p>
                            </div>
                            <span
                              className={`shrink-0 inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ring-1 ${ESTADO_BADGE[insp.estado]}`}
                            >
                              {ESTADO_LABEL[insp.estado]}
                            </span>
                          </div>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </>
            )}
          </>
        )}
      </main>
    </div>
  );
}
