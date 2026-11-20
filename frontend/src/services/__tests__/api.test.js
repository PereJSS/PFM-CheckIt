// @vitest-environment jsdom
import axios from "axios";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import api, { apiBaseUrl, REFRESCO_TIMEOUT_MS, salir } from "../api";

// Adaptador falso: 401 con el token caducado, 200 con el renovado.
const adaptador = (config) => {
  if (config.headers.Authorization === "Bearer nuevo") {
    return Promise.resolve({
      data: { ok: config.url },
      status: 200,
      headers: {},
      config,
    });
  }
  const error = new Error("401");
  error.config = config;
  error.response = { status: 401, headers: {}, data: {} };
  return Promise.reject(error);
};

const errorDeRefresco = (status, headers = {}) => {
  const error = new Error(String(status));
  error.response = { status, headers, data: {} };
  return error;
};

beforeEach(() => {
  api.defaults.adapter = adaptador;
  sessionStorage.setItem("access_token", "viejo");
  sessionStorage.setItem("refresh_token", "refresco");
});

afterEach(() => {
  vi.restoreAllMocks();
  sessionStorage.clear();
});

describe("renovación del token en api.js", () => {
  it("varias peticiones con 401 a la vez comparten un único refresco", async () => {
    const post = vi.spyOn(axios, "post").mockResolvedValue({ data: { access: "nuevo" } });
    const resultados = await Promise.all([api.get("/a/"), api.get("/b/"), api.get("/c/")]);
    expect(post).toHaveBeenCalledTimes(1);
    expect(resultados.map((r) => r.data.ok)).toEqual(["/a/", "/b/", "/c/"]);
    expect(sessionStorage.getItem("access_token")).toBe("nuevo");
  });

  it("tras un refresco terminado, el siguiente 401 vuelve a refrescar", async () => {
    const post = vi.spyOn(axios, "post").mockResolvedValue({ data: { access: "nuevo" } });
    await api.get("/a/");
    sessionStorage.setItem("access_token", "viejo");
    await api.get("/b/");
    expect(post).toHaveBeenCalledTimes(2);
  });

  it("un 429 del refresco no cierra la sesión y avisa de esperar", async () => {
    vi.spyOn(axios, "post").mockRejectedValue(errorDeRefresco(429, { "retry-after": "20" }));
    const error = await api.get("/a/").catch((e) => e);
    expect(error.response.status).toBe(429);
    expect(error.message).toMatch(/espera 20 s/);
    expect(sessionStorage.getItem("refresh_token")).toBe("refresco");
  });

  it("un refresh token rechazado cierra la sesión", async () => {
    vi.spyOn(axios, "post").mockRejectedValue(errorDeRefresco(401));
    await api.get("/a/").catch(() => {});
    expect(sessionStorage.getItem("access_token")).toBeNull();
    expect(sessionStorage.getItem("refresh_token")).toBeNull();
  });

  it("sin refresh token cierra la sesión sin intentar refrescar", async () => {
    sessionStorage.removeItem("refresh_token");
    const post = vi.spyOn(axios, "post");
    await api.get("/a/").catch(() => {});
    expect(post).not.toHaveBeenCalled();
    expect(sessionStorage.getItem("access_token")).toBeNull();
  });

  it("un 429 con varias peticiones a la vez: un solo refresco y la sesión intacta", async () => {
    const post = vi.spyOn(axios, "post").mockRejectedValue(errorDeRefresco(429));
    const errores = await Promise.all(
      ["/a/", "/b/", "/c/"].map((u) => api.get(u).catch((e) => e)),
    );
    expect(post).toHaveBeenCalledTimes(1);
    errores.forEach((e) => expect(e.message).toMatch(/espera un momento/));
    expect(sessionStorage.getItem("access_token")).toBe("viejo");
    expect(sessionStorage.getItem("refresh_token")).toBe("refresco");
  });

  it.each([
    ["red caída", (() => new Error("Network Error"))()],
    ["502 de Render arrancando", errorDeRefresco(502)],
  ])("un fallo del refresco por %s no cierra la sesión", async (_caso, fallo) => {
    vi.spyOn(axios, "post").mockRejectedValue(fallo);
    const error = await api.get("/a/").catch((e) => e);
    expect(error.response.status).toBe(401);
    expect(sessionStorage.getItem("refresh_token")).toBe("refresco");
  });

  it("tras un fallo, el siguiente 401 vuelve a intentar el refresco", async () => {
    const post = vi
      .spyOn(axios, "post")
      .mockRejectedValueOnce(errorDeRefresco(502))
      .mockResolvedValueOnce({ data: { access: "nuevo" } });
    await api.get("/a/").catch(() => {});
    const r = await api.get("/b/");
    expect(post).toHaveBeenCalledTimes(2);
    expect(r.data.ok).toBe("/b/");
  });

  it("un 401 que llega tarde reintenta con el token ya renovado, sin refrescar", async () => {
    const post = vi.spyOn(axios, "post");
    const config = { url: "/a/", method: "get", headers: {} };
    api.defaults.adapter = (c) => {
      if (c.headers.Authorization === "Bearer nuevo") return adaptador(c);
      sessionStorage.setItem("access_token", "nuevo"); // otro refresco terminó entretanto
      return adaptador(c);
    };
    const r = await api.request(config);
    expect(post).not.toHaveBeenCalled();
    expect(r.data.ok).toBe("/a/");
  });

  it("si entra otro usuario durante el refresco, no se mezclan sus tokens", async () => {
    let resolver;
    vi.spyOn(axios, "post").mockImplementation(() => new Promise((r) => (resolver = r)));
    const peticionDeA = api.get("/a/").catch((e) => e);
    await vi.waitFor(() => expect(resolver).toBeDefined());
    expect(axios.post).toHaveBeenCalledWith(expect.any(String), { refresh: "refresco" }, { timeout: REFRESCO_TIMEOUT_MS });
    // logout de A y login de B en la misma pestaña
    sessionStorage.setItem("access_token", "acceso-de-B");
    sessionStorage.setItem("refresh_token", "refresco-de-B");
    resolver({ data: { access: "acceso-de-A" } });
    await peticionDeA;
    expect(sessionStorage.getItem("access_token")).toBe("acceso-de-B");
    expect(sessionStorage.getItem("refresh_token")).toBe("refresco-de-B");
  });

  it("si el refresco de A falla tras entrar B, la sesión de B sigue", async () => {
    let rechazar;
    vi.spyOn(axios, "post").mockImplementation(
      () => new Promise((_r, j) => (rechazar = j)),
    );
    const peticionDeA = api.get("/a/").catch((e) => e);
    await vi.waitFor(() => expect(rechazar).toBeDefined());
    sessionStorage.setItem("access_token", "acceso-de-B");
    sessionStorage.setItem("refresh_token", "refresco-de-B");
    rechazar(errorDeRefresco(401));
    await peticionDeA;
    expect(sessionStorage.getItem("refresh_token")).toBe("refresco-de-B");
  });
});

