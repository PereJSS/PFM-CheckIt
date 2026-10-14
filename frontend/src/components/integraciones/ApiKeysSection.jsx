import { useCallback, useEffect, useState } from "react";
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

// Gestión de API keys (GET/POST /integraciones/api-keys/, DELETE .../<id>/ para revocar).
export default function ApiKeysSection() {
  const [claves, setClaves] = useState([]);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState(null);

  const [showForm, setShowForm] = useState(false);
  const [nombre, setNombre] = useState("");
  const [formErrors, setFormErrors] = useState({});
  const [saving, setSaving] = useState(false);
  const [revocando, setRevocando] = useState(null);

  // Clave en claro recién creada. Solo en memoria: nunca en storage.
  const [claveNueva, setClaveNueva] = useState(null);

  const cargar = useCallback(
    () =>
      api
        .get("/integraciones/api-keys/")
        .then((res) => {
          setClaves(res.data);
          setError(null);
        })
        .catch(() => setError("No se pudieron cargar las API keys."))
        .finally(() => setCargando(false)),
    [],
  );

  useEffect(() => {
    cargar();
  }, [cargar]);

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
      cargar();
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
    setRevocando(k.id);
    try {
      await api.delete(`/integraciones/api-keys/${k.id}/`);
      await cargar();
    } catch {
      setError("No se pudo revocar la API key. Inténtalo de nuevo.");
    } finally {
      setRevocando(null);
    }
  };

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
          type="button"
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

      {claveNueva && (
        <SecretoUnaVez
          titulo={`API key "${claveNueva.nombre}" creada`}
          valor={claveNueva.clave}
          onCerrar={() => setClaveNueva(null)}
        />
      )}

      {showForm && (
        <form
          onSubmit={handleSubmit}
          className="mb-6 bg-white border border-slate-200 rounded-xl p-6 shadow-sm"
        >
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
            />
            {formErrors.nombre && (
              <p className="mt-1 text-xs text-red-600">{formErrors.nombre}</p>
            )}
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
              {saving ? "Creando…" : "Crear API key"}
            </button>
          </div>
        </form>
      )}

      {error && (
        <div role="alert" className={ALERT_ERROR}>
          {error}
        </div>
      )}

      <div className={CARD} aria-busy={cargando}>
        {cargando ? (
          <p className="px-5 py-10 text-center text-slate-400 text-sm">
            Cargando API keys…
          </p>
        ) : claves.length === 0 ? (
          !error && (
            <p className="px-5 py-10 text-center text-slate-400 text-sm">
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
                      {k.revocada ? (
                        <span
                          className={`${BADGE} bg-red-50 text-red-700 ring-red-200`}
                        >
                          Revocada
                        </span>
                      ) : (
                        <span
                          className={`${BADGE} bg-emerald-50 text-emerald-700 ring-emerald-200`}
                        >
                          Activa
                        </span>
                      )}
                    </td>
                    <td className={TD}>
                      {!k.revocada && (
                        <button
                          type="button"
                          onClick={() => handleRevocar(k)}
                          disabled={revocando === k.id}
                          aria-label={`Revocar la API key ${k.nombre}`}
                          className="text-xs text-red-500 hover:underline font-medium disabled:opacity-50"
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
