import { afterEach, describe, expect, it, vi } from "vitest";
import { consultarVerificacion } from "../verificacion";

const CLAVE = "a".repeat(64);
const FOTO = "b".repeat(64);

const respuesta = (status, cuerpo = {}, cabeceras = {}) =>
  new Response(typeof cuerpo === "string" ? cuerpo : JSON.stringify(cuerpo), {
    status,
    headers: cabeceras,
  });

afterEach(() => vi.unstubAllGlobals());

describe("consultarVerificacion", () => {
  it("la clave y el hash van en el cuerpo, sin cookies, caché ni referrer", async () => {
    const fetchFalso = vi.fn().mockResolvedValue(respuesta(200, { hash: FOTO }));
    vi.stubGlobal("fetch", fetchFalso);
    await consultarVerificacion(CLAVE, FOTO);
    const [url, opciones] = fetchFalso.mock.calls[0];
    expect(url).toMatch(/\/verificacion\/$/);
    expect(url).not.toContain(CLAVE);
    expect(url).not.toContain(FOTO);
    expect(opciones.method).toBe("POST");
    expect(JSON.parse(opciones.body)).toEqual({ clave: CLAVE, sha256: FOTO });
    expect(opciones.headers["Content-Type"]).toBe("application/json");
    expect(opciones.headers.Authorization).toBeUndefined();
    expect(opciones.credentials).toBe("omit");
    expect(opciones.cache).toBe("no-store");
    expect(opciones.referrerPolicy).toBe("no-referrer");
  });

  it.each([
    [200, { hash: FOTO }, "ok"],
    [400, { error: "x" }, "formato"],
    [404, { error: "x" }, "no_encontrada"],
    [500, "<html>", "servidor"],
    [503, "<html>", "servidor"],
    [415, {}, "servidor"],
  ])("%i se traduce", async (status, cuerpo, tipo) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(respuesta(status, cuerpo)));
    expect((await consultarVerificacion(CLAVE, FOTO)).tipo).toBe(tipo);
  });

  it("entrega los datos del 200", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(respuesta(200, { hash: FOTO })));
    expect((await consultarVerificacion(CLAVE, FOTO)).datos).toEqual({ hash: FOTO });
  });

  it("un 200 que no es JSON no se da por bueno", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(respuesta(200, "<html>")));
    expect((await consultarVerificacion(CLAVE, FOTO)).tipo).toBe("servidor");
  });

  it.each([
    ["42", 42],
    [undefined, null],
    ["mañana", null],
    ["9".repeat(400), null],
  ])("429 con Retry-After %s", async (cabecera, segundos) => {
    const cabeceras = cabecera ? { "Retry-After": cabecera } : {};
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(respuesta(429, {}, cabeceras)));
    expect(await consultarVerificacion(CLAVE, FOTO)).toEqual({ tipo: "espera", segundos });
  });

  it("sin red", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    expect((await consultarVerificacion(CLAVE, FOTO)).tipo).toBe("red");
  });

  it("agotado el tiempo, aborta y lo dice", async () => {
    vi.useFakeTimers();
    try {
      vi.stubGlobal(
        "fetch",
        vi.fn(
          (_, { signal }) =>
            new Promise((_resolver, rechazar) => {
              signal.addEventListener("abort", () =>
                rechazar(new DOMException("abortado", "AbortError")),
              );
            }),
        ),
      );
      const consulta = consultarVerificacion(CLAVE, FOTO);
      await vi.advanceTimersByTimeAsync(90_000);
      expect((await consulta).tipo).toBe("tiempo");
    } finally {
      vi.useRealTimers();
    }
  });
});
