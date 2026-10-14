// Clases y utilidades compartidas por las secciones de Integraciones.
// Replican las del panel de administración (pages/admin.jsx) para mantener el mismo aspecto.

export const TH =
  "px-5 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wide";
export const TD = "px-5 py-3.5";
export const CARD =
  "bg-white rounded-xl border border-slate-200 overflow-hidden shadow-sm";
export const FORM_CARD =
  "mb-6 bg-white border border-slate-200 rounded-xl p-6 shadow-sm";
export const INPUT =
  "w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-800 bg-white focus:outline-none focus:ring-2 focus:ring-blue-500";
export const LABEL = "block text-xs font-medium text-slate-500 mb-1";
export const BTN_PRIMARY =
  "inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-blue-600 text-white text-sm font-semibold hover:bg-blue-700 disabled:opacity-50 transition";
export const BTN_SUBMIT =
  "px-5 py-2 rounded-lg bg-blue-600 text-white text-sm font-semibold hover:bg-blue-700 disabled:opacity-50 transition";
export const BTN_SECONDARY =
  "inline-flex items-center gap-2 px-4 py-2 rounded-lg border border-slate-200 bg-white text-sm text-slate-600 hover:bg-slate-50 disabled:opacity-50 transition";
export const LINK_ACCION =
  "text-xs text-blue-600 hover:underline font-medium disabled:opacity-50";
export const LINK_PELIGRO =
  "text-xs text-red-500 hover:underline font-medium disabled:opacity-50";
export const ALERT_ERROR =
  "mb-6 px-4 py-3 rounded-lg bg-red-50 border border-red-200 text-sm text-red-700";
export const FIELD_ERROR = "mt-1 text-xs text-red-600";
export const VACIO = "px-5 py-10 text-center text-slate-400 text-sm";
export const CODE =
  "font-mono text-xs bg-slate-100 text-slate-700 px-1.5 py-0.5 rounded";
export const PRE =
  "mt-2 overflow-x-auto rounded-lg bg-slate-900 p-4 font-mono text-xs leading-relaxed text-slate-100";

// Insignias de estado (mismo patrón que ESTADO en admin.jsx).
export const BADGE =
  "inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ring-1";
export const COLOR = {
  verde: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  ambar: "bg-amber-50 text-amber-700 ring-amber-200",
  gris: "bg-slate-100 text-slate-600 ring-slate-200",
  rojo: "bg-red-50 text-red-700 ring-red-200",
};

// Fecha y hora legibles en es-ES; "—" si el backend devuelve null.
export const formatFecha = (iso) =>
  iso
    ? new Date(iso).toLocaleString("es-ES", {
        dateStyle: "short",
        timeStyle: "short",
      })
    : "—";

// Mensaje de error para el usuario. Un 429 ya trae texto claro desde api.js;
// un 400 con "detail" (p. ej. máximo de destinos activos) se muestra tal cual.
export const mensajeError = (err, fallback) => {
  const status = err?.response?.status;
  if (status === 429) return err.message;
  const detail = err?.response?.data?.detail;
  if (status === 400 && typeof detail === "string") return detail;
  return fallback;
};

// Convierte la respuesta de error de DRF en { campo: "mensaje" } como hace admin.jsx.
// DRF devuelve listas por campo; las unimos para pintarlas en una línea.
// "detail" se pinta como error general del formulario.
export const erroresDRF = (err, fallback) => {
  const data = err?.response?.data;
  if (err?.response?.status === 429 || !data || typeof data !== "object") {
    return { non_field_errors: mensajeError(err, fallback) };
  }
  return Object.fromEntries(
    Object.entries(data).map(([k, v]) => [
      k === "detail" ? "non_field_errors" : k,
      Array.isArray(v) ? v.join(" ") : String(v),
    ]),
  );
};

// Props de accesibilidad para un campo con posible error: aria-invalid y
// aria-describedby apuntando al <p id={idError}> que lo explica.
export const ariaCampo = (errores, campo, idError, ...otrosIds) => {
  const ids = [...otrosIds, errores[campo] ? idError : null].filter(Boolean);
  return {
    "aria-invalid": errores[campo] ? true : undefined,
    "aria-describedby": ids.length ? ids.join(" ") : undefined,
  };
};
