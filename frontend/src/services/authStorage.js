// Claves centralizadas para evitar typos al leer/escribir tokens en storage.
const ACCESS_TOKEN_KEY = "access_token";
const REFRESH_TOKEN_KEY = "refresh_token";

// Lee el access token activo desde sessionStorage.
export const getAccessToken = () => sessionStorage.getItem(ACCESS_TOKEN_KEY);

// Lee el refresh token activo desde sessionStorage.
export const getRefreshToken = () => sessionStorage.getItem(REFRESH_TOKEN_KEY);

// Guarda ambos tokens cuando llegan en login o refresco inicial.
export const setAuthTokens = ({ accessToken, refreshToken }) => {
  if (accessToken) {
    sessionStorage.setItem(ACCESS_TOKEN_KEY, accessToken);
  }
  if (refreshToken) {
    sessionStorage.setItem(REFRESH_TOKEN_KEY, refreshToken);
  }
};

// Actualiza solo el access token (p. ej., tras refresh silencioso de sesión).
export const setAccessToken = (accessToken) => {
  if (accessToken) {
    sessionStorage.setItem(ACCESS_TOKEN_KEY, accessToken);
  }
};

// Cierra sesión local eliminando tokens de almacenamiento de sesión y legado.
export const clearAuthTokens = () => {
  sessionStorage.removeItem(ACCESS_TOKEN_KEY);
  sessionStorage.removeItem(REFRESH_TOKEN_KEY);
  // Limpieza de compatibilidad con sesiones antiguas compartidas por pestañas.
  localStorage.removeItem(ACCESS_TOKEN_KEY);
  localStorage.removeItem(REFRESH_TOKEN_KEY);
};
