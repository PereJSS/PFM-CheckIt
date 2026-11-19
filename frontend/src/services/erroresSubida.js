// Qué hacer cuando falla la subida de una foto. Sin respuesta, con un 5xx o con un
// error pasajero (401 sesión caducada, 408, 425, 429) se guarda en el móvil y se
// reintenta. Un 4xx con el mensaje de la API (400 datos no válidos, 403, 404, 409
// inspección completada…) o un 413 no se arreglan reintentando: antes se reintentaban
// para siempre y la foto quedaba atascada, bloqueando además finalizar la inspección.
const REINTENTABLES = new Set([401, 408, 425, 429]);

// Mensaje de la API ({"error": …} o {"detail": …} de DRF). Un 4xx sin él viene de un
// proxy, de un WAF o de un despliegue a medias, y se reintenta
const mensajeDeLaApi = (error) => {
  const data = error?.response?.data;
  const mensaje = data?.error ?? data?.detail;
  return typeof mensaje === "string" && mensaje.trim() ? mensaje : null;
};

export const esRechazoDefinitivo = (error) => {
  const status = error?.response?.status;
  if (typeof status !== "number" || status < 400 || status >= 500) return false;
  if (REINTENTABLES.has(status)) return false;
  // La foto no va a encoger: da igual si el límite es del proxy o de Django
  if (status === 413) return true;
  return mensajeDeLaApi(error) !== null;
};

export const motivoDelRechazo = (error) => {
  const mensaje = mensajeDeLaApi(error);
  if (mensaje) return mensaje;
  const status = error?.response?.status;
  if (status === 413) return "La foto es demasiado grande para el servidor.";
  return `El servidor rechazó la foto (código ${status}).`;
};
