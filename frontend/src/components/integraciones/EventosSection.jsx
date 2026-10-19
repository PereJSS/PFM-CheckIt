import { Fragment, useState } from "react";
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
  PRE,
  TD,
  TH,
  VACIO,
  formatFecha,
  mensajeError,
} from "./estilos";

// Estados de WebhookEvent (integraciones/models.py) con su color.
const ESTADO_EVENTO = {
  RECIBIDO: { label: "Recibido", cls: COLOR.ambar },
  PROCESADO: { label: "Procesado", cls: COLOR.verde },
  IGNORADO: { label: "Ignorado", cls: COLOR.gris },
  ERROR: { label: "Error", cls: COLOR.rojo },
};

// Tamaño de página del backend (EVENTOS_POR_PAGINA en integraciones/views.py).
const POR_PAGINA = 50;

// El backend solo deja reprocesar estos estados; el resto da 409.
const reprocesable = (ev) => ev.estado === "ERROR" || ev.estado === "RECIBIDO";

const FILTROS_VACIOS = { estado: "", proveedor: "", event_id: "" };

const queryDe = (filtros) =>
  new URLSearchParams(
    Object.entries(filtros).filter(([, v]) => v.trim()),
  ).toString();

// Eventos de entrada (GET /integraciones/eventos/). Los filtros se resuelven en
// el servidor; "Cargar más" pide la página siguiente con ?antes=<último id>.
// Cada fila se despliega para ver el payload y la API key (GET .../<id>/).
export default function EventosSection() {
  const [filtros, setFiltros] = useState(FILTROS_VACIOS);
  // Texto de proveedor/event_id mientras se escribe; se aplica al enviar.
  const [borrador, setBorrador] = useState(FILTROS_VACIOS);
  const query = queryDe(filtros);
  const {
    datos: primera,
    cargando,
    error,
    instante,
    recargar,
  } = useListado(
    "/integraciones/eventos/",
    query,
    "No se pudieron cargar los eventos recibidos.",
  );

  // Páginas extra ligadas a la primera página que las originó: si cambian los
  // filtros o se recarga, `instante` cambia y se descartan sin efectos.
  const clavePagina = `${query}#${instante}`;
  const [extra, setExtra] = useState({ clave: null, items: [], agotado: false });
  const extraVigente =
    extra.clave === clavePagina ? extra : { items: [], agotado: false };
  const eventos = [...primera, ...extraVigente.items];
  const [cargandoMas, setCargandoMas] = useState(false);

  const [abierto, setAbierto] = useState(null);
  const [detalles, setDetalles] = useState({});
  const [reprocesando, setReprocesando] = useState(null);
  const [accionError, setAccionError] = useState(null);
  const [aviso, setAviso] = useState(null);

  const hayMas =
    !extraVigente.agotado &&
    primera.length === POR_PAGINA &&
    eventos.length % POR_PAGINA === 0;

  const cambiarEstado = (estado) => {
    setFiltros((f) => ({ ...f, estado }));
    setBorrador((b) => ({ ...b, estado }));
  };

  const aplicarFiltros = (e) => {
    e.preventDefault();
    setFiltros(borrador);
  };

  const limpiarFiltros = () => {
    setFiltros(FILTROS_VACIOS);
    setBorrador(FILTROS_VACIOS);
  };

  const cargarMas = async () => {
    const ultimo = eventos[eventos.length - 1];
    if (!ultimo) return;
    setAccionError(null);
    setCargandoMas(true);
    try {
      const params = new URLSearchParams(query);
      params.set("antes", ultimo.id);
      const { data } = await api.get("/integraciones/eventos/", { params });
      setExtra({
        clave: clavePagina,
        items: [...extraVigente.items, ...data],
        agotado: data.length < POR_PAGINA,
      });
    } catch (err) {
      setAccionError(mensajeError(err, "No se pudieron cargar más eventos."));
    } finally {
      setCargandoMas(false);
    }
  };

  const alternarDetalle = async (ev) => {
    if (abierto === ev.id) {
      setAbierto(null);
      return;
    }
    setAbierto(ev.id);
    if (detalles[ev.id]) return;
    try {
      const { data } = await api.get(`/integraciones/eventos/${ev.id}/`);
      setDetalles((d) => ({ ...d, [ev.id]: { datos: data } }));
    } catch (err) {
      setDetalles((d) => ({
        ...d,
        [ev.id]: {
          error: mensajeError(err, "No se pudo cargar el detalle del evento."),
        },
      }));
    }
  };

  const handleReprocesar = async (ev) => {
    setAccionError(null);
    setAviso(null);
    setReprocesando(ev.id);
    try {
      await api.post(`/integraciones/eventos/${ev.id}/reprocesar/`);
      setAviso(`Evento ${ev.event_id} reencolado para procesar.`);
    } catch (err) {
      setAccionError(
        err.response?.status === 409
          ? err.response.data?.error ||
              "Este evento ya está resuelto y no se puede reprocesar."
          : mensajeError(
              err,
              "No se pudo reprocesar el evento. Inténtalo de nuevo.",
            ),
      );
    } finally {
      setReprocesando(null);
      // El estado cambió (o ya había cambiado) en el servidor: refrescamos.
      setDetalles((d) => {
        const resto = { ...d };
        delete resto[ev.id];
        return resto;
      });
      recargar();
    }
  };

  const mensajeAlerta = error || accionError;

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
            Eventos enviados por tu PMS, del más reciente al más antiguo, para
            depurar la integración.
          </p>
        </div>
        <form
          onSubmit={aplicarFiltros}
          className="flex flex-wrap items-end gap-2"
        >
          <div>
            <label htmlFor="eventos-filtro" className={LABEL}>
              Estado
            </label>
            <select
              id="eventos-filtro"
              value={filtros.estado}
              onChange={(e) => cambiarEstado(e.target.value)}
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
          <div>
            <label htmlFor="eventos-proveedor" className={LABEL}>
              Proveedor
            </label>
            <input
              id="eventos-proveedor"
              value={borrador.proveedor}
              onChange={(e) =>
                setBorrador((b) => ({ ...b, proveedor: e.target.value }))
              }
              placeholder="guesty"
              className={INPUT}
            />
          </div>
          <div>
            <label htmlFor="eventos-event-id" className={LABEL}>
              Event ID
            </label>
            <input
              id="eventos-event-id"
              value={borrador.event_id}
              onChange={(e) =>
                setBorrador((b) => ({ ...b, event_id: e.target.value }))
              }
              placeholder="evt_123"
              className={INPUT}
            />
          </div>
          <button type="submit" className={BTN_SECONDARY}>
            Filtrar
          </button>
          {query && (
            <button
              type="button"
              onClick={limpiarFiltros}
              className={BTN_SECONDARY}
            >
              Limpiar
            </button>
          )}
          <button
            type="button"
            onClick={recargar}
            disabled={cargando}
            className={BTN_SECONDARY}
          >
            {cargando ? "Actualizando…" : "Actualizar"}
          </button>
        </form>
      </div>

      <p role="status" className="sr-only">
        {aviso ?? ""}
      </p>
      {aviso && (
        <div className="mb-6 px-4 py-3 rounded-lg bg-emerald-50 border border-emerald-200 text-sm text-emerald-700">
          {aviso}
        </div>
      )}

      {mensajeAlerta && (
        <div role="alert" className={ALERT_ERROR}>
          {mensajeAlerta}
        </div>
      )}

      <div className={CARD} aria-busy={cargando}>
        {cargando && eventos.length === 0 ? (
          <p className={VACIO}>Cargando eventos…</p>
        ) : eventos.length === 0 ? (
          !error && (
            <p className={VACIO}>
              {query
                ? "No hay eventos con esos filtros."
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
                  <th className={TH}>Acciones</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {eventos.map((ev) => {
                  const badge = ESTADO_EVENTO[ev.estado] ?? {
                    label: ev.estado,
                    cls: COLOR.gris,
                  };
                  const detalle = detalles[ev.id];
                  return (
                    <Fragment key={ev.id}>
                      <tr className="hover:bg-slate-50 transition">
                        <td
                          className={`${TD} font-mono text-xs text-slate-800`}
                        >
                          {ev.tipo}
                        </td>
                        <td className={`${TD} text-slate-500`}>
                          {ev.proveedor}
                        </td>
                        <td
                          className={`${TD} font-mono text-xs text-slate-500`}
                        >
                          {ev.event_id}
                        </td>
                        <td className={TD}>
                          <span className={`${BADGE} ${badge.cls}`}>
                            {badge.label}
                          </span>
                        </td>
                        <td
                          className={`${TD} text-slate-500 whitespace-nowrap`}
                        >
                          {formatFecha(ev.recibido)}
                        </td>
                        <td
                          className={`${TD} font-mono text-xs text-slate-400`}
                        >
                          {ev.inspeccion ? `#${ev.inspeccion}` : "—"}
                        </td>
                        <td
                          className={`${TD} text-xs text-red-600 max-w-xs break-words`}
                        >
                          {ev.error || (
                            <span className="text-slate-400">—</span>
                          )}
                        </td>
                        <td className={`${TD} whitespace-nowrap`}>
                          <div className="flex gap-3">
                            <button
                              type="button"
                              onClick={() => alternarDetalle(ev)}
                              aria-expanded={abierto === ev.id}
                              aria-controls={`evento-detalle-${ev.id}`}
                              className={LINK_ACCION}
                            >
                              {abierto === ev.id ? "Ocultar" : "Detalle"}
                            </button>
                            {reprocesable(ev) && (
                              <button
                                type="button"
                                onClick={() => handleReprocesar(ev)}
                                disabled={reprocesando === ev.id}
                                aria-label={`Reprocesar el evento ${ev.event_id}`}
                                className={LINK_ACCION}
                              >
                                {reprocesando === ev.id
                                  ? "Reprocesando…"
                                  : "Reprocesar"}
                              </button>
                            )}
                          </div>
                        </td>
                      </tr>
                      {abierto === ev.id && (
                        <tr id={`evento-detalle-${ev.id}`}>
                          <td colSpan={8} className={`${TD} bg-slate-50`}>
                            {!detalle ? (
                              <p className="text-xs text-slate-400">
                                Cargando detalle…
                              </p>
                            ) : detalle.error ? (
                              <p className="text-xs text-red-600">
                                {detalle.error}
                              </p>
                            ) : (
                              <div className="text-xs text-slate-600">
                                <p>
                                  API key:{" "}
                                  {detalle.datos.api_key ? (
                                    <span className="font-mono">
                                      {detalle.datos.api_key.nombre} (ck_
                                      {detalle.datos.api_key.prefijo}_…)
                                    </span>
                                  ) : (
                                    <span className="text-slate-400">
                                      eliminada
                                    </span>
                                  )}
                                  {detalle.datos.procesado && (
                                    <>
                                      {" · "}Procesado:{" "}
                                      {formatFecha(detalle.datos.procesado)}
                                    </>
                                  )}
                                </p>
                                <pre className={PRE}>
                                  {JSON.stringify(
                                    detalle.datos.payload,
                                    null,
                                    2,
                                  )}
                                </pre>
                              </div>
                            )}
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {hayMas && (
        <div className="mt-4 flex justify-center">
          <button
            type="button"
            onClick={cargarMas}
            disabled={cargandoMas || cargando}
            className={BTN_SECONDARY}
          >
            {cargandoMas ? "Cargando…" : "Cargar más"}
          </button>
        </div>
      )}
    </section>
  );
}
