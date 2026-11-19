import { useEffect, useRef, useState } from "react";
import { crearPlantilla, guardarPlantilla } from "../../services/checklists";
import { motivoDelRechazo } from "../../services/erroresSubida";

let ultimaClave = 0;
const nuevaClave = () => ++ultimaClave;

const conClaves = (estructura) =>
  (estructura?.estancias || []).map((e) => ({
    clave: nuevaClave(),
    nombre: e.nombre,
    puntos: e.puntos.map((p) => ({ clave: nuevaClave(), texto: p.texto, requiere_foto: !!p.requiere_foto })),
  }));

const puntoVacio = () => ({ clave: nuevaClave(), texto: "", requiere_foto: false });

const mover = (lista, i, salto) => {
  const j = i + salto;
  if (j < 0 || j >= lista.length) return lista;
  const copia = [...lista];
  [copia[i], copia[j]] = [copia[j], copia[i]];
  return copia;
};

// Mensajes de un 400 de DRF ({campo: [mensajes]}, anidados o {error: "…"}) en una lista
const aplanar = (v) =>
  typeof v === "string"
    ? [v]
    : Array.isArray(v)
      ? v.flatMap(aplanar)
      : v && typeof v === "object"
        ? Object.values(v).flatMap(aplanar)
        : v == null
          ? []
          : [String(v)];

const mensajesDeError = (data) => {
  const mensajes = aplanar(data);
  return mensajes.length ? mensajes : ["No se pudo guardar la plantilla."];
};

const FOCO = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:ring-offset-1";
const BOTON_MINI =
  `min-w-9 min-h-9 px-2 rounded border border-slate-400 text-sm text-slate-700 hover:bg-slate-50 disabled:opacity-30 ${FOCO}`;

