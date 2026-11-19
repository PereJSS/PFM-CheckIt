import { useCallback, useEffect, useState } from "react";
import {
  borrarPlantilla,
  duplicarPlantilla,
  guardarPlantilla,
  listarPlantillas,
} from "../../services/checklists";
import { motivoDelRechazo } from "../../services/erroresSubida";
import EditorPlantilla from "./EditorPlantilla";
import { MODELOS } from "./modelosPlantilla";

const BASE_ACCION =
  "min-h-9 px-2 rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-600";
const ACCION = `${BASE_ACCION} text-blue-700 hover:bg-blue-50`;
const BORRAR = `${BASE_ACCION} text-red-700 hover:bg-red-50`;
const plural = (n, uno, varios) => `${n} ${n === 1 ? uno : varios}`;
const totalPuntos = (p) => p.estructura.estancias.reduce((n, e) => n + e.puntos.length, 0);
const copia = (estructura) => JSON.parse(JSON.stringify(estructura));

// Plantillas de checklist del cliente (#19). Cada inspección guarda una copia al crearse:
// editar o borrar una plantilla no cambia las inspecciones ya creadas.
export default function PlantillasPanel({ onCambio }) {
  const [plantillas, setPlantillas] = useState([]);
  const [error, setError] = useState(null);
  const [eligiendoModelo, setEligiendoModelo] = useState(false);
  // null: listado; objeto: editor (con id si es una existente)
  const [editando, setEditando] = useState(null);
  // Aviso para el editor abierto (p. ej. no se pudo recargar) y confirmación de acciones
  const [avisoEditor, setAvisoEditor] = useState(null);
  const [hecho, setHecho] = useState(null);

  const cargar = useCallback(
    () =>
      listarPlantillas()
        .then(setPlantillas)
        .catch((e) => {
          console.error("No se pudieron cargar las plantillas", e);
          setError("No se pudieron cargar las plantillas.");
        }),
    [],
  );

  useEffect(() => {
    cargar();
  }, [cargar]);

  const tras = () => {
    cargar();
    onCambio?.();
  };

  const ejecutar = (accion, mensaje) => {
    setError(null);
    setHecho(null);
    accion()
      .then(() => {
        setHecho(mensaje);
        tras();
      })
      .catch((e) => {
        console.error("Falló la acción sobre la plantilla", e);
        const status = e?.response?.status;
        setError(
          status && status < 500
            ? motivoDelRechazo(e, "la acción")
            : "No se pudo completar la acción. Inténtalo de nuevo.",
        );
      });
  };

  const borrar = (p) => {
    const uso = p.propiedades_count
      ? ` La usan ${plural(p.propiedades_count, "propiedad", "propiedades")}, que pasarán a usar la plantilla por defecto.`
      : "";
    if (!window.confirm(`¿Borrar la plantilla «${p.nombre}»?${uso} Las inspecciones ya creadas conservan su checklist.`)) return;
    ejecutar(() => borrarPlantilla(p.id), `Plantilla «${p.nombre}» borrada.`);
  };

  const recargarYEditar = (id) =>
    listarPlantillas()
      .then((lista) => {
        setPlantillas(lista);
        const fresca = lista.find((p) => p.id === id);
        setAvisoEditor(null);
        if (fresca) {
          setEditando({ ...fresca });
        } else {
          setEditando(null);
          setError("La plantilla ya no existe: la ha borrado otra persona.");
        }
      })
      .catch((e) => {
        console.error("No se pudo recargar la plantilla", e);
        setAvisoEditor("No se pudo recargar la plantilla. Inténtalo de nuevo.");
      });

  if (editando) {
    return (
      <EditorPlantilla
        key={`${editando.id ?? "nueva"}-${editando.version ?? 0}`}
        plantilla={editando}
        onGuardada={() => {
          setEditando(null);
          setAvisoEditor(null);
          setHecho("Plantilla guardada.");
          tras();
        }}
        onCancelar={() => {
          setEditando(null);
          setAvisoEditor(null);
        }}
        onRecargar={recargarYEditar}
        avisoExterno={avisoEditor}
      />
    );
  }

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-slate-500 max-w-2xl">
          Cada inspección copia la plantilla de su propiedad (o la de por defecto) al crearse. El operario no puede
          finalizar sin marcar todos los puntos, y un daño necesita foto.
        </p>
        <button
          type="button"
          aria-expanded={eligiendoModelo}
          aria-controls="modelos-plantilla"
          onClick={() => setEligiendoModelo((v) => !v)}
          className="px-4 py-2 rounded-lg bg-blue-600 text-white text-sm font-semibold hover:bg-blue-700"
        >
          Nueva plantilla
        </button>
      </div>

      {eligiendoModelo && (
        <div id="modelos-plantilla" className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {MODELOS.map((m) => (
            <button
              key={m.id}
              type="button"
              onClick={() => {
                setEligiendoModelo(false);
                setEditando({
                  nombre: m.id === "vacia" ? "" : m.nombre,
                  descripcion: "",
                  por_defecto: plantillas.length === 0,
                  estructura: copia(m.estructura),
                });
              }}
              className="text-left p-4 rounded-lg border border-slate-200 hover:border-blue-400 hover:bg-blue-50"
            >
              <span className="block text-sm font-semibold text-slate-800">{m.nombre}</span>
              <span className="block text-xs text-slate-500 mt-1">{m.descripcion}</span>
            </button>
          ))}
        </div>
      )}

      <div role="alert">{error && <p className="text-sm text-red-700">{error}</p>}</div>
      <div role="status" aria-live="polite">{hecho && <p className="text-sm text-emerald-800">{hecho}</p>}</div>

      {plantillas.length === 0 && !error ? (
        <p className="text-sm text-slate-500">Aún no hay plantillas: las inspecciones se hacen sin checklist.</p>
      ) : (
        <ul className="flex flex-col divide-y divide-slate-100 bg-white rounded-xl border border-slate-200">
          {plantillas.map((p) => (
            <li key={p.id} className="flex flex-wrap items-center justify-between gap-3 px-5 py-4">
              <div>
                <p className="text-sm font-semibold text-slate-800 flex items-center gap-2">
                  <span>{p.nombre}</span>
                  {p.por_defecto && (
                    <span className="text-xs font-medium text-blue-700 bg-blue-50 rounded px-2 py-0.5">Por defecto</span>
                  )}
                </p>
                <p className="text-xs text-slate-500">
                  {plural(totalPuntos(p), "punto", "puntos")} · {plural(p.propiedades_count, "propiedad", "propiedades")}
                </p>
              </div>
              <div className="flex flex-wrap gap-1 text-sm font-medium">
                <button type="button" aria-label={`Editar ${p.nombre}`}
                  onClick={() => setEditando({ ...p })} className={ACCION}>
                  Editar
                </button>
                <button type="button" aria-label={`Duplicar ${p.nombre}`}
                  onClick={() => ejecutar(() => duplicarPlantilla(p.id), `Plantilla «${p.nombre}» duplicada.`)}
                  className={ACCION}>
                  Duplicar
                </button>
                {!p.por_defecto && (
                  <button type="button" aria-label={`Marcar por defecto ${p.nombre}`}
                    onClick={() =>
                      ejecutar(
                        () => guardarPlantilla(p.id, { por_defecto: true, version: p.version }),
                        `«${p.nombre}» es ahora la plantilla por defecto.`,
                      )
                    }
                    className={ACCION}>
                    Marcar por defecto
                  </button>
                )}
                <button type="button" aria-label={`Borrar ${p.nombre}`} onClick={() => borrar(p)}
                  className={BORRAR}>
                  Borrar
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
