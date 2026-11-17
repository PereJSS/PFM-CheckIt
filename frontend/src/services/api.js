// este archivo se encarga de configurar una instancia de axios para hacer solicitudes HTTP a la API del backend.
//  La baseURL se establece en "/api/v1", lo que significa que todas las solicitudes realizadas con esta instancia de axios se dirigirán a esa ruta base.

import axios from "axios";
import {
  clearAuthTokens,
  getAccessToken,
  getRefreshToken,
  setAccessToken,
} from "./authStorage";

export const apiBaseUrl = import.meta.env.VITE_API_URL || "/api/v1";

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
      .post(`${apiBaseUrl}/auth/refresh/`, { refresh: refreshToken })
      .then(({ data }) => {
        if (!sigueLaMismaSesion(refreshToken)) {
          throw new Error("La sesión cambió durante el refresco");
        }
        setAccessToken(data.access);
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
