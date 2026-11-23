// Claves centralizadas para evitar typos al leer/escribir tokens en storage.
const ACCESS_TOKEN_KEY = "access_token";
const REFRESH_TOKEN_KEY = "refresh_token";
// Rol del usuario de esta pestaña (#15): no es secreto ni da permisos (los decide el
// servidor en cada petición); solo deja abrir la app sin red sin cerrar la sesión.
const ROL_KEY = "rol";

// Lee el access token activo desde sessionStorage.
export const getAccessToken = () => sessionStorage.getItem(ACCESS_TOKEN_KEY);

// Lee el refresh token activo desde sessionStorage.
export const getRefreshToken = () => sessionStorage.getItem(REFRESH_TOKEN_KEY);

const ROLES = ["ADMINISTRADOR", "ADMIN", "OPERARIO"];

export const getRolGuardado = () => {
  const rol = sessionStorage.getItem(ROL_KEY);
  return ROLES.includes(rol) ? rol : null;
};

// Un rol desconocido o vacío borra el guardado: nunca queda el de otra sesión
export const guardarRol = (rol) => {
  if (ROLES.includes(rol)) sessionStorage.setItem(ROL_KEY, rol);
  else sessionStorage.removeItem(ROL_KEY);
};

// Guarda ambos tokens cuando llegan en login o refresco inicial.
export const setAuthTokens = ({ accessToken, refreshToken }) => {
  if (accessToken) {
    sessionStorage.setItem(ACCESS_TOKEN_KEY, accessToken);
  }
  if (refreshToken) {
    sessionStorage.setItem(REFRESH_TOKEN_KEY, refreshToken);
  }
};

// Cierra sesión local eliminando tokens de almacenamiento de sesión y legado.
export const clearAuthTokens = () => {
  sessionStorage.removeItem(ACCESS_TOKEN_KEY);
  sessionStorage.removeItem(REFRESH_TOKEN_KEY);
  sessionStorage.removeItem(ROL_KEY);
  // Limpieza de compatibilidad con sesiones antiguas compartidas por pestañas.
  localStorage.removeItem(ACCESS_TOKEN_KEY);
  localStorage.removeItem(REFRESH_TOKEN_KEY);
};
