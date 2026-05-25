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
const PROP_EMPTY = { nombre: "", direccion: "", descripcion: "" };
const OP_EMPTY = {
  username: "",
  email: "",
  first_name: "",
  last_name: "",
  password: "",
};

export default function AdminPage() {
  const [tab, setTab] = useState("inspecciones");

  // ── Inspecciones ──────────────────────────────────────────────────────────
  const [inspections, setInspections] = useState([]);
  const [propiedades, setPropiedades] = useState([]);
  const [operarios, setOperarios] = useState([]);
  const [error, setError] = useState(null);
  const [downloading, setDownloading] = useState(null);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState(FORM_EMPTY);
  const [formError, setFormError] = useState(null);
  const [saving, setSaving] = useState(false);

  // ── Propiedades ───────────────────────────────────────────────────────────
  const [showPropForm, setShowPropForm] = useState(false);
  const [propForm, setPropForm] = useState(PROP_EMPTY);
  const [propErrors, setPropErrors] = useState({});
  const [propSaving, setPropSaving] = useState(false);
  const [editingProp, setEditingProp] = useState(null); // id si estamos editando

  // ── Operarios ─────────────────────────────────────────────────────────────
  const [showOpForm, setShowOpForm] = useState(false);
  const [opForm, setOpForm] = useState(OP_EMPTY);
  const [opErrors, setOpErrors] = useState({});
  const [opSaving, setOpSaving] = useState(false);

  const loadAll = () => {
    api
      .get("/inspecciones/")
      .then((res) => setInspections(res.data))
      .catch(() => setError("No se pudieron cargar las inspecciones."));
    api.get("/propiedades/").then((res) => setPropiedades(res.data));
    api.get("/usuarios/operarios/").then((res) => setOperarios(res.data));
  };

  useEffect(() => {
    loadAll();
  }, []);

  // ── Handlers inspecciones ─────────────────────────────────────────────────
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
      loadAll();
    } catch {
      setFormError("No se pudo crear la inspección. Inténtalo de nuevo.");
    } finally {
      setSaving(false);
    }
  };

  // ── Handlers propiedades ──────────────────────────────────────────────────
  const openNewProp = () => {
    setEditingProp(null);
    setPropForm(PROP_EMPTY);
    setPropErrors({});
    setShowPropForm(true);
  };

  const openEditProp = (p) => {
    setEditingProp(p.id);
    setPropForm({
      nombre: p.nombre,
      direccion: p.direccion,
      descripcion: p.descripcion || "",
    });
    setPropErrors({});
    setShowPropForm(true);
  };

  const handlePropSubmit = async (e) => {
    e.preventDefault();
    setPropErrors({});
    setPropSaving(true);
    try {
      if (editingProp) {
        await api.put(`/propiedades/${editingProp}/`, propForm);
      } else {
        await api.post("/propiedades/", propForm);
      }
      setShowPropForm(false);
      setPropForm(PROP_EMPTY);
      setEditingProp(null);
      api.get("/propiedades/").then((res) => setPropiedades(res.data));
    } catch (err) {
      setPropErrors(
        err.response?.data || { non_field_errors: "Error al guardar." },
      );
    } finally {
      setPropSaving(false);
    }
  };

  const handleDeleteProp = async (id) => {
    if (!window.confirm("¿Eliminar esta propiedad?")) return;
    await api.delete(`/propiedades/${id}/`);
    api.get("/propiedades/").then((res) => setPropiedades(res.data));
  };

  // ── Handlers operarios ────────────────────────────────────────────────────
  const handleOpSubmit = async (e) => {
    e.preventDefault();
    setOpErrors({});
    setOpSaving(true);
    try {
      await api.post("/usuarios/operarios/", opForm);
      setShowOpForm(false);
      setOpForm(OP_EMPTY);
      api.get("/usuarios/operarios/").then((res) => setOperarios(res.data));
    } catch (err) {
      setOpErrors(
        err.response?.data || {
          non_field_errors: "Error al crear el operario.",
        },
      );
    } finally {
      setOpSaving(false);
    }
  };

  const handleDeleteOp = async (id) => {
    if (!window.confirm("¿Eliminar este operario?")) return;
    await api.delete(`/usuarios/operarios/${id}/`);
    api.get("/usuarios/operarios/").then((res) => setOperarios(res.data));
  };

  const fieldErr = (errs, field) =>
    errs[field] ? (
      <p className="mt-1 text-xs text-red-600">{errs[field]}</p>
    ) : null;

  // ── Render ─────────────────────────────────────────────────────────────────
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
        {/* Pestañas */}
        <nav className="flex gap-1 mb-8 border-b border-slate-200">
          {[
            { id: "inspecciones", label: "Inspecciones" },
            { id: "propiedades", label: "Propiedades" },
            { id: "operarios", label: "Operarios" },
          ].map((t) => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={`px-4 py-2 text-sm font-medium transition border-b-2 -mb-px ${
                tab === t.id
                  ? "border-blue-600 text-blue-600"
                  : "border-transparent text-slate-500 hover:text-slate-800"
              }`}
            >
              {t.label}
            </button>
          ))}
        </nav>

        {/* ── TAB: Inspecciones ────────────────────────────────────────────── */}
        {tab === "inspecciones" && (
          <>
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
                      <tr
                        key={insp.id}
                        className="hover:bg-slate-50 transition"
                      >
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
                              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-blue-600 text-white text-xs font-semibold hover:bg-blue-700 disabled:opacity-50 transition"
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
          </>
        )}

        {/* ── TAB: Propiedades ─────────────────────────────────────────────── */}
        {tab === "propiedades" && (
          <>
            <div className="mb-8 flex items-center justify-between">
              <div>
                <h1 className="text-2xl font-semibold text-slate-900">
                  Propiedades
                </h1>
                <p className="mt-1 text-sm text-slate-500">
                  Gestiona los inmuebles de tu cartera
                </p>
              </div>
              <button
                onClick={openNewProp}
                className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-blue-600 text-white text-sm font-semibold hover:bg-blue-700 transition"
              >
                + Nueva propiedad
              </button>
            </div>

            {showPropForm && (
              <form
                onSubmit={handlePropSubmit}
                className="mb-8 bg-white border border-slate-200 rounded-xl p-6 shadow-sm"
              >
                <h2 className="text-base font-semibold text-slate-800 mb-4">
                  {editingProp ? "Editar propiedad" : "Nueva propiedad"}
                </h2>
                <div className="space-y-4">
                  <div>
                    <label className="block text-xs font-medium text-slate-500 mb-1">
                      Nombre <span className="text-red-500">*</span>
                    </label>
                    <input
                      value={propForm.nombre}
                      onChange={(e) =>
                        setPropForm((f) => ({ ...f, nombre: e.target.value }))
                      }
                      required
                      placeholder="Piso Calle Mayor 3"
                      className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-800 focus:outline-none focus:ring-2 focus:ring-blue-500"
                    />
                    {fieldErr(propErrors, "nombre")}
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-slate-500 mb-1">
                      Dirección <span className="text-red-500">*</span>
                    </label>
                    <input
                      value={propForm.direccion}
                      onChange={(e) =>
                        setPropForm((f) => ({
                          ...f,
                          direccion: e.target.value,
                        }))
                      }
                      required
                      placeholder="Calle Mayor 3, 2ºA, 28001 Madrid"
                      className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-800 focus:outline-none focus:ring-2 focus:ring-blue-500"
                    />
                    {fieldErr(propErrors, "direccion")}
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-slate-500 mb-1">
                      Descripción
                    </label>
                    <textarea
                      value={propForm.descripcion}
                      onChange={(e) =>
                        setPropForm((f) => ({
                          ...f,
                          descripcion: e.target.value,
                        }))
                      }
                      rows={3}
                      placeholder="Notas adicionales sobre la propiedad…"
                      className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-800 focus:outline-none focus:ring-2 focus:ring-blue-500 resize-none"
                    />
                  </div>
                </div>
                {propErrors.non_field_errors && (
                  <p className="mt-2 text-xs text-red-600">
                    {propErrors.non_field_errors}
                  </p>
                )}
                <div className="mt-4 flex justify-end gap-3">
                  <button
                    type="button"
                    onClick={() => setShowPropForm(false)}
                    className="px-4 py-2 rounded-lg border border-slate-200 text-sm text-slate-600 hover:bg-slate-50 transition"
                  >
                    Cancelar
                  </button>
                  <button
                    type="submit"
                    disabled={propSaving}
                    className="px-5 py-2 rounded-lg bg-blue-600 text-white text-sm font-semibold hover:bg-blue-700 disabled:opacity-50 transition"
                  >
                    {propSaving
                      ? "Guardando…"
                      : editingProp
                        ? "Guardar cambios"
                        : "Crear propiedad"}
                  </button>
                </div>
              </form>
            )}

            <div className="bg-white rounded-xl border border-slate-200 overflow-hidden shadow-sm">
              {propiedades.length === 0 ? (
                <p className="px-5 py-10 text-center text-slate-400 text-sm">
                  Aún no tienes propiedades. Crea la primera.
                </p>
              ) : (
                <table className="min-w-full text-sm">
                  <thead>
                    <tr className="border-b border-slate-200 bg-slate-50">
                      <th className="px-5 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wide">
                        Nombre
                      </th>
                      <th className="px-5 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wide">
                        Dirección
                      </th>
                      <th className="px-5 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wide">
                        Descripción
                      </th>
                      <th className="px-5 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wide w-28">
                        Acciones
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {propiedades.map((p) => (
                      <tr key={p.id} className="hover:bg-slate-50 transition">
                        <td className="px-5 py-3.5 font-medium text-slate-800">
                          {p.nombre}
                        </td>
                        <td className="px-5 py-3.5 text-slate-500">
                          {p.direccion}
                        </td>
                        <td className="px-5 py-3.5 text-slate-400 italic">
                          {p.descripcion || "—"}
                        </td>
                        <td className="px-5 py-3.5">
                          <div className="flex gap-2">
                            <button
                              onClick={() => openEditProp(p)}
                              className="text-xs text-blue-600 hover:underline font-medium"
                            >
                              Editar
                            </button>
                            <button
                              onClick={() => handleDeleteProp(p.id)}
                              className="text-xs text-red-500 hover:underline font-medium"
                            >
                              Eliminar
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </>
        )}

        {/* ── TAB: Operarios ───────────────────────────────────────────────── */}
        {tab === "operarios" && (
          <>
            <div className="mb-8 flex items-center justify-between">
              <div>
                <h1 className="text-2xl font-semibold text-slate-900">
                  Operarios
                </h1>
                <p className="mt-1 text-sm text-slate-500">
                  Usuarios asignables a inspecciones
                </p>
              </div>
              <button
                onClick={() => {
                  setShowOpForm((v) => !v);
                  setOpErrors({});
                  setOpForm(OP_EMPTY);
                }}
                className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-blue-600 text-white text-sm font-semibold hover:bg-blue-700 transition"
              >
                {showOpForm ? "Cancelar" : "+ Nuevo operario"}
              </button>
            </div>

            {showOpForm && (
              <form
                onSubmit={handleOpSubmit}
                className="mb-8 bg-white border border-slate-200 rounded-xl p-6 shadow-sm"
              >
                <h2 className="text-base font-semibold text-slate-800 mb-4">
                  Crear cuenta de operario
                </h2>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                  <div>
                    <label className="block text-xs font-medium text-slate-500 mb-1">
                      Nombre
                    </label>
                    <input
                      value={opForm.first_name}
                      onChange={(e) =>
                        setOpForm((f) => ({ ...f, first_name: e.target.value }))
                      }
                      placeholder="Juan"
                      className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-800 focus:outline-none focus:ring-2 focus:ring-blue-500"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-slate-500 mb-1">
                      Apellidos
                    </label>
                    <input
                      value={opForm.last_name}
                      onChange={(e) =>
                        setOpForm((f) => ({ ...f, last_name: e.target.value }))
                      }
                      placeholder="Pérez"
                      className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-800 focus:outline-none focus:ring-2 focus:ring-blue-500"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-slate-500 mb-1">
                      Usuario <span className="text-red-500">*</span>
                    </label>
                    <input
                      value={opForm.username}
                      onChange={(e) =>
                        setOpForm((f) => ({ ...f, username: e.target.value }))
                      }
                      required
                      placeholder="juan_perez"
                      className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-800 focus:outline-none focus:ring-2 focus:ring-blue-500"
                    />
                    {fieldErr(opErrors, "username")}
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-slate-500 mb-1">
                      Correo electrónico
                    </label>
                    <input
                      type="email"
                      value={opForm.email}
                      onChange={(e) =>
                        setOpForm((f) => ({ ...f, email: e.target.value }))
                      }
                      placeholder="juan@empresa.com"
                      className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-800 focus:outline-none focus:ring-2 focus:ring-blue-500"
                    />
                    {fieldErr(opErrors, "email")}
                  </div>
                  <div className="sm:col-span-2">
                    <label className="block text-xs font-medium text-slate-500 mb-1">
                      Contraseña <span className="text-red-500">*</span>
                    </label>
                    <input
                      type="password"
                      value={opForm.password}
                      onChange={(e) =>
                        setOpForm((f) => ({ ...f, password: e.target.value }))
                      }
                      required
                      className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-800 focus:outline-none focus:ring-2 focus:ring-blue-500"
                    />
                    {fieldErr(opErrors, "password")}
                  </div>
                </div>
                {opErrors.non_field_errors && (
                  <p className="mt-3 text-xs text-red-600">
                    {opErrors.non_field_errors}
                  </p>
                )}
                <div className="mt-4 flex justify-end">
                  <button
                    type="submit"
                    disabled={opSaving}
                    className="px-5 py-2 rounded-lg bg-blue-600 text-white text-sm font-semibold hover:bg-blue-700 disabled:opacity-50 transition"
                  >
                    {opSaving ? "Creando…" : "Crear operario"}
                  </button>
                </div>
              </form>
            )}

            <div className="bg-white rounded-xl border border-slate-200 overflow-hidden shadow-sm">
              {operarios.length === 0 ? (
                <p className="px-5 py-10 text-center text-slate-400 text-sm">
                  Aún no hay operarios. Crea el primero.
                </p>
              ) : (
                <table className="min-w-full text-sm">
                  <thead>
                    <tr className="border-b border-slate-200 bg-slate-50">
                      <th className="px-5 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wide">
                        Nombre
                      </th>
                      <th className="px-5 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wide">
                        Usuario
                      </th>
                      <th className="px-5 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wide">
                        Correo
                      </th>
                      <th className="px-5 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wide w-24">
                        Acciones
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {operarios.map((o) => (
                      <tr key={o.id} className="hover:bg-slate-50 transition">
                        <td className="px-5 py-3.5 font-medium text-slate-800">
                          {o.nombre}
                        </td>
                        <td className="px-5 py-3.5 text-slate-500 font-mono text-xs">
                          {o.username}
                        </td>
                        <td className="px-5 py-3.5 text-slate-400">
                          {o.email || "—"}
                        </td>
                        <td className="px-5 py-3.5">
                          <button
                            onClick={() => handleDeleteOp(o.id)}
                            className="text-xs text-red-500 hover:underline font-medium"
                          >
                            Eliminar
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </>
        )}
      </main>
    </div>
  );
}
