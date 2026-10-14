import { useState } from "react";
import useListado from "./useListado";
import {
  ALERT_ERROR,
  BADGE,
  BTN_SECONDARY,
  CARD,
  COLOR,
  INPUT,
  LABEL,
  TD,
  TH,
  VACIO,
  formatFecha,
} from "./estilos";

// Estados de WebhookEvent (integraciones/models.py) con su color.
const ESTADO_EVENTO = {
  RECIBIDO: { label: "Recibido", cls: COLOR.ambar },
  PROCESADO: { label: "Procesado", cls: COLOR.verde },
  IGNORADO: { label: "Ignorado", cls: COLOR.gris },
  ERROR: { label: "Error", cls: COLOR.rojo },
};

// Eventos de entrada (GET /integraciones/eventos/). El backend devuelve los
// últimos 50 sin paginación ni filtros, así que el filtro por estado es local.
export default function EventosSection() {
  const {
    datos: eventos,
    cargando,
    error,
    recargar,
  } = useListado(
    "/integraciones/eventos/",
    "",
    "No se pudieron cargar los eventos recibidos.",
  );
  const [filtro, setFiltro] = useState("");

  const visibles = filtro
    ? eventos.filter((ev) => ev.estado === filtro)
    : eventos;

  return (
    <section aria-labelledby="titulo-eventos" className="mb-12">
      <div className="mb-4 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h2
            id="titulo-eventos"
            className="text-lg font-semibold text-slate-900"
          >
            Eventos recibidos
          </h2>
          <p className="mt-1 text-sm text-slate-500">
            Últimos 50 eventos enviados por tu PMS, para depurar la integración.
          </p>
        </div>
        <div className="flex items-end gap-2">
          <div>
            <label htmlFor="eventos-filtro" className={LABEL}>
              Estado
            </label>
            <select
              id="eventos-filtro"
              value={filtro}
              onChange={(e) => setFiltro(e.target.value)}
              className={INPUT}
            >
              <option value="">Todos</option>
              {Object.entries(ESTADO_EVENTO).map(([valor, { label }]) => (
                <option key={valor} value={valor}>
                  {label}
                </option>
              ))}
            </select>
          </div>
          <button
            type="button"
            onClick={recargar}
            disabled={cargando}
            className={BTN_SECONDARY}
          >
            {cargando ? "Actualizando…" : "Actualizar"}
          </button>
        </div>
      </div>

      {error && (
        <div role="alert" className={ALERT_ERROR}>
          {error}
        </div>
      )}

      <div className={CARD} aria-busy={cargando}>
        {cargando && eventos.length === 0 ? (
          <p className={VACIO}>Cargando eventos…</p>
        ) : visibles.length === 0 ? (
          !error && (
            <p className={VACIO}>
              {filtro
                ? "No hay eventos con ese estado."
                : "Todavía no se ha recibido ningún evento."}
            </p>
          )
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead>
                <tr className="border-b border-slate-200 bg-slate-50">
                  <th className={TH}>Tipo</th>
                  <th className={TH}>Proveedor</th>
                  <th className={TH}>Event ID</th>
                  <th className={TH}>Estado</th>
                  <th className={TH}>Recibido</th>
                  <th className={TH}>Inspección</th>
                  <th className={TH}>Error</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {visibles.map((ev) => {
                  const badge = ESTADO_EVENTO[ev.estado] ?? {
                    label: ev.estado,
                    cls: COLOR.gris,
                  };
                  return (
                    <tr key={ev.id} className="hover:bg-slate-50 transition">
                      <td className={`${TD} font-mono text-xs text-slate-800`}>
                        {ev.tipo}
                      </td>
                      <td className={`${TD} text-slate-500`}>{ev.proveedor}</td>
                      <td className={`${TD} font-mono text-xs text-slate-500`}>
                        {ev.event_id}
                      </td>
                      <td className={TD}>
                        <span className={`${BADGE} ${badge.cls}`}>
                          {badge.label}
                        </span>
                      </td>
                      <td className={`${TD} text-slate-500 whitespace-nowrap`}>
                        {formatFecha(ev.recibido)}
                      </td>
                      <td className={`${TD} font-mono text-xs text-slate-400`}>
                        {ev.inspeccion ? `#${ev.inspeccion}` : "—"}
                      </td>
                      <td
                        className={`${TD} text-xs text-red-600 max-w-xs break-words`}
                      >
                        {ev.error || <span className="text-slate-400">—</span>}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </section>
  );
}
