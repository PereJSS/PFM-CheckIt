// este archivo se encarga de configurar una instancia de axios para hacer solicitudes HTTP a la API del backend.
//  La baseURL se establece en "/api/v1", lo que significa que todas las solicitudes realizadas con esta instancia de axios se dirigirán a esa ruta base.

import axios from "axios";
import {
  clearAuthTokens,
  getAccessToken,
  getRefreshToken,
  setAuthTokens,
} from "./authStorage";

export const apiBaseUrl = import.meta.env.VITE_API_URL || "/api/v1";

// Tope del refresco: sin él, una respuesta que no llega (Render arrancando) deja colgadas
// todas las peticiones que esperan el token. Al vencer cuenta como fallo de red: no cierra sesión
export const REFRESCO_TIMEOUT_MS = 15000;

const api = axios.create({
  baseURL: apiBaseUrl,
});

// Interceptor de petición: adjunta el token JWT en cada petición
api.interceptors.request.use((config) => {
  const token = getAccessToken();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// 429 (throttling de DRF): mensaje claro y homogéneo para toda la app. Se deja
// en error.message, en data.detail y en data.non_field_errors, que es donde
// leen los formularios existentes.
const avisarDemasiadosIntentos = (error) => {
  const segundos = Number.parseInt(error.response.headers?.["retry-after"], 10);
  const mensaje = Number.isFinite(segundos)
    ? `Demasiados intentos, espera ${Math.max(1, segundos)} s y vuelve a probar.`
    : "Demasiados intentos, espera un momento y vuelve a probar.";
  error.message = mensaje;
  error.response.data = { detail: mensaje, non_field_errors: mensaje };
  return error;
};

// Un único refresco en curso por refresh token: las peticiones que reciben 401 a la
// vez esperan al mismo, en vez de gastar cada una un intento del límite de
// /auth/refresh/. Va ligado al token con el que empezó: si entretanto se cierra la
// sesión o entra otro usuario, su resultado no se escribe ni borra nada.
let refrescoEnCurso = null;

const sigueLaMismaSesion = (refreshToken) => getRefreshToken() === refreshToken;

const refrescarAccessToken = (refreshToken) => {
  if (refrescoEnCurso?.refreshToken !== refreshToken) {
    const promesa = axios
      .post(`${apiBaseUrl}/auth/refresh/`, { refresh: refreshToken }, { timeout: REFRESCO_TIMEOUT_MS })
      .then(({ data }) => {
        if (!sigueLaMismaSesion(refreshToken)) {
          throw new Error("La sesión cambió durante el refresco");
        }
        // Rotación: el servidor anula el refresh usado y entrega otro en la respuesta
        setAuthTokens({ accessToken: data.access, refreshToken: data.refresh });
        return data.access;
      })
      .finally(() => {
        if (refrescoEnCurso?.promesa === promesa) refrescoEnCurso = null;
      });
    refrescoEnCurso = { refreshToken, promesa };
  }
  return refrescoEnCurso.promesa;
};

const cerrarSesion = () => {
  clearAuthTokens();
  window.location.href = "/login";
};

// Botón «Cerrar sesión»: anula el refresh en el servidor y, sin esperar respuesta, borra
// la sesión local. keepalive deja terminar la petición aunque la página navegue a /login;
// si falla (sin red, 429), el refresh caduca solo y ya no está en esta pestaña. Si había un
// refresco en vuelo, el refresh que este entregue no se guarda (cambió la sesión) y caduca solo.
const avisarLogoutFallido = (motivo) =>
  console.warn("No se pudo anular la sesión en el servidor; caducará sola:", motivo);

export const salir = () => {
  const refresh = getRefreshToken();
  if (refresh) {
    fetch(`${apiBaseUrl}/auth/logout/`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh }),
      keepalive: true,
    })
      .then((respuesta) => {
        if (!respuesta.ok) avisarLogoutFallido(respuesta.status);
      })
      .catch(avisarLogoutFallido);
  }
  cerrarSesion();
};

// Interceptor de respuesta: si el access token expiró (401), intenta renovarlo
api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const original = error.config;

    if (error.response?.status === 429) {
      return Promise.reject(avisarDemasiadosIntentos(error));
    }

    // Solo intentar renovar una vez y solo ante 401
    if (error.response?.status === 401 && !original._retry) {
      original._retry = true;

      // 401 que llega tarde: el token ya se renovó después de enviar la petición
      const accessActual = getAccessToken();
      if (accessActual && original.headers?.Authorization !== `Bearer ${accessActual}`) {
        return api(original);
      }

      const refreshToken = getRefreshToken();
      if (!refreshToken) {
        cerrarSesion();
        return Promise.reject(error);
      }
      try {
        await refrescarAccessToken(refreshToken);
      } catch (errorRefresco) {
        const estado = errorRefresco.response?.status;
        // Un 429 del refresco no invalida la sesión: se espera y se reintenta luego
        if (estado === 429) {
          return Promise.reject(avisarDemasiadosIntentos(errorRefresco));
        }
        // Solo un refresh token rechazado (400/401) cierra la sesión, y solo si sigue
        // siendo la de esta pestaña. Red caída o 5xx (Render arrancando) no la cierran.
        if ((estado === 400 || estado === 401) && sigueLaMismaSesion(refreshToken)) {
          cerrarSesion();
        }
        return Promise.reject(error);
      }
      return api(original);
    }

    return Promise.reject(error);
  },
);

export default api;
