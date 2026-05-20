import { useEffect, useState } from "react";
import api from "../services/api";

const ESTADO = {
  COMPLETADA: {
    label: "Completada",
    cls: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  },
  EN_PROGRESO: {
    label: "En curso",
    cls: "bg-amber-50  text-amber-700  ring-amber-200",
  },
  PENDIENTE: {
    label: "Pendiente",
    cls: "bg-slate-100 text-slate-600  ring-slate-200",
  },
};

export default function AdminPage() {
  const [inspections, setInspections] = useState([]);
  const [error, setError] = useState(null);
  const [downloading, setDownloading] = useState(null);

  useEffect(() => {
    api
      .get("/inspecciones/")
      .then((res) => setInspections(res.data))
      .catch(() => setError("No se pudieron cargar las inspecciones."));
  }, []);

  const handleDownloadPDF = async (id) => {
    setDownloading(id);
    try {
      const token = localStorage.getItem("access_token");
      const response = await fetch(`/api/v1/inspecciones/${id}/claim-report/`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!response.ok) throw new Error("Sin permisos o informe no disponible");

      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `Reclamacion_Ckeckii_${id}.pdf`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
    } catch (err) {
      alert("Error al descargar el informe: " + err.message);
    } finally {
      setDownloading(null);
    }
  };

  const handleLogout = () => {
    localStorage.removeItem("access_token");
    localStorage.removeItem("refresh_token");
    window.location.href = "/login";
  };

  return (
    <div className="min-h-screen bg-slate-50">
      {/* Navbar */}
      <header className="bg-white border-b border-slate-200 px-6 py-4 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <span className="text-xl font-bold tracking-tight text-slate-900">
            Ckeckii
          </span>
          <span className="text-xs font-medium text-slate-400 bg-slate-100 px-2 py-0.5 rounded-full">
            Admin
          </span>
        </div>
        <button
          onClick={handleLogout}
          className="text-sm text-slate-500 hover:text-slate-800 transition font-medium"
        >
          Cerrar sesión
        </button>
      </header>

      <main className="max-w-6xl mx-auto px-6 py-10">
        {/* Título */}
        <div className="mb-8">
          <h1 className="text-2xl font-semibold text-slate-900">
            Inspecciones
          </h1>
          <p className="mt-1 text-sm text-slate-500">
            Gestión de check-outs y pruebas periciales
          </p>
        </div>

        {error && (
          <div className="mb-6 px-4 py-3 rounded-lg bg-red-50 border border-red-200 text-sm text-red-700">
            {error}
          </div>
        )}

        {/* Tabla */}
        <div className="bg-white rounded-xl border border-slate-200 overflow-hidden shadow-sm">
          <table className="min-w-full text-sm">
            <thead>
              <tr className="border-b border-slate-200 bg-slate-50">
                <th className="px-5 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wide w-16">
                  ID
                </th>
                <th className="px-5 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wide">
                  Propiedad
                </th>
                <th className="px-5 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wide">
                  Fecha
                </th>
                <th className="px-5 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wide">
                  Estado
                </th>
                <th className="px-5 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wide">
                  Informe
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {inspections.map((insp) => {
                const badge = ESTADO[insp.estado] ?? {
                  label: insp.estado,
                  cls: "bg-slate-100 text-slate-600 ring-slate-200",
                };
                return (
                  <tr key={insp.id} className="hover:bg-slate-50 transition">
                    <td className="px-5 py-3.5 font-mono text-xs text-slate-400">
                      #{insp.id}
                    </td>
                    <td className="px-5 py-3.5 font-medium text-slate-800">
                      {insp.propiedad_nombre}
                    </td>
                    <td className="px-5 py-3.5 text-slate-500">
                      {new Date(insp.fecha_creacion).toLocaleDateString(
                        "es-ES",
                      )}
                    </td>
                    <td className="px-5 py-3.5">
                      <span
                        className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ring-1 ${badge.cls}`}
                      >
                        {badge.label}
                      </span>
                    </td>
                    <td className="px-5 py-3.5">
                      {insp.estado === "COMPLETADA" ? (
                        <button
                          onClick={() => handleDownloadPDF(insp.id)}
                          disabled={downloading === insp.id}
                          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-blue-600 text-white text-xs font-semibold hover:bg-blue-700 active:bg-blue-800 disabled:opacity-50 transition"
                        >
                          {downloading === insp.id
                            ? "Generando…"
                            : "Descargar PDF"}
                        </button>
                      ) : (
                        <span className="text-slate-400 text-xs italic">
                          Pendiente de operario
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })}
              {inspections.length === 0 && !error && (
                <tr>
                  <td
                    colSpan={5}
                    className="px-5 py-10 text-center text-slate-400 text-sm"
                  >
                    No hay inspecciones registradas.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </main>
    </div>
  );
}