describe("rotación del refresh token", () => {
  it("guarda el refresh nuevo que entrega el refresco", async () => {
    vi.spyOn(axios, "post").mockResolvedValue({ data: { access: "nuevo", refresh: "refresco-2" } });
    await api.get("/a/");
    expect(sessionStorage.getItem("access_token")).toBe("nuevo");
    expect(sessionStorage.getItem("refresh_token")).toBe("refresco-2");
  });

  it("sin refresh en la respuesta conserva el que tenía", async () => {
    vi.spyOn(axios, "post").mockResolvedValue({ data: { access: "nuevo" } });
    await api.get("/a/");
    expect(sessionStorage.getItem("refresh_token")).toBe("refresco");
  });

  it("el siguiente refresco usa el refresh rotado", async () => {
    const post = vi.spyOn(axios, "post")
      .mockResolvedValueOnce({ data: { access: "nuevo", refresh: "refresco-2" } })
      .mockResolvedValueOnce({ data: { access: "nuevo", refresh: "refresco-3" } });
    await api.get("/a/");
    sessionStorage.setItem("access_token", "viejo");
    await api.get("/b/");
    expect(post).toHaveBeenLastCalledWith(expect.any(String), { refresh: "refresco-2" }, expect.anything());
    expect(sessionStorage.getItem("refresh_token")).toBe("refresco-3");
  });

  it("si entra otro usuario durante el refresco, no guarda el refresh rotado de A", async () => {
    let resolver;
    vi.spyOn(axios, "post").mockImplementation(() => new Promise((r) => (resolver = r)));
    const peticionDeA = api.get("/a/").catch((e) => e);
    await vi.waitFor(() => expect(resolver).toBeDefined());
    sessionStorage.setItem("access_token", "acceso-de-B");
    sessionStorage.setItem("refresh_token", "refresco-de-B");
    resolver({ data: { access: "acceso-de-A", refresh: "refresco-de-A-2" } });
    await peticionDeA;
    expect(sessionStorage.getItem("refresh_token")).toBe("refresco-de-B");
  });
});

