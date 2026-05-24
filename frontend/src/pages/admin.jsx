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

const FORM_EMPTY = { propiedad: "", operario: "" };

export default function AdminPage() {
  const [inspections, setInspections] = useState([]);
  const [propiedades, setPropiedades] = useState([]);
  const [operarios, setOperarios] = useState([]);
  const [error, setError] = useState(null);
  const [downloading, setDownloading] = useState(null);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState(FORM_EMPTY);
  const [formError, setFormError] = useState(null);
  const [saving, setSaving] = useState(false);

  const loadInspections = () =>
    api
      .get("/inspecciones/")
      .then((res) => setInspections(res.data))
      .catch(() => setError("No se pudieron cargar las inspecciones."));

  useEffect(() => {
    loadInspections();
    api.get("/propiedades/").then((res) => setPropiedades(res.data));
    api.get("/usuarios/operarios/").then((res) => setOperarios(res.data));
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
      a.download = `Reclamacion_CheckIt_${id}.pdf`;
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

  const handleSubmit = async (e) => {
    e.preventDefault();
    setFormError(null);
    if (!form.propiedad || !form.operario) {
      setFormError("Selecciona una propiedad y un operario.");
      return;
    }
    setSaving(true);
    try {
      await api.post("/inspecciones/", {
        propiedad: Number(form.propiedad),
        operario: Number(form.operario),
        estado: "PENDIENTE",
      });
      setForm(FORM_EMPTY);
      setShowForm(false);
      loadInspections();
    } catch {
      setFormError("No se pudo crear la inspección. Inténtalo de nuevo.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="min-h-screen bg-slate-50">
      {/* Navbar */}
      <header className="bg-white border-b border-slate-200 px-6 py-4 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <span className="text-xl font-bold tracking-tight text-slate-900">
            CheckIt
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
        {/* Título + botón nueva inspección */}
        <div className="mb-8 flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-semibold text-slate-900">
              Inspecciones
            </h1>
            <p className="mt-1 text-sm text-slate-500">
              Gestión de check-outs y pruebas periciales
            </p>
          </div>
          <button
            onClick={() => {
              setShowForm((v) => !v);
              setFormError(null);
              setForm(FORM_EMPTY);
            }}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-blue-600 text-white text-sm font-semibold hover:bg-blue-700 transition"
          >
            {showForm ? "Cancelar" : "+ Nueva inspección"}
          </button>
        </div>

        {/* Formulario de nueva inspección */}
        {showForm && (
          <form
            onSubmit={handleSubmit}
            className="mb-8 bg-white border border-slate-200 rounded-xl p-6 shadow-sm"
          >
            <h2 className="text-base font-semibold text-slate-800 mb-4">
              Asignar nueva inspección
            </h2>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div>
                <label className="block text-xs font-medium text-slate-500 mb-1">
                  Propiedad
                </label>
                <select
                  value={form.propiedad}
                  onChange={(e) =>
                    setForm((f) => ({ ...f, propiedad: e.target.value }))
                  }
                  className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-800 bg-white focus:outline-none focus:ring-2 focus:ring-blue-500"
                >
                  <option value="">Selecciona una propiedad…</option>
                  {propiedades.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.nombre}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-500 mb-1">
                  Operario
                </label>
                <select
                  value={form.operario}
                  onChange={(e) =>
                    setForm((f) => ({ ...f, operario: e.target.value }))
                  }
                  className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-800 bg-white focus:outline-none focus:ring-2 focus:ring-blue-500"
                >
                  <option value="">Selecciona un operario…</option>
                  {operarios.map((o) => (
                    <option key={o.id} value={o.id}>
                      {o.nombre}
                    </option>
                  ))}
                </select>
              </div>
            </div>
            {formError && (
              <p className="mt-3 text-xs text-red-600">{formError}</p>
            )}
            <div className="mt-4 flex justify-end">
              <button
                type="submit"
                disabled={saving}
                className="px-5 py-2 rounded-lg bg-blue-600 text-white text-sm font-semibold hover:bg-blue-700 disabled:opacity-50 transition"
              >
                {saving ? "Asignando…" : "Crear inspección"}
              </button>
            </div>
          </form>
        )}

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
                  Operario
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
                      {insp.operario_nombre ?? "—"}
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
                    colSpan={6}
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
