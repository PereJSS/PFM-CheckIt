import { useState, useEffect } from "react";
import InspectionForm from "../components/inspectionForm";
import api from "../services/api";
import { syncPendingEvidences } from "../services/offline";
import { clearAuthTokens } from "../services/authStorage";

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

export default function InspectionPage() {
  // Estado principal de la pantalla del operario.
  const [inspecciones, setInspecciones] = useState([]);
  const [seleccionada, setSeleccionada] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [syncInfo, setSyncInfo] = useState(null);

  // Carga el listado de inspecciones asignadas al operario autenticado.
  const cargar = () =>
    api
      .get("/inspecciones/")
      .then((res) => setInspecciones(res.data))
      .catch(() => setError("No se pudieron cargar las inspecciones."))
      .finally(() => setLoading(false));

  useEffect(() => {
    // Hidratación inicial de datos al entrar en la página.
    cargar();

    // Si hay conexión, intenta subir automáticamente evidencias guardadas offline.
    const syncOfflineNow = async () => {
      if (!navigator.onLine) return;

      const result = await syncPendingEvidences(async (evidence) => {
        // Reconstruye payload multipart igual que una subida normal online.
        const formData = new FormData();
        const fallbackName = `evidencia_${Date.now()}.jpg`;

        formData.append(
          "foto",
          evidence.file,
          evidence.file?.name || fallbackName,
        );
        formData.append(
          "descripcion",
          evidence.description || evidence.room || "Evidencia sin descripción",
        );

        if (evidence.hash) {
          formData.append("hash_sha256", evidence.hash);
        }

        await api.post(
          `/inspecciones/${evidence.inspectionId}/evidencias/`,
          formData,
        );
      });

      if (result.syncedCount > 0) {
        // Tras sincronizar, refresca para reflejar estado/contador actualizado.
        setSyncInfo(
          `Se sincronizaron ${result.syncedCount} evidencias pendientes.`,
        );
        cargar();
      }
    };

    // Reintenta sincronización al recuperar conectividad del dispositivo.
    syncOfflineNow();
    window.addEventListener("online", syncOfflineNow);

    // Limpieza del listener al desmontar la página.
    return () => {
      window.removeEventListener("online", syncOfflineNow);
    };
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
          <span className="text-xs font-medium text-slate-400 bg-slate-100 px-2 py-0.5 rounded-full">
            Operario
          </span>
          <button
            onClick={() => {
              // Logout local simple para SPA: limpia tokens y redirige.
              clearAuthTokens();
              window.location.href = "/login";
            }}
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
                <InspectionForm inspectionId={seleccionada.id} />
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
                              <p className="text-xs text-slate-400 mt-0.5">
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
