// Hora de captura según el dispositivo (#29), en ISO UTC, a partir del lastModified
// del archivo. Nunca lanza: un valor raro devuelve null y la foto se sube sin hora,
// porque es un metadato opcional y no debe hacer perder la evidencia.
export function horaDeArchivo(file) {
  const ms = file?.lastModified;
  if (!Number.isFinite(ms) || ms <= 0) return null;
  try {
    return new Date(ms).toISOString();
  } catch {
    return null;
  }
}
