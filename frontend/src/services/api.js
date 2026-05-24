// este archivo se encarga de configurar una instancia de axios para hacer solicitudes HTTP a la API del backend.
//  La baseURL se establece en "/api/v1", lo que significa que todas las solicitudes realizadas con esta instancia de axios se dirigirán a esa ruta base.

import axios from "axios";

const api = axios.create({
  baseURL: "/api/v1",
});

// Interceptor de petición: adjunta el token JWT en cada petición
api.interceptors.request.use((config) => {
  const token = localStorage.getItem("access_token");
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

    // Solo intentar renovar una vez y solo ante 401
    if (error.response?.status === 401 && !original._retry) {
      original._retry = true;
      const refreshToken = localStorage.getItem("refresh_token");

      if (refreshToken) {
        try {
          const { data } = await axios.post("/api/v1/auth/refresh/", {
            refresh: refreshToken,
          });
          localStorage.setItem("access_token", data.access);
          original.headers.Authorization = `Bearer ${data.access}`;
          return api(original);
        } catch {
          // Refresh fallido: limpiar sesión y redirigir al login
          localStorage.removeItem("access_token");
          localStorage.removeItem("refresh_token");
          window.location.href = "/login";
        }
      } else {
        // Sin refresh token: redirigir al login
        localStorage.removeItem("access_token");
        window.location.href = "/login";
      }
    }

    return Promise.reject(error);
  },
);

export default api;
