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

const ESTADO_ENTREGA = {
  PENDIENTE: {
    label: "Pendiente",
    cls: "bg-amber-50 text-amber-700 ring-amber-200",
  },
  ENTREGADA: {
    label: "Entregada",
    cls: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  },
  FALLIDA: { label: "Fallida", cls: "bg-red-50 text-red-700 ring-red-200" },
};

// Historial de entregas (GET /integraciones/entregas/, últimas 50).
// Los filtros ?destino y ?estado se resuelven en el servidor.
export default function EntregasWebhook({ destinos }) {
  const [entregas, setEntregas] = useState([]);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState(null);
  const [filtros, setFiltros] = useState({ destino: "", estado: "" });
  const [reintentando, setReintentando] = useState(null);

  const cargar = useCallback(
    (f) => {
      const params = {};
      if (f.destino) params.destino = f.destino;
      if (f.estado) params.estado = f.estado;
      return api
        .get("/integraciones/entregas/", { params })
        .then((res) => {
          setEntregas(res.data);
          setError(null);
        })
        .catch(() => setError("No se pudieron cargar las entregas."))
        .finally(() => setCargando(false));
    },
    [],
  );

  useEffect(() => {
    cargar(filtros);
  }, [cargar, filtros]);

  const cambiarFiltro = (campo, valor) => {
    setCargando(true);
    setFiltros((f) => ({ ...f, [campo]: valor }));
  };

  const handleReintentar = async (e) => {
    setReintentando(e.id);
    try {
      await api.post(`/integraciones/entregas/${e.id}/reintentar/`);
      await cargar(filtros);
    } catch (err) {
      setError(
        err.response?.status === 409
          ? err.response.data?.error ||
              "Solo se pueden reintentar entregas fallidas."
          : "No se pudo reintentar la entrega. Inténtalo de nuevo.",
      );
    } finally {
      setReintentando(null);
    }
  };

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-end justify-between gap-4">
        <h3 className="text-base font-semibold text-slate-800">Entregas</h3>
        <div className="flex flex-wrap items-end gap-2">
          <div>
            <label htmlFor="entregas-destino" className={LABEL}>
              Destino
            </label>
            <select
              id="entregas-destino"
              value={filtros.destino}
              onChange={(e) => cambiarFiltro("destino", e.target.value)}
              className={INPUT}
            >
              <option value="">Todos</option>
              {destinos.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.nombre || d.url}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label htmlFor="entregas-estado" className={LABEL}>
              Estado
            </label>
            <select
              id="entregas-estado"
              value={filtros.estado}
              onChange={(e) => cambiarFiltro("estado", e.target.value)}
              className={INPUT}
            >
              <option value="">Todos</option>
              {Object.entries(ESTADO_ENTREGA).map(([valor, { label }]) => (
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
              cargar(filtros);
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
        {cargando && entregas.length === 0 ? (
          <p className="px-5 py-10 text-center text-slate-400 text-sm">
            Cargando entregas…
          </p>
        ) : entregas.length === 0 ? (
          !error && (
            <p className="px-5 py-10 text-center text-slate-400 text-sm">
              {filtros.destino || filtros.estado
                ? "No hay entregas con esos filtros."
                : "Todavía no se ha enviado ningún webhook."}
            </p>
          )
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead>
                <tr className="border-b border-slate-200 bg-slate-50">
                  <th className={TH}>Evento</th>
                  <th className={TH}>Destino</th>
                  <th className={TH}>Estado</th>
                  <th className={TH}>Intentos</th>
                  <th className={TH}>HTTP</th>
                  <th className={TH}>Creada</th>
                  <th className={TH}>Error</th>
                  <th className={TH}>Acciones</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {entregas.map((e) => {
                  const badge = ESTADO_ENTREGA[e.estado] ?? {
                    label: e.estado,
                    cls: "bg-slate-100 text-slate-600 ring-slate-200",
                  };
                  return (
                    <tr key={e.id} className="hover:bg-slate-50 transition">
                      <td className={TD}>
                        <div className="font-mono text-xs text-slate-800">
                          {e.evento}
                        </div>
                        <div className="text-xs text-slate-400">
                          {e.inspeccion ? `Inspección #${e.inspeccion}` : "—"}
                        </div>
                      </td>
                      <td
                        className={`${TD} font-mono text-xs text-slate-500 break-all`}
                      >
                        {e.destino_url}
                      </td>
                      <td className={TD}>
                        <span className={`${BADGE} ${badge.cls}`}>
                          {badge.label}
                        </span>
                      </td>
                      <td className={`${TD} text-slate-500`}>{e.intentos}</td>
                      <td className={`${TD} font-mono text-xs text-slate-500`}>
                        {e.ultimo_codigo ?? "—"}
                      </td>
                      <td className={`${TD} text-slate-500 whitespace-nowrap`}>
                        {formatFecha(e.creada)}
                      </td>
                      <td
                        className={`${TD} text-xs text-red-600 max-w-xs break-words`}
                      >
                        {e.error || <span className="text-slate-400">—</span>}
                      </td>
                      <td className={TD}>
                        {e.estado === "FALLIDA" && (
                          <button
                            type="button"
                            onClick={() => handleReintentar(e)}
                            disabled={reintentando === e.id}
                            aria-label={`Reintentar la entrega ${e.entrega_id}`}
                            className="text-xs text-blue-600 hover:underline font-medium disabled:opacity-50"
                          >
                            {reintentando === e.id
                              ? "Reintentando…"
                              : "Reintentar"}
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
