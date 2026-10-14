// Clases y utilidades compartidas por las secciones de Integraciones.
// Replican las del panel de administración (pages/admin.jsx) para mantener el mismo aspecto.

export const TH =
  "px-5 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wide";
export const TD = "px-5 py-3.5";
export const CARD =
  "bg-white rounded-xl border border-slate-200 overflow-hidden shadow-sm";
export const INPUT =
  "w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-800 bg-white focus:outline-none focus:ring-2 focus:ring-blue-500";
export const LABEL = "block text-xs font-medium text-slate-500 mb-1";
export const BTN_PRIMARY =
  "inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-blue-600 text-white text-sm font-semibold hover:bg-blue-700 disabled:opacity-50 transition";
export const BTN_SECONDARY =
  "inline-flex items-center gap-2 px-4 py-2 rounded-lg border border-slate-200 bg-white text-sm text-slate-600 hover:bg-slate-50 disabled:opacity-50 transition";
export const ALERT_ERROR =
  "mb-6 px-4 py-3 rounded-lg bg-red-50 border border-red-200 text-sm text-red-700";
export const BADGE =
  "inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ring-1";

// Fecha y hora legibles en es-ES; "—" si el backend devuelve null.
export const formatFecha = (iso) =>
  iso
    ? new Date(iso).toLocaleString("es-ES", {
        dateStyle: "short",
        timeStyle: "short",
      })
    : "—";

// Convierte la respuesta de error de DRF en { campo: "mensaje" } como hace admin.jsx.
// DRF devuelve listas por campo; las unimos para pintarlas en una línea.
export const erroresDRF = (err, fallback) => {
  const data = err?.response?.data;
  if (!data || typeof data !== "object") return { non_field_errors: fallback };
  return Object.fromEntries(
    Object.entries(data).map(([k, v]) => [
      k === "detail" ? "non_field_errors" : k,
      Array.isArray(v) ? v.join(" ") : String(v),
    ]),
  );
};
