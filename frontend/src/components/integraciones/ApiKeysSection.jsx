import { useRef, useState } from "react";
import api from "../../services/api";
import SecretoUnaVez from "./SecretoUnaVez";
import useListado from "./useListado";
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
  LINK_PELIGRO,
  TD,
  TH,
  VACIO,
  ariaCampo,
  erroresDRF,
  formatFecha,
  mensajeError,
} from "./estilos";

// Gestión de API keys (GET/POST /integraciones/api-keys/, DELETE .../<id>/ para revocar).
export default function ApiKeysSection() {
  const {
    datos: claves,
    cargando,
    error,
    recargar,
  } = useListado(
    "/integraciones/api-keys/",
    "",
    "No se pudieron cargar las API keys.",
  );

  const [showForm, setShowForm] = useState(false);
  const [nombre, setNombre] = useState("");
  const [formErrors, setFormErrors] = useState({});
  const [saving, setSaving] = useState(false);
  const [revocando, setRevocando] = useState(null);
  const [accionError, setAccionError] = useState(null);
  const botonNuevaRef = useRef(null);

  // Clave en claro recién creada. Solo en memoria: nunca en storage.
  const [claveNueva, setClaveNueva] = useState(null);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setFormErrors({});
    setSaving(true);
    try {
      const { data } = await api.post("/integraciones/api-keys/", {
        nombre: nombre.trim(),
      });
      setClaveNueva({ nombre: data.nombre, clave: data.clave });
      setNombre("");
      setShowForm(false);
      recargar();
    } catch (err) {
      setFormErrors(erroresDRF(err, "No se pudo crear la API key."));
    } finally {
      setSaving(false);
    }
  };

  const handleRevocar = async (k) => {
    if (
      !window.confirm(
        `¿Revocar la API key "${k.nombre}"? Los sistemas que la usen dejarán de poder enviar eventos. Esta acción no se puede deshacer.`,
      )
    )
      return;
    setAccionError(null);
    setRevocando(k.id);
    try {
      await api.delete(`/integraciones/api-keys/${k.id}/`);
      recargar();
    } catch (err) {
      setAccionError(
        mensajeError(err, "No se pudo revocar la API key. Inténtalo de nuevo."),
      );
    } finally {
      setRevocando(null);
    }
  };

  const cerrarAviso = () => {
    setClaveNueva(null);
    botonNuevaRef.current?.focus();
  };

  const mensajeAlerta = error || accionError;

  return (
    <section aria-labelledby="titulo-api-keys" className="mb-12">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h2
            id="titulo-api-keys"
            className="text-lg font-semibold text-slate-900"
          >
            API keys
          </h2>
          <p className="mt-1 text-sm text-slate-500">
            Claves con las que tu PMS se autentica al enviar eventos a CheckIt.
          </p>
        </div>
        <button
          ref={botonNuevaRef}
          type="button"
          aria-expanded={showForm}
          aria-controls="form-api-key"
          onClick={() => {
            setShowForm((v) => !v);
            setFormErrors({});
            setNombre("");
          }}
          className={BTN_PRIMARY}
        >
          {showForm ? "Cancelar" : "+ Nueva API key"}
        </button>
      </div>

      {/* Anuncio sin el valor del secreto; la región existe siempre para que se lea. */}
      <p role="status" className="sr-only">
        {claveNueva ? "API key creada" : ""}
      </p>

      {claveNueva && (
        <SecretoUnaVez
          key={claveNueva.clave}
          titulo={`API key "${claveNueva.nombre}" creada`}
          valor={claveNueva.clave}
          onCerrar={cerrarAviso}
        />
      )}

      {showForm && (
        <form id="form-api-key" onSubmit={handleSubmit} className={FORM_CARD}>
          <h3 className="text-base font-semibold text-slate-800 mb-4">
            Crear API key
          </h3>
          <div>
            <label htmlFor="api-key-nombre" className={LABEL}>
              Nombre descriptivo <span className="text-red-500">*</span>
            </label>
            <input
              id="api-key-nombre"
              value={nombre}
              onChange={(e) => setNombre(e.target.value)}
              required
              maxLength={100}
              placeholder="Guesty producción"
              className={INPUT}
              {...ariaCampo(formErrors, "nombre", "api-key-nombre-error")}
            />
            {formErrors.nombre && (
              <p id="api-key-nombre-error" className={FIELD_ERROR}>
                {formErrors.nombre}
              </p>
            )}
          </div>
          {formErrors.non_field_errors && (
            <p role="alert" className="mt-3 text-xs text-red-600">
              {formErrors.non_field_errors}
            </p>
          )}
          <div className="mt-4 flex justify-end">
            <button type="submit" disabled={saving} className={BTN_SUBMIT}>
              {saving ? "Creando…" : "Crear API key"}
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
        {cargando && claves.length === 0 ? (
          <p className={VACIO}>Cargando API keys…</p>
        ) : claves.length === 0 ? (
          !error && (
            <p className={VACIO}>
              Aún no tienes API keys. Crea una para conectar tu PMS.
            </p>
          )
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead>
                <tr className="border-b border-slate-200 bg-slate-50">
                  <th className={TH}>Nombre</th>
                  <th className={TH}>Prefijo</th>
                  <th className={TH}>Creada</th>
                  <th className={TH}>Último uso</th>
                  <th className={TH}>Estado</th>
                  <th className={`${TH} w-28`}>Acciones</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {claves.map((k) => (
                  <tr key={k.id} className="hover:bg-slate-50 transition">
                    <td className={`${TD} font-medium text-slate-800`}>
                      {k.nombre}
                    </td>
                    <td className={`${TD} font-mono text-xs text-slate-500`}>
                      ck_{k.prefijo}_…
                    </td>
                    <td className={`${TD} text-slate-500`}>
                      {formatFecha(k.creada)}
                    </td>
                    <td className={`${TD} text-slate-500`}>
                      {k.ultimo_uso ? formatFecha(k.ultimo_uso) : "Nunca"}
                    </td>
                    <td className={TD}>
                      <span
                        className={`${BADGE} ${k.revocada ? COLOR.rojo : COLOR.verde}`}
                      >
                        {k.revocada ? "Revocada" : "Activa"}
                      </span>
                    </td>
                    <td className={TD}>
                      {!k.revocada && (
                        <button
                          type="button"
                          onClick={() => handleRevocar(k)}
                          disabled={revocando === k.id}
                          aria-label={`Revocar la API key ${k.nombre}`}
                          className={LINK_PELIGRO}
                        >
                          {revocando === k.id ? "Revocando…" : "Revocar"}
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </section>
  );
}
