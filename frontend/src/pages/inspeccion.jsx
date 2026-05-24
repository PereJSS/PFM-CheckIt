import { useState, useEffect } from "react";
import InspectionForm from "../components/inspectionForm";
import api from "../services/api";

export default function InspectionPage() {
  const [inspeccion, setInspeccion] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    api
      .get("/inspecciones/")
      .then((res) => {
        const pendiente = res.data.find(
          (i) => i.estado === "PENDIENTE" || i.estado === "EN_PROGRESO",
        );
        setInspeccion(pendiente || null);
      })
      .catch(() => setError("No se pudieron cargar las inspecciones."))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="min-h-screen bg-slate-50">
      <header className="bg-white border-b border-slate-200 px-6 py-4 flex items-center justify-between">
        <span className="text-xl font-bold tracking-tight text-slate-900">
          CheckIt
        </span>
        <span className="text-xs font-medium text-slate-400 bg-slate-100 px-2 py-0.5 rounded-full">
          Operario
        </span>
      </header>

      <main className="max-w-lg mx-auto px-4 py-10">
        {loading && (
          <p className="text-sm text-slate-500">Cargando inspección…</p>
        )}

        {error && <p className="text-sm text-red-600">{error}</p>}

        {!loading && !error && !inspeccion && (
          <p className="text-sm text-slate-500">
            No tienes inspecciones pendientes asignadas.
          </p>
        )}

        {!loading && inspeccion && (
          <>
            <div className="mb-6">
              <h1 className="text-xl font-semibold text-slate-900">
                {inspeccion.propiedad_nombre} · #{inspeccion.id}
              </h1>
              <p className="mt-1 text-sm text-slate-500">
                Registra los daños encontrados durante el check-out
              </p>
            </div>
            <InspectionForm inspectionId={inspeccion.id} />
          </>
        )}
      </main>
    </div>
  );
}
