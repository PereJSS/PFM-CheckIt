import { useCallback, useEffect, useState } from "react";
import api from "../../services/api";
import { mensajeError } from "./estilos";

// Carga un listado (array plano) de la API sin carreras:
// - cada petición se cancela con AbortController si llega otra (filtros,
//   "Actualizar", recarga tras una acción) o si el componente se desmonta;
// - el estado guarda la "clave" de la petición que respondió, así que
//   `cargando` solo se apaga cuando responde la última, nunca una vieja.
// `query` es un string (URLSearchParams) para que las dependencias sean estables.
export default function useListado(url, query, textoError) {
  const [nonce, setNonce] = useState(0);
  const clave = `${url}?${query}#${nonce}`;
  const [res, setRes] = useState({
    clave: null,
    instante: 0,
    datos: [],
    error: null,
    noDisponible: false,
  });

  useEffect(() => {
    const ctrl = new AbortController();
    const claveActual = `${url}?${query}#${nonce}`;
    api
      .get(url, { params: new URLSearchParams(query), signal: ctrl.signal })
      .then((r) => {
        if (Array.isArray(r.data)) {
          setRes({
            clave: claveActual,
            // Momento de la respuesta, para cálculos de antigüedad sin Date.now() en render.
            instante: Date.now(),
            datos: r.data,
            error: null,
            noDisponible: false,
          });
        } else {
          setRes((prev) => ({
            ...prev,
            clave: claveActual,
            error: "Respuesta inesperada del servidor.",
          }));
        }
      })
      .catch((err) => {
        if (ctrl.signal.aborted) return;
        setRes((prev) => ({
          ...prev,
          clave: claveActual,
          error: mensajeError(err, textoError),
          noDisponible: err.response?.status === 404,
        }));
      });
    return () => ctrl.abort();
  }, [url, query, nonce, textoError]);

  const recargar = useCallback(() => setNonce((n) => n + 1), []);

  return {
    datos: res.datos,
    error: res.error,
    noDisponible: res.noDisponible,
    instante: res.instante,
    cargando: res.clave !== clave,
    recargar,
  };
}
