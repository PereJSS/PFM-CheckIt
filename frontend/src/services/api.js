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

// Interceptor de respuesta: si el access token expiró (401), intenta renovarlo
api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const original = error.config;

    // 429 (throttling de DRF): mensaje claro y homogéneo para toda la app. Se deja
    // en error.message, en data.detail y en data.non_field_errors, que es donde
    // leen los formularios existentes.
    if (error.response?.status === 429) {
      const segundos = Number.parseInt(
        error.response.headers?.["retry-after"],
        10,
      );
      const mensaje = Number.isFinite(segundos)
        ? `Demasiados intentos, espera ${Math.max(1, segundos)} s y vuelve a probar.`
        : "Demasiados intentos, espera un momento y vuelve a probar.";
      error.message = mensaje;
      error.response.data = { detail: mensaje, non_field_errors: mensaje };
      return Promise.reject(error);
    }

    // Solo intentar renovar una vez y solo ante 401
    if (error.response?.status === 401 && !original._retry) {
      original._retry = true;
      const refreshToken = getRefreshToken();

      if (refreshToken) {
        try {
          const { data } = await axios.post(`${apiBaseUrl}/auth/refresh/`, {
            refresh: refreshToken,
          });
          setAccessToken(data.access);
          original.headers.Authorization = `Bearer ${data.access}`;
          return api(original);
        } catch {
          // Refresh fallido: limpiar sesión y redirigir al login
          clearAuthTokens();
          window.location.href = "/login";
        }
      } else {
        // Sin refresh token: redirigir al login
        clearAuthTokens();
        window.location.href = "/login";
      }
    }

    return Promise.reject(error);
  },
);

export default api;