// Editor de una plantilla de checklist (#19): estancias y puntos, con botones para
// ordenar (sin arrastrar). Al editar una existente manda la `version` leída: si otra
// persona la cambió entretanto, el servidor responde 409 y se ofrece recargar.
export default function EditorPlantilla({ plantilla, onGuardada, onCancelar, onRecargar, avisoExterno }) {
  const [nombre, setNombre] = useState(plantilla.nombre || "");
  const [descripcion, setDescripcion] = useState(plantilla.descripcion || "");
  const [porDefecto, setPorDefecto] = useState(!!plantilla.por_defecto);
  const [estancias, setEstancias] = useState(() => conClaves(plantilla.estructura));
  const [guardando, setGuardando] = useState(false);
  const [errores, setErrores] = useState([]);
  const [conflicto, setConflicto] = useState(false);
  // Foco: al añadir va al campo nuevo; al quitar, al vecino; con un error, al aviso
  const enfocar = useRef(null);
  const alerta = useRef(null);
  const campo = (clave) => (el) => {
    if (el && enfocar.current === clave) {
      el.focus();
      enfocar.current = null;
    }
  };
  useEffect(() => {
    if (conflicto || errores.length > 0 || avisoExterno) alerta.current?.focus();
  }, [conflicto, errores, avisoExterno]);

  const cambiarEstancia = (i, cambio) =>
    setEstancias((lista) => lista.map((e, k) => (k === i ? { ...e, ...cambio(e) } : e)));
  const cambiarPunto = (i, j, cambio) =>
    cambiarEstancia(i, (e) => ({ puntos: e.puntos.map((p, k) => (k === j ? { ...p, ...cambio } : p)) }));

  const guardar = async (ev) => {
    ev.preventDefault();
    setErrores([]);
    setConflicto(false);
    setGuardando(true);
    const datos = {
      nombre,
      descripcion,
      por_defecto: porDefecto,
      estructura: {
        estancias: estancias.map((e) => ({
          nombre: e.nombre,
          puntos: e.puntos.map((p) => ({ texto: p.texto, requiere_foto: p.requiere_foto })),
        })),
      },
    };
    try {
      const guardada = plantilla.id
        ? await guardarPlantilla(plantilla.id, { ...datos, version: plantilla.version })
        : await crearPlantilla(datos);
      onGuardada(guardada);
    } catch (error) {
      const status = error?.response?.status;
      // Solo es «otra persona la cambió» el 409 de versión (trae la versión actual)
      if (status === 409 && error.response.data?.version !== undefined) setConflicto(true);
      else if (status === 400) setErrores(mensajesDeError(error.response.data));
      else if (status && status < 500) setErrores([motivoDelRechazo(error, "la plantilla")]);
      else {
        console.error("No se pudo guardar la plantilla", error);
        setErrores(["No se pudo guardar la plantilla. Inténtalo de nuevo."]);
      }
    } finally {
      setGuardando(false);
    }
  };

  return (
    <form onSubmit={guardar} className="bg-white rounded-xl border border-slate-200 shadow-sm p-6 flex flex-col gap-5">
      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <label htmlFor="plantilla-nombre" className="block text-xs font-medium text-slate-500 mb-1">
            Nombre de la plantilla
          </label>
          <input
            id="plantilla-nombre"
            value={nombre}
            maxLength={100}
            required
            onChange={(e) => setNombre(e.target.value)}
            className="w-full px-3 py-2 rounded-lg border border-slate-300 text-sm"
          />
        </div>
        <label className="flex items-center gap-2 text-sm text-slate-700 self-end">
          <input type="checkbox" checked={porDefecto} onChange={(e) => setPorDefecto(e.target.checked)} />
          Plantilla por defecto del cliente
        </label>
      </div>
      <div>
        <label htmlFor="plantilla-descripcion" className="block text-xs font-medium text-slate-500 mb-1">
          Descripción (opcional)
        </label>
        <textarea
          id="plantilla-descripcion"
          value={descripcion}
          maxLength={1000}
          onChange={(e) => setDescripcion(e.target.value)}
          className="w-full px-3 py-2 rounded-lg border border-slate-300 text-sm resize-none h-16"
        />
      </div>

      {estancias.map((e, i) => {
        const nombreEstancia = e.nombre || `estancia ${i + 1}`;
        return (
          <fieldset key={e.clave} className="rounded-lg border border-slate-200 p-4 flex flex-col gap-3">
            <legend className="sr-only">{nombreEstancia}</legend>
            <div className="flex gap-2 items-center">
              <input
                ref={campo(e.clave)}
                aria-label={`Nombre de la estancia ${i + 1}`}
                value={e.nombre}
                maxLength={60}
                required
                onChange={(ev) => cambiarEstancia(i, () => ({ nombre: ev.target.value }))}
                className="flex-1 px-3 py-2 rounded-lg border border-slate-300 text-sm font-semibold"
              />
              <button type="button" className={BOTON_MINI} disabled={i === 0}
                aria-label={`Subir estancia ${nombreEstancia}`} onClick={() => setEstancias((l) => mover(l, i, -1))}>↑</button>
              <button type="button" className={BOTON_MINI} disabled={i === estancias.length - 1}
                aria-label={`Bajar estancia ${nombreEstancia}`} onClick={() => setEstancias((l) => mover(l, i, 1))}>↓</button>
              <button type="button" className={BOTON_MINI} disabled={estancias.length === 1}
                aria-label={`Quitar estancia ${nombreEstancia}`}
                onClick={() => {
                  enfocar.current = (estancias[i - 1] || estancias[i + 1])?.clave;
                  setEstancias((l) => l.filter((_, k) => k !== i));
                }}>✕</button>
            </div>

            <ul className="flex flex-col gap-2">
              {e.puntos.map((p, j) => {
                const nombrePunto = p.texto || `punto ${j + 1}`;
                return (
                  <li key={p.clave} className="flex flex-wrap gap-2 items-center">
                    <input
                      ref={campo(p.clave)}
                      aria-label={`Punto ${j + 1} de ${nombreEstancia}`}
                      value={p.texto}
                      maxLength={200}
                      required
                      onChange={(ev) => cambiarPunto(i, j, { texto: ev.target.value })}
                      className="flex-1 min-w-40 px-3 py-1.5 rounded-lg border border-slate-300 text-sm"
                    />
                    <label className="flex items-center gap-2 min-h-9 text-xs text-slate-700">
                      <input
                        type="checkbox"
                        className="w-5 h-5"
                        checked={p.requiere_foto}
                        onChange={(ev) => cambiarPunto(i, j, { requiere_foto: ev.target.checked })}
                      />
                      Foto obligatoria{" "}
                      <span className="sr-only">del punto {j + 1} de {nombreEstancia}</span>
                    </label>
                    <button type="button" className={BOTON_MINI} disabled={j === 0}
                      aria-label={`Subir punto ${nombrePunto}`}
                      onClick={() => cambiarEstancia(i, (est) => ({ puntos: mover(est.puntos, j, -1) }))}>↑</button>
                    <button type="button" className={BOTON_MINI} disabled={j === e.puntos.length - 1}
                      aria-label={`Bajar punto ${nombrePunto}`}
                      onClick={() => cambiarEstancia(i, (est) => ({ puntos: mover(est.puntos, j, 1) }))}>↓</button>
                    <button type="button" className={BOTON_MINI} disabled={e.puntos.length === 1}
                      aria-label={`Quitar punto ${nombrePunto}`}
                      onClick={() => {
                        enfocar.current = (e.puntos[j - 1] || e.puntos[j + 1])?.clave;
                        cambiarEstancia(i, (est) => ({ puntos: est.puntos.filter((_, k) => k !== j) }));
                      }}>✕</button>
                  </li>
                );
              })}
            </ul>
            <button type="button" aria-label={`Añadir punto a ${nombreEstancia}`}
              onClick={() => {
                const nuevo = puntoVacio();
                enfocar.current = nuevo.clave;
                cambiarEstancia(i, (est) => ({ puntos: [...est.puntos, nuevo] }));
              }}
              className={`self-start min-h-9 px-2 rounded text-sm font-semibold text-blue-700 hover:bg-blue-50 ${FOCO}`}>
              + Punto
            </button>
          </fieldset>
        );
      })}

      <button type="button"
        onClick={() => {
          const nueva = { clave: nuevaClave(), nombre: "", puntos: [puntoVacio()] };
          enfocar.current = nueva.clave;
          setEstancias((l) => [...l, nueva]);
        }}
        className={`self-start min-h-9 px-2 rounded text-sm font-semibold text-blue-700 hover:bg-blue-50 ${FOCO}`}>
        Añadir estancia
      </button>

      {(conflicto || errores.length > 0 || avisoExterno) && (
        <div role="alert" ref={alerta} tabIndex={-1}
          className="px-4 py-3 rounded-lg bg-red-50 text-red-800 border border-red-200 text-sm focus:outline-none">
          {avisoExterno && <p>{avisoExterno}</p>}
          {conflicto ? (
            <div className="flex flex-wrap items-center gap-3">
              <span>Otra persona ha cambiado esta plantilla desde que la abriste. Recárgala para ver sus cambios (perderás los tuyos).</span>
              <button type="button" onClick={() => onRecargar(plantilla.id)} className="font-semibold underline">
                Recargar
              </button>
            </div>
          ) : errores.length > 0 && (
            <ul className="list-disc pl-5">
              {errores.map((m, k) => <li key={k}>{m}</li>)}
            </ul>
          )}
        </div>
      )}

      <div className="flex gap-3">
        <button type="submit" disabled={guardando}
          className="px-4 py-2 rounded-lg bg-blue-600 text-white text-sm font-semibold hover:bg-blue-700 disabled:opacity-50">
          {guardando ? "Guardando…" : "Guardar plantilla"}
        </button>
        <button type="button" onClick={onCancelar} className="px-4 py-2 rounded-lg text-sm text-slate-600 hover:bg-slate-100">
          Cancelar
        </button>
      </div>
    </form>
  );
}
