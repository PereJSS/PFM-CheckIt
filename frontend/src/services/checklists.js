// Checklists (#19): plantillas del cliente y checklist de cada inspección.
// La regla de faltas es la misma que la del servidor (inspecciones/services/checklist.py):
// el móvil avisa antes, pero quien decide al completar es el servidor (409 con `faltas`).
import api from "./api";

export const RESULTADOS = [
  { valor: "OK", etiqueta: "OK" },
  { valor: "DANO", etiqueta: "Daño" },
  { valor: "NO_APLICA", etiqueta: "No aplica" },
];

// ── API ──────────────────────────────────────────────────────────────────────
export const listarPlantillas = () => api.get("/plantillas-checklist/").then((r) => r.data);
export const crearPlantilla = (datos) => api.post("/plantillas-checklist/", datos).then((r) => r.data);
// `datos` lleva la `version` leída: si otra persona la cambió, el servidor responde 409
export const guardarPlantilla = (id, datos) =>
  api.patch(`/plantillas-checklist/${id}/`, datos).then((r) => r.data);
export const borrarPlantilla = (id) => api.delete(`/plantillas-checklist/${id}/`);
export const duplicarPlantilla = (id) =>
  api.post(`/plantillas-checklist/${id}/duplicar/`).then((r) => r.data);

export const obtenerChecklist = (inspeccionId) =>
  api.get(`/inspecciones/${inspeccionId}/checklist/`).then((r) => r.data);
export const aplicarChecklist = (inspeccionId, datos = {}) =>
  api.post(`/inspecciones/${inspeccionId}/checklist/`, datos).then((r) => r.data);
export const marcarPunto = (inspeccionId, puntoId, datos) =>
  api.patch(`/inspecciones/${inspeccionId}/checklist/puntos/${puntoId}/`, datos).then((r) => r.data);

// ── Reglas ───────────────────────────────────────────────────────────────────
// `fotosPendientes`: { puntoId: nº de fotos guardadas en el móvil sin subir }
export function faltasLocales(checklist, fotosPendientes = {}) {
  if (!checklist?.tiene_checklist) return [];
  const faltas = [];
  for (const estancia of checklist.estancias) {
    for (const p of estancia.puntos) {
      const fotos = (p.evidencias?.length || 0) + (fotosPendientes[p.id] || 0);
      let motivo = null;
      if (p.resultado == null) motivo = "sin_marcar";
      else if (fotos === 0 && (p.resultado === "DANO" || (p.requiere_foto && p.resultado === "OK")))
        motivo = "falta_foto";
      if (motivo) faltas.push({ punto: p.id, estancia: estancia.nombre, texto: p.texto, motivo });
    }
  }
  return faltas;
}

export function progreso(estancia) {
  const total = estancia.puntos.length;
  const marcados = estancia.puntos.filter((p) => p.resultado != null).length;
  return { marcados, total };
}

export function textoFaltas(faltas) {
  const partes = faltas
    .slice(0, 5)
    .map((f) => `${f.estancia} · ${f.texto} (${f.motivo === "sin_marcar" ? "sin marcar" : "falta foto"})`);
  const resto = faltas.length > 5 ? ` y ${faltas.length - 5} más` : "";
  return partes.join("; ") + resto;
}

// Marcas guardadas en el móvil sin subir: son más recientes que lo que dijo el servidor,
// así que se pintan encima (si no, al reabrir sin red el operario las vería sin marcar)
export function superponerMarcas(checklist, marcas = []) {
  if (!checklist?.tiene_checklist || marcas.length === 0) return checklist;
  const porPunto = new Map(marcas.map((m) => [Number(m.puntoId), m]));
  return {
    ...checklist,
    estancias: checklist.estancias.map((e) => ({
      ...e,
      puntos: e.puntos.map((p) => {
        const m = porPunto.get(p.id);
        return m ? { ...p, resultado: m.resultado ?? null, nota: m.nota ?? p.nota } : p;
      }),
    })),
  };
}

