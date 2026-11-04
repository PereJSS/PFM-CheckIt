// Consulta del verificador público. Usa fetch y no la instancia de api.js: esa adjunta el JWT
// de la sesión y reintenta con el refresh, y aquí no hay cuenta.
const baseUrl = import.meta.env.VITE_API_URL || "/api/v1";

// El plan gratuito de Render puede tardar en despertar
const TIEMPO_MAXIMO_MS = 90_000;

export async function consultarVerificacion(clave, sha256) {
  const control = new AbortController();
  const temporizador = setTimeout(() => control.abort(), TIEMPO_MAXIMO_MS);
  try {
    const respuesta = await fetch(`${baseUrl}/verificacion/`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ clave, sha256 }),
      credentials: "omit",
      cache: "no-store",
      referrerPolicy: "no-referrer",
      signal: control.signal,
    });
    if (respuesta.status === 200) {
      try {
        return { tipo: "ok", datos: await respuesta.json() };
      } catch {
        return { tipo: "servidor" };
      }
    }
    if (respuesta.status === 400) return { tipo: "formato" };
    if (respuesta.status === 404) return { tipo: "no_encontrada" };
    if (respuesta.status === 429) {
      const cabecera = respuesta.headers.get("Retry-After");
      const segundos = /^\d{1,6}$/.test(cabecera ?? "") ? Number(cabecera) : null;
      return { tipo: "espera", segundos };
    }
    return { tipo: "servidor" };
  } catch (error) {
    return { tipo: error?.name === "AbortError" ? "tiempo" : "red" };
  } finally {
    clearTimeout(temporizador);
  }
}
