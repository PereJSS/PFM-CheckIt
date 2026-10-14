import { useEffect, useId, useRef, useState } from "react";
import { BTN_SUBMIT } from "./estilos";

// Aviso con un secreto (API key o secreto de firma) que el backend solo devuelve
// una vez. Vive únicamente en el estado de React del padre: nunca se guarda en
// localStorage/sessionStorage, y al cerrar el aviso se descarta.
// Al montarse mueve el foco a su título; al cerrar, el padre devuelve el foco
// al botón que originó la acción (onCerrar).
export default function SecretoUnaVez({ titulo, valor, onCerrar }) {
  const [mensaje, setMensaje] = useState("");
  const inputRef = useRef(null);
  const tituloRef = useRef(null);
  const id = useId();

  useEffect(() => {
    tituloRef.current?.focus();
  }, []);

  const copiar = async () => {
    try {
      await navigator.clipboard.writeText(valor);
      setMensaje("Copiado al portapapeles.");
    } catch {
      // Sin Clipboard API (HTTP no seguro, permisos): dejamos el texto seleccionado.
      inputRef.current?.select();
      setMensaje(
        "No se pudo copiar automáticamente. El texto está seleccionado: cópialo con Ctrl+C.",
      );
    }
  };

  return (
    // Sin role="alert": no queremos que el lector de pantalla lea el secreto en voz alta.
    <section
      aria-labelledby={`${id}-titulo`}
      className="mb-6 rounded-xl border border-amber-300 bg-amber-50 p-5"
    >
      <h3
        id={`${id}-titulo`}
        ref={tituloRef}
        tabIndex={-1}
        className="text-sm font-semibold text-amber-900 focus:outline-none"
      >
        {titulo}
      </h3>
      <p id={`${id}-ayuda`} className="mt-1 text-sm text-amber-800">
        Copia este valor ahora y guárdalo en un lugar seguro.{" "}
        <strong>No se volverá a mostrar.</strong>
      </p>
      <div className="mt-3 flex flex-col gap-2 sm:flex-row">
        <label htmlFor={`${id}-valor`} className="sr-only">
          {titulo}
        </label>
        <input
          id={`${id}-valor`}
          ref={inputRef}
          readOnly
          value={valor}
          aria-describedby={`${id}-ayuda`}
          onFocus={(e) => e.target.select()}
          autoComplete="off"
          spellCheck={false}
          className="w-full rounded-lg border border-amber-300 bg-white px-3 py-2 font-mono text-xs text-slate-800 focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
        <button
          type="button"
          onClick={copiar}
          className={`shrink-0 ${BTN_SUBMIT}`}
        >
          Copiar
        </button>
        <button
          type="button"
          onClick={onCerrar}
          className="shrink-0 px-4 py-2 rounded-lg border border-amber-300 bg-white text-sm text-amber-900 hover:bg-amber-100 transition"
        >
          Ya lo he guardado
        </button>
      </div>
      <p aria-live="polite" className="mt-2 min-h-4 text-xs text-amber-900">
        {mensaje}
      </p>
    </section>
  );
}
