import { useState } from "react";
import api from "../../services/api";
import SecretoUnaVez from "./SecretoUnaVez";
import {
  ALERT_ERROR,
  BADGE,
  BTN_PRIMARY,
  CARD,
  INPUT,
  LABEL,
  TD,
  TH,
  erroresDRF,
  formatFecha,
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

// Destinos de webhooks salientes. Los datos los carga el padre
// (WebhooksSalientesSection) porque también los usa el filtro de entregas.
export default function DestinosWebhook({ destinos, cargando, error, recargar }) {
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState(DESTINO_EMPTY);
  const [formErrors, setFormErrors] = useState({});
  const [saving, setSaving] = useState(false);
  const [accionError, setAccionError] = useState(null);
  const [ocupado, setOcupado] = useState(null);
  // Secreto de firma recién creado. Solo en memoria: nunca en storage.
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
      setSecretoNuevo({ url: data.url, secreto: data.secreto });
      setForm(DESTINO_EMPTY);
      setShowForm(false);
      recargar();
    } catch (err) {
      setFormErrors(erroresDRF(err, "No se pudo crear el destino."));
    } finally {
      setSaving(false);
    }
  };

  const handleToggleActivo = async (d) => {
    setAccionError(null);
    setOcupado(d.id);
    try {
      await api.patch(`/integraciones/destinos/${d.id}/`, {
        activo: !d.activo,
      });
      await recargar();
    } catch {
      setAccionError("No se pudo actualizar el destino. Inténtalo de nuevo.");
    } finally {
      setOcupado(null);
    }
  };

  const handleDelete = async (d) => {
    if (
      !window.confirm(
        `¿Eliminar el destino ${d.url}? También se borrará su historial de entregas.`,
      )
    )
      return;
    setAccionError(null);
    setOcupado(d.id);
    try {
      await api.delete(`/integraciones/destinos/${d.id}/`);
      await recargar();
    } catch {
      setAccionError("No se pudo eliminar el destino. Inténtalo de nuevo.");
    } finally {
      setOcupado(null);
    }
  };

  const etiquetaEvento = (valor) =>
    EVENTOS_SALIENTES.find((e) => e.valor === valor)?.label ?? valor;

  return (
    <div className="mb-8">
      <div className="mb-4 flex items-center justify-between">
        <h3 className="text-base font-semibold text-slate-800">Destinos</h3>
        <button
          type="button"
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

      {secretoNuevo && (
        <SecretoUnaVez
          titulo={`Secreto de firma del destino ${secretoNuevo.url}`}
          valor={secretoNuevo.secreto}
          onCerrar={() => setSecretoNuevo(null)}
        />
      )}

      {showForm && (
        <form
          onSubmit={handleSubmit}
          className="mb-6 bg-white border border-slate-200 rounded-xl p-6 shadow-sm"
        >
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
              />
              {formErrors.nombre && (
                <p className="mt-1 text-xs text-red-600">{formErrors.nombre}</p>
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
                onChange={(e) => setForm((f) => ({ ...f, url: e.target.value }))}
                required
                placeholder="https://erp.ejemplo.com/webhooks/checkit"
                className={INPUT}
              />
              {formErrors.url && (
                <p className="mt-1 text-xs text-red-600">{formErrors.url}</p>
              )}
            </div>
            <fieldset className="sm:col-span-2">
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
                <p className="mt-1 text-xs text-red-600">{formErrors.eventos}</p>
              )}
            </fieldset>
          </div>
          {formErrors.non_field_errors && (
            <p className="mt-3 text-xs text-red-600">
              {formErrors.non_field_errors}
            </p>
          )}
          <div className="mt-4 flex justify-end">
            <button
              type="submit"
              disabled={saving}
              className="px-5 py-2 rounded-lg bg-blue-600 text-white text-sm font-semibold hover:bg-blue-700 disabled:opacity-50 transition"
            >
              {saving ? "Creando…" : "Crear destino"}
            </button>
          </div>
        </form>
      )}

      {(error || accionError) && (
        <div role="alert" className={ALERT_ERROR}>
          {error || accionError}
        </div>
      )}

      <div className={CARD} aria-busy={cargando}>
        {cargando && destinos.length === 0 ? (
          <p className="px-5 py-10 text-center text-slate-400 text-sm">
            Cargando destinos…
          </p>
        ) : destinos.length === 0 ? (
          !error && (
            <p className="px-5 py-10 text-center text-slate-400 text-sm">
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
                  <th className={`${TH} w-40`}>Acciones</th>
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
                      {d.eventos.map(etiquetaEvento).join(", ")}
                    </td>
                    <td className={`${TD} font-mono text-xs text-slate-500`}>
                      {d.secreto_prefijo}
                    </td>
                    <td className={`${TD} text-slate-500 whitespace-nowrap`}>
                      {formatFecha(d.creado)}
                    </td>
                    <td className={TD}>
                      {d.activo ? (
                        <span
                          className={`${BADGE} bg-emerald-50 text-emerald-700 ring-emerald-200`}
                        >
                          Activo
                        </span>
                      ) : (
                        <span
                          className={`${BADGE} bg-slate-100 text-slate-600 ring-slate-200`}
                        >
                          Pausado
                        </span>
                      )}
                    </td>
                    <td className={TD}>
                      <div className="flex gap-3">
                        <button
                          type="button"
                          onClick={() => handleToggleActivo(d)}
                          disabled={ocupado === d.id}
                          aria-label={`${d.activo ? "Pausar" : "Activar"} el destino ${d.url}`}
                          className="text-xs text-blue-600 hover:underline font-medium disabled:opacity-50"
                        >
                          {d.activo ? "Pausar" : "Activar"}
                        </button>
                        <button
                          type="button"
                          onClick={() => handleDelete(d)}
                          disabled={ocupado === d.id}
                          aria-label={`Eliminar el destino ${d.url}`}
                          className="text-xs text-red-500 hover:underline font-medium disabled:opacity-50"
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