describe("salir", () => {
  let ubicacion;
  beforeEach(() => {
    ubicacion = { href: "" };
    vi.spyOn(window, "location", "get").mockReturnValue(ubicacion);
    vi.spyOn(console, "warn").mockImplementation(() => {});
  });

  it("anula el refresh en el servidor y borra la sesión local", () => {
    const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("{}"));
    salir();
    expect(fetch).toHaveBeenCalledTimes(1);
    const [url, opciones] = fetch.mock.calls[0];
    expect(url).toBe(`${apiBaseUrl}/auth/logout/`);
    // keepalive: la petición sobrevive a la navegación a /login
    expect(opciones).toMatchObject({ method: "POST", keepalive: true });
    expect(JSON.parse(opciones.body)).toEqual({ refresh: "refresco" });
    expect(sessionStorage.getItem("access_token")).toBeNull();
    expect(sessionStorage.getItem("refresh_token")).toBeNull();
    expect(ubicacion.href).toBe("/login");
    expect(console.warn).not.toHaveBeenCalled();
  });

  it("borra la sesión local aunque el servidor no responda, y lo avisa en consola", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("Failed to fetch"));
    salir();
    expect(sessionStorage.getItem("refresh_token")).toBeNull();
    expect(ubicacion.href).toBe("/login");
    await vi.waitFor(() => expect(console.warn).toHaveBeenCalledTimes(1));
  });

  it("un 429 del logout también se avisa en consola", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("{}", { status: 429 }));
    salir();
    await vi.waitFor(() => expect(console.warn).toHaveBeenCalledWith(expect.any(String), 429));
  });

  it("sin refresh token no llama al servidor", () => {
    sessionStorage.removeItem("refresh_token");
    const fetch = vi.spyOn(globalThis, "fetch");
    salir();
    expect(fetch).not.toHaveBeenCalled();
    expect(sessionStorage.getItem("access_token")).toBeNull();
  });
});

describe("tope del refresco", () => {
  it("un refresco que no responde a tiempo no cierra la sesión", async () => {
    const error = new Error("timeout of 15000ms exceeded");
    error.code = "ECONNABORTED";
    vi.spyOn(axios, "post").mockRejectedValue(error);
    await api.get("/a/").catch(() => {});
    expect(axios.post).toHaveBeenCalledWith(expect.any(String), { refresh: "refresco" }, { timeout: REFRESCO_TIMEOUT_MS });
    expect(sessionStorage.getItem("refresh_token")).toBe("refresco");
  });
});
