import { useState } from "react";
import api from "../../services/api";
import useListado from "./useListado";
import {
  ALERT_ERROR,
  BADGE,
  BTN_SECONDARY,
  CARD,
  COLOR,
  INPUT,
  LABEL,
  LINK_ACCION,
  TD,
  TH,
  VACIO,
  formatFecha,
  mensajeError,
} from "./estilos";

const ESTADO_ENTREGA = {
  PENDIENTE: { label: "Pendiente", cls: COLOR.ambar },
  ENTREGADA: { label: "Entregada", cls: COLOR.verde },
  FALLIDA: { label: "Fallida", cls: COLOR.rojo },
};

// El backend permite reintentar una entrega FALLIDA, o una PENDIENTE cuyo
// "actualizada" tenga más de 10 minutos (atascada). El resto da 409.
const PENDIENTE_ATASCADA_MS = 10 * 60 * 1000;

const reintentable = (e, ahora) =>
  e.estado === "FALLIDA" ||
  (e.estado === "PENDIENTE" &&
    ahora - Date.parse(e.actualizada ?? e.creada) > PENDIENTE_ATASCADA_MS);

// Historial de entregas (GET /integraciones/entregas/, últimas 50).
// Los filtros ?destino y ?estado se resuelven en el servidor.
export default function EntregasWebhook({ destinos }) {
  const [filtros, setFiltros] = useState({ destino: "", estado: "" });
  const query = new URLSearchParams(
    Object.entries(filtros).filter(([, v]) => v),
  ).toString();
  const {
    datos: entregas,
    cargando,
    error,
    instante,
    recargar,
  } = useListado(
    "/integraciones/entregas/",
    query,
    "No se pudieron cargar las entregas.",
  );
  const [reintentando, setReintentando] = useState(null);
  const [accionError, setAccionError] = useState(null);

  const cambiarFiltro = (campo, valor) =>
    setFiltros((f) => ({ ...f, [campo]: valor }));

  const handleReintentar = async (e) => {
    setAccionError(null);
    setReintentando(e.id);
    try {
      await api.post(`/integraciones/entregas/${e.id}/reintentar/`);
      recargar();
    } catch (err) {
      setAccionError(
        err.response?.status === 409
          ? err.response.data?.error ||
              "Esta entrega no se puede reintentar ahora."
          : mensajeError(
              err,
              "No se pudo reintentar la entrega. Inténtalo de nuevo.",
            ),
      );
      // El estado pudo cambiar en el servidor (p. ej. ya entregada): refrescamos.
      recargar();
    } finally {
      setReintentando(null);
    }
  };

  const mensajeAlerta = error || accionError;

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
            onClick={recargar}
            disabled={cargando}
            className={BTN_SECONDARY}
          >
            {cargando ? "Actualizando…" : "Actualizar"}
          </button>
        </div>
      </div>

      {mensajeAlerta && (
        <div role="alert" className={ALERT_ERROR}>
          {mensajeAlerta}
        </div>
      )}

      <div className={CARD} aria-busy={cargando}>
        {cargando && entregas.length === 0 ? (
          <p className={VACIO}>Cargando entregas…</p>
        ) : entregas.length === 0 ? (
          !error && (
            <p className={VACIO}>
              {query
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
                    cls: COLOR.gris,
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
                        {reintentable(e, instante) && (
                          <button
                            type="button"
                            onClick={() => handleReintentar(e)}
                            disabled={reintentando === e.id}
                            aria-label={`Reintentar la entrega ${e.entrega_id}`}
                            className={LINK_ACCION}
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
