// Multipart de una foto para POST /inspecciones/{id}/evidencias/. Lo usan la subida
// normal y la sincronización de la cola sin conexión, para que manden lo mismo.
// La estancia va en su propio campo: antes iba en la descripción, y solo si el
// operario no escribía nada, así que con descripción el acta no la decía.
// Mismo tope que DESCRIPCION_MAX en el backend (inspecciones/models.py)
export const DESCRIPCION_MAX = 1000;

export const formularioEvidencia = ({
  file,
  room,
  description,
  hash,
  capturedAt,
  punto,
}) => {
  const formData = new FormData();
  formData.append("foto", file, file?.name || `evidencia_${Date.now()}.jpg`);
  if (room) formData.append("estancia", room);
  if (description) formData.append("descripcion", description);
  if (hash) formData.append("hash_sha256", hash);
  if (capturedAt) formData.append("capturada_en", capturedAt);
  // Punto del checklist que documenta (#19); la estancia la pone el servidor desde él
  if (punto) formData.append("punto", String(punto));
  return formData;
};
