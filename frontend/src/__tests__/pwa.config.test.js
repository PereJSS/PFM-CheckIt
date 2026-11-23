// @vitest-environment node
// La PWA (#15) solo guarda la aplicación: nunca respuestas de la API ni tokens.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { opcionesPWA } from "../../pwa.config.js";

const html = readFileSync(new URL("../../index.html", import.meta.url), "utf8");

describe("opciones de la PWA", () => {
  it("pide permiso para actualizar en vez de recargar sola", () => {
    expect(opcionesPWA.registerType).toBe("prompt");
  });

  it("la versión nueva espera al botón, pero el primer service worker controla la página", () => {
    // Sin clientsClaim, una pestaña abierta desde la primera visita nunca queda controlada
    // y «Actualizar» no recargaría (no llega el evento controlling)
    expect(opcionesPWA.workbox.clientsClaim).toBe(true);
    expect(opcionesPWA.workbox.skipWaiting).not.toBe(true);
  });

  it("no guarda en caché nada en tiempo de ejecución", () => {
    expect(opcionesPWA.workbox.runtimeCaching).toBeUndefined();
    expect(opcionesPWA.strategies ?? "generateSW").toBe("generateSW");
  });

  it("no sirve la aplicación en lugar de una ruta de la API", () => {
    const excluida = (ruta) =>
      opcionesPWA.workbox.navigateFallbackDenylist.some((re) => re.test(ruta));
    expect(excluida("/api/v1/auth/login/")).toBe(true);
    expect(excluida("/api")).toBe(true);
    expect(excluida("/operario")).toBe(false);
    expect(excluida("/verificar")).toBe(false);
  });

  it("solo precarga archivos de la aplicación", () => {
    expect(opcionesPWA.workbox.globPatterns).toEqual([
      "**/*.{js,css,html,svg,png,ico,woff2}",
    ]);
    expect(opcionesPWA.workbox.cleanupOutdatedCaches).toBe(true);
  });

  it("tiene un manifiesto instalable en español", () => {
    const m = opcionesPWA.manifest;
    expect(m.name).toBe("CheckIt");
    expect(m.short_name).toBe("CheckIt");
    expect(m.lang).toBe("es");
    expect(m.display).toBe("standalone");
    expect(m.start_url).toBe("/");
    const tamaños = m.icons.map((i) => `${i.sizes}${i.purpose ? ` ${i.purpose}` : ""}`);
    expect(tamaños).toEqual(expect.arrayContaining(["192x192", "512x512", "512x512 maskable"]));
    expect(m.icons.some((i) => i.purpose === "any maskable")).toBe(false);
  });

  it("el index.html se llama CheckIt y su color coincide con el manifiesto", () => {
    expect(html).toMatch(/<title>CheckIt<\/title>/);
    expect(html).toContain(`<meta name="theme-color" content="${opcionesPWA.manifest.theme_color}"`);
    expect(html).toMatch(/<link rel="apple-touch-icon" href="\/apple-touch-icon-180x180.png"/);
    expect(html).toMatch(/<meta name="description" content="[^"]+"/);
  });
});

describe("cabeceras de Vercel", () => {
  const vercel = JSON.parse(readFileSync(new URL("../../vercel.json", import.meta.url), "utf8"));
  const cabecera = (ruta) =>
    vercel.headers?.find((h) => h.source === ruta)?.headers.find((x) => x.key === "Cache-Control")?.value;

  it("sw.js y el manifiesto no se guardan en caché HTTP", () => {
    expect(cabecera("/sw.js")).toMatch(/no-cache/);
    expect(cabecera("/manifest.webmanifest")).toMatch(/no-cache/);
  });
});
