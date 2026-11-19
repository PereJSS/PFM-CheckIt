import { useEffect, useRef, useState } from "react";
import { DESCRIPCION_MAX } from "../services/formularioEvidencia";
import { RESULTADOS, progreso } from "../services/checklists";

// Marcado: color y ✓ (el color no es la única señal); tonos 700 para el contraste del texto
const MARCADO = {
  OK: "bg-emerald-700 text-white border-emerald-700",
  DANO: "bg-red-700 text-white border-red-700",
  NO_APLICA: "bg-slate-700 text-white border-slate-700",
};
const FOCO = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:ring-offset-2";

function Punto({ punto, estancia, fotosPendientes, onMarcar, onFoto, deshabilitado }) {
  // El padre remonta este punto cuando cambia la nota guardada (key), así que el campo
  // siempre parte de lo guardado: tras deshacer una marca no queda texto que no se guardó
  const [nota, setNota] = useState(punto.nota || "");
  const fotos = (punto.evidencias?.length || 0) + (fotosPendientes[punto.id] || 0);
  const tituloId = `punto-${punto.id}`;
  const fotosId = `punto-${punto.id}-fotos`;
  const faltaFoto = fotos === 0 && punto.resultado === "DANO";

  return (
    <li role="group" aria-labelledby={tituloId} aria-describedby={fotosId}
      className="rounded-lg border border-slate-200 p-4 flex flex-col gap-3">
      <div className="flex items-start justify-between gap-2">
        <p id={tituloId} className="text-sm font-semibold text-slate-800">
          {punto.texto}
        </p>
        {punto.requiere_foto && (
          <span className="shrink-0 text-xs font-medium text-amber-800 bg-amber-50 rounded px-2 py-0.5">
            Foto obligatoria
          </span>
        )}
      </div>

      <div className="grid grid-cols-3 gap-2">
        {RESULTADOS.map((r) => {
          const activo = punto.resultado === r.valor;
          return (
            <button
              key={r.valor}
              type="button"
              aria-pressed={activo}
              disabled={deshabilitado}
              onClick={() => onMarcar(punto, { resultado: r.valor })}
              className={`min-h-12 px-2 py-3 rounded-lg border-2 text-sm font-semibold transition disabled:opacity-40 ${FOCO} ${
                activo ? MARCADO[r.valor] : "bg-white text-slate-800 border-slate-500 hover:bg-slate-50"
              }`}
            >
              {activo && <span aria-hidden="true">✓ </span>}
              {r.etiqueta}{" "}
              <span className="sr-only">— {punto.texto}</span>
            </button>
          );
        })}
      </div>

      <div>
        <label htmlFor={`nota-${punto.id}`} className="block text-xs font-medium text-slate-600 mb-1">
          Nota (opcional)
        </label>
        <textarea
          id={`nota-${punto.id}`}
          value={nota}
          maxLength={DESCRIPCION_MAX}
          disabled={deshabilitado}
          onChange={(e) => setNota(e.target.value)}
          onBlur={() => {
            if (nota !== (punto.nota || "")) onMarcar(punto, { nota });
          }}
          className="w-full px-3 py-2 rounded-lg border border-slate-400 text-sm min-h-16"
        />
      </div>

      <div className="flex items-center justify-between gap-2">
        <p id={fotosId} aria-live="polite"
          className={`text-xs ${faltaFoto ? "text-red-700 font-semibold" : "text-slate-600"}`}>
          {faltaFoto ? "Falta una foto del daño." : fotos === 1 ? "1 foto" : `${fotos} fotos`}
        </p>
        <button
          type="button"
          disabled={deshabilitado}
          onClick={() => onFoto(punto, estancia.nombre)}
          className={`min-h-11 px-4 rounded-lg border-2 border-blue-700 text-sm font-semibold text-blue-700 hover:bg-blue-50 disabled:opacity-40 ${FOCO}`}
        >
          Añadir foto{" "}<span className="sr-only">a {punto.texto}</span>
        </button>
      </div>
    </li>
  );
}

// Checklist de la inspección (#19): estancias con su progreso y, dentro, cada punto con
// OK / Daño / No aplica, nota y foto. Quien guarda (en línea o en el móvil) es el padre.
export default function ChecklistFlow({ checklist, fotosPendientes = {}, onMarcar, onFoto, deshabilitado = false }) {
  const [estanciaId, setEstanciaId] = useState(null);
  // Al entrar o salir de una estancia el botón pulsado desaparece: el foco va al título
  // de la estancia, o de vuelta al botón de la estancia que se dejó
  const tituloEstancia = useRef(null);
  const botonesEstancia = useRef({});
  const ultimaEstancia = useRef(null);
  const estancia = checklist.estancias.find((e) => e.id === estanciaId);

  useEffect(() => {
    if (estanciaId != null) tituloEstancia.current?.focus();
    else if (ultimaEstancia.current != null) botonesEstancia.current[ultimaEstancia.current]?.focus();
  }, [estanciaId]);

  if (!estancia) {
    return (
      <section aria-labelledby="checklist-titulo" className="flex flex-col gap-2">
        <h3 id="checklist-titulo" className="text-xs font-semibold text-slate-600 uppercase tracking-wide">
          Checklist · {checklist.plantilla_nombre}
        </h3>
        <ul className="flex flex-col gap-2">
          {checklist.estancias.map((e) => {
            const { marcados, total } = progreso(e);
            return (
              <li key={e.id}>
                <button
                  ref={(el) => {
                    botonesEstancia.current[e.id] = el;
                  }}
                  type="button"
                  onClick={() => {
                    ultimaEstancia.current = e.id;
                    setEstanciaId(e.id);
                  }}
                  className={`w-full min-h-12 flex items-center justify-between px-4 py-3 rounded-lg border border-slate-300 hover:bg-slate-50 text-left ${FOCO}`}
                >
                  <span className="text-sm font-semibold text-slate-800">{e.nombre}</span>
                  <span aria-hidden="true"
                    className={`text-xs font-medium ${marcados === total ? "text-emerald-800" : "text-slate-600"}`}>
                    {marcados === total ? "✓ " : ""}{marcados}/{total}
                  </span>
                  <span className="sr-only">, {marcados} de {total} puntos marcados</span>
                </button>
              </li>
            );
          })}
        </ul>
      </section>
    );
  }

  return (
    <section aria-labelledby="estancia-titulo" className="flex flex-col gap-3">
      <button
        type="button"
        onClick={() => setEstanciaId(null)}
        className={`self-start min-h-11 px-3 rounded-lg text-sm font-medium text-slate-700 hover:bg-slate-100 ${FOCO}`}
      >
        ← Estancias
      </button>
      <h3 id="estancia-titulo" ref={tituloEstancia} tabIndex={-1}
        className="text-base font-semibold text-slate-900 focus:outline-none">
        {estancia.nombre}
      </h3>
      <ul className="flex flex-col gap-3">
        {estancia.puntos.map((p) => (
          <Punto
            key={`${p.id}:${p.nota || ""}`}
            punto={p}
            estancia={estancia}
            fotosPendientes={fotosPendientes}
            onMarcar={onMarcar}
            onFoto={onFoto}
            deshabilitado={deshabilitado}
          />
        ))}
      </ul>
    </section>
  );
}
