import { useRef, useState } from "react";
import api from "../../services/api";
import SecretoUnaVez from "./SecretoUnaVez";
import {
  ALERT_ERROR,
  BADGE,
  BTN_PRIMARY,
  BTN_SUBMIT,
  CARD,
  COLOR,
  FIELD_ERROR,
  FORM_CARD,
  INPUT,
  LABEL,
  LINK_ACCION,
  LINK_PELIGRO,
  TD,
  TH,
  VACIO,
  ariaCampo,
  erroresDRF,
  formatFecha,
  mensajeError,
} from "./estilos";

// Eventos suscribibles según el contrato de webhooks salientes.
const EVENTOS_SALIENTES = [
  { valor: "inspeccion.completada", label: "Inspección completada" },
  { valor: "pdf.listo", label: "PDF listo" },
];

const DESTINO_EMPTY = {
  nombre: "",
  url: "",
  eventos: EVENTOS_SALIENTES.map((e) => e.valor),
};

const idBotonRotar = (id) => `rotar-secreto-${id}`;

// Destinos de webhooks salientes. Los datos los carga el padre
// (WebhooksSalientesSection) porque también los usa el filtro de entregas.
export default function DestinosWebhook({ destinos, cargando, error, recargar }) {
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState(DESTINO_EMPTY);
  const [formErrors, setFormErrors] = useState({});
  const [saving, setSaving] = useState(false);
  const [accionError, setAccionError] = useState(null);
  const [ocupado, setOcupado] = useState(null);
  const botonNuevoRef = useRef(null);
  // Secreto de firma recién creado o rotado. Solo en memoria: nunca en storage.
  // origen: id del destino rotado (para devolver el foco a su botón) o null si es alta.
  const [secretoNuevo, setSecretoNuevo] = useState(null);

  const toggleEvento = (valor) =>
    setForm((f) => ({
      ...f,
      eventos: f.eventos.includes(valor)
        ? f.eventos.filter((v) => v !== valor)
        : [...f.eventos, valor],
    }));

  const handleSubmit = async (e) => {
    e.preventDefault();
    setFormErrors({});
    if (form.eventos.length === 0) {
      setFormErrors({ eventos: "Selecciona al menos un evento." });
      return;
    }
    setSaving(true);
    try {
      const { data } = await api.post("/integraciones/destinos/", {
        nombre: form.nombre.trim(),
        url: form.url.trim(),
        eventos: form.eventos,
      });
      setSecretoNuevo({
        titulo: `Destino creado: secreto de firma de ${data.url}`,
        anuncio: "Destino creado",
        secreto: data.secreto,
        origen: null,
      });
      setForm(DESTINO_EMPTY);
      setShowForm(false);
      recargar();
    } catch (err) {
      // El 400 por máximo de destinos activos llega como {"detail": "..."}.
      setFormErrors(erroresDRF(err, "No se pudo crear el destino."));
    } finally {
      setSaving(false);
    }
  };

  // Ejecuta una acción sobre un destino con su estado de "ocupado" y error.
  const accion = async (d, peticion, textoError) => {
    setAccionError(null);
    setOcupado(d.id);
    try {
      const res = await peticion();
      recargar();
      return res;
    } catch (err) {
      setAccionError(mensajeError(err, textoError));
      return null;
    } finally {
      setOcupado(null);
    }
  };

  const handleToggleActivo = (d) =>
    accion(
      d,
      () =>
        api.patch(`/integraciones/destinos/${d.id}/`, { activo: !d.activo }),
      "No se pudo actualizar el destino. Inténtalo de nuevo.",
    );

  const handleRotar = async (d) => {
    if (
      !window.confirm(
        `¿Rotar el secreto de ${d.url}? El secreto actual dejará de valer al momento y tendrás que configurar el nuevo en tu sistema.`,
      )
    )
      return;
    const res = await accion(
      d,
      () => api.post(`/integraciones/destinos/${d.id}/rotar-secreto/`),
      "No se pudo rotar el secreto. Inténtalo de nuevo.",
    );
    if (res?.data?.secreto) {
      setSecretoNuevo({
        titulo: `Nuevo secreto de firma de ${d.url}`,
        anuncio: "Secreto rotado",
        secreto: res.data.secreto,
        origen: d.id,
      });
    }
  };

  const handleDelete = (d) => {
    if (
      !window.confirm(
        `¿Eliminar el destino ${d.url}? También se borrará su historial de entregas.`,
      )
    )
      return;
    accion(
      d,
      () => api.delete(`/integraciones/destinos/${d.id}/`),
      "No se pudo eliminar el destino. Inténtalo de nuevo.",
    );
  };

  const cerrarAviso = () => {
    const origen = secretoNuevo?.origen;
    setSecretoNuevo(null);
    const boton =
      origen != null
        ? document.getElementById(idBotonRotar(origen))
        : botonNuevoRef.current;
    (boton ?? botonNuevoRef.current)?.focus();
  };

  const etiquetaEvento = (valor) =>
    EVENTOS_SALIENTES.find((e) => e.valor === valor)?.label ?? valor;

  const mensajeAlerta = error || accionError;

  return (
    <div className="mb-8">
      <div className="mb-4 flex items-center justify-between">
        <h3 className="text-base font-semibold text-slate-800">Destinos</h3>
        <button
          ref={botonNuevoRef}
          type="button"
          aria-expanded={showForm}
          aria-controls="form-destino"
          onClick={() => {
            setShowForm((v) => !v);
            setFormErrors({});
            setForm(DESTINO_EMPTY);
          }}
          className={BTN_PRIMARY}
        >
          {showForm ? "Cancelar" : "+ Nuevo destino"}
        </button>
      </div>

      {/* Anuncio sin el valor del secreto; la región existe siempre para que se lea. */}
      <p role="status" className="sr-only">
        {secretoNuevo ? secretoNuevo.anuncio : ""}
      </p>

      {secretoNuevo && (
        <SecretoUnaVez
          key={secretoNuevo.secreto}
          titulo={secretoNuevo.titulo}
          valor={secretoNuevo.secreto}
          onCerrar={cerrarAviso}
        />
      )}

      {showForm && (
        <form id="form-destino" onSubmit={handleSubmit} className={FORM_CARD}>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label htmlFor="destino-nombre" className={LABEL}>
                Nombre
              </label>
              <input
                id="destino-nombre"
                value={form.nombre}
                onChange={(e) =>
                  setForm((f) => ({ ...f, nombre: e.target.value }))
                }
                maxLength={100}
                placeholder="ERP contabilidad"
                className={INPUT}
                {...ariaCampo(formErrors, "nombre", "destino-nombre-error")}
              />
              {formErrors.nombre && (
                <p id="destino-nombre-error" className={FIELD_ERROR}>
                  {formErrors.nombre}
                </p>
              )}
            </div>
            <div>
              <label htmlFor="destino-url" className={LABEL}>
                URL (https) <span className="text-red-500">*</span>
              </label>
              <input
                id="destino-url"
                type="url"
                value={form.url}
                onChange={(e) =>
                  setForm((f) => ({ ...f, url: e.target.value }))
                }
                required
                placeholder="https://erp.ejemplo.com/webhooks/checkit"
                className={INPUT}
                {...ariaCampo(formErrors, "url", "destino-url-error")}
              />
              {formErrors.url && (
                <p id="destino-url-error" className={FIELD_ERROR}>
                  {formErrors.url}
                </p>
              )}
            </div>
            <fieldset
              className="sm:col-span-2"
              {...ariaCampo(formErrors, "eventos", "destino-eventos-error")}
            >
              <legend className={LABEL}>
                Eventos <span className="text-red-500">*</span>
              </legend>
              <div className="flex flex-wrap gap-4">
                {EVENTOS_SALIENTES.map((ev) => (
                  <label
                    key={ev.valor}
                    className="inline-flex items-center gap-2 text-sm text-slate-700"
                  >
                    <input
                      type="checkbox"
                      checked={form.eventos.includes(ev.valor)}
                      onChange={() => toggleEvento(ev.valor)}
                      className="rounded border-slate-300"
                    />
                    {ev.label}{" "}
                    <span className="font-mono text-xs text-slate-400">
                      {ev.valor}
                    </span>
                  </label>
                ))}
              </div>
              {formErrors.eventos && (
                <p id="destino-eventos-error" className={FIELD_ERROR}>
                  {formErrors.eventos}
                </p>
              )}
            </fieldset>
          </div>
          {formErrors.non_field_errors && (
            <p role="alert" className="mt-3 text-xs text-red-600">
              {formErrors.non_field_errors}
            </p>
          )}
          <div className="mt-4 flex justify-end">
            <button type="submit" disabled={saving} className={BTN_SUBMIT}>
              {saving ? "Creando…" : "Crear destino"}
            </button>
          </div>
        </form>
      )}

      {mensajeAlerta && (
        <div role="alert" className={ALERT_ERROR}>
          {mensajeAlerta}
        </div>
      )}

      <div className={CARD} aria-busy={cargando}>
        {cargando && destinos.length === 0 ? (
          <p className={VACIO}>Cargando destinos…</p>
        ) : destinos.length === 0 ? (
          !error && (
            <p className={VACIO}>
              Aún no hay destinos. Añade una URL para recibir avisos de CheckIt.
            </p>
          )
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead>
                <tr className="border-b border-slate-200 bg-slate-50">
                  <th className={TH}>Destino</th>
                  <th className={TH}>Eventos</th>
                  <th className={TH}>Secreto</th>
                  <th className={TH}>Creado</th>
                  <th className={TH}>Estado</th>
                  <th className={`${TH} w-56`}>Acciones</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {destinos.map((d) => (
                  <tr key={d.id} className="hover:bg-slate-50 transition">
                    <td className={TD}>
                      <div className="font-medium text-slate-800">
                        {d.nombre || "Sin nombre"}
                      </div>
                      <div className="font-mono text-xs text-slate-500 break-all">
                        {d.url}
                      </div>
                    </td>
                    <td className={`${TD} text-xs text-slate-500`}>
                      {(d.eventos ?? []).map(etiquetaEvento).join(", ")}
                    </td>
                    {/* Texto opaco que identifica el secreto (p. ej. "whsec_…9f3a"). */}
                    <td className={`${TD} font-mono text-xs text-slate-500`}>
                      {d.secreto_prefijo || "—"}
                    </td>
                    <td className={`${TD} text-slate-500 whitespace-nowrap`}>
                      {formatFecha(d.creado)}
                    </td>
                    <td className={TD}>
                      <span
                        className={`${BADGE} ${d.activo ? COLOR.verde : COLOR.gris}`}
                      >
                        {d.activo ? "Activo" : "Pausado"}
                      </span>
                    </td>
                    <td className={TD}>
                      <div className="flex flex-wrap gap-3">
                        <button
                          type="button"
                          onClick={() => handleToggleActivo(d)}
                          disabled={ocupado === d.id}
                          aria-label={`${d.activo ? "Pausar" : "Activar"} el destino ${d.url}`}
                          className={LINK_ACCION}
                        >
                          {d.activo ? "Pausar" : "Activar"}
                        </button>
                        <button
                          id={idBotonRotar(d.id)}
                          type="button"
                          onClick={() => handleRotar(d)}
                          disabled={ocupado === d.id}
                          aria-label={`Rotar el secreto del destino ${d.url}`}
                          className={LINK_ACCION}
                        >
                          Rotar secreto
                        </button>
                        <button
                          type="button"
                          onClick={() => handleDelete(d)}
                          disabled={ocupado === d.id}
                          aria-label={`Eliminar el destino ${d.url}`}
                          className={LINK_PELIGRO}
                        >
                          Eliminar
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
