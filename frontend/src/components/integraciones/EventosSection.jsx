import { useCallback, useEffect, useState } from "react";
import api from "../../services/api";
import {
  ALERT_ERROR,
  BADGE,
  BTN_SECONDARY,
  CARD,
  INPUT,
  LABEL,
  TD,
  TH,
  formatFecha,
} from "./estilos";

// Estados de WebhookEvent (integraciones/models.py) con su color.
const ESTADO_EVENTO = {
  RECIBIDO: {
    label: "Recibido",
    cls: "bg-amber-50 text-amber-700 ring-amber-200",
  },
  PROCESADO: {
    label: "Procesado",
    cls: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  },
  IGNORADO: {
    label: "Ignorado",
    cls: "bg-slate-100 text-slate-600 ring-slate-200",
  },
  ERROR: { label: "Error", cls: "bg-red-50 text-red-700 ring-red-200" },
};

// Eventos de entrada (GET /integraciones/eventos/). El backend devuelve los
// últimos 50 sin paginación ni filtros, así que el filtro por estado es local.
export default function EventosSection() {
  const [eventos, setEventos] = useState([]);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState(null);
  const [filtro, setFiltro] = useState("");

  const cargar = useCallback(
    () =>
      api
        .get("/integraciones/eventos/")
        .then((res) => {
          setEventos(res.data);
          setError(null);
        })
        .catch(() => setError("No se pudieron cargar los eventos recibidos."))
        .finally(() => setCargando(false)),
    [],
  );

  useEffect(() => {
    cargar();
  }, [cargar]);

  const visibles = filtro
    ? eventos.filter((ev) => ev.estado === filtro)
    : eventos;

  return (
    <section aria-labelledby="titulo-eventos" className="mb-12">
      <div className="mb-4 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h2 id="titulo-eventos" className="text-lg font-semibold text-slate-900">
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
            onClick={() => {
              setCargando(true);
              cargar();
            }}
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
          <p className="px-5 py-10 text-center text-slate-400 text-sm">
            Cargando eventos…
          </p>
        ) : visibles.length === 0 ? (
          !error && (
            <p className="px-5 py-10 text-center text-slate-400 text-sm">
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
                    cls: "bg-slate-100 text-slate-600 ring-slate-200",
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
                      <td className={`${TD} text-xs text-red-600 max-w-xs break-words`}>
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
