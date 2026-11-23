// @vitest-environment node
// Construye la app y revisa el service worker generado: solo la aplicación precargada,
// ninguna ruta de la API y ninguna estrategia de caché en tiempo de ejecución.
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { build } from "vite";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

const raiz = fileURLToPath(new URL("../..", import.meta.url));
let salida;
let sw;

beforeAll(async () => {
  salida = mkdtempSync(join(tmpdir(), "checkit-pwa-"));
  await build({ root: raiz, logLevel: "silent", build: { outDir: salida, emptyOutDir: true } });
  // Sin espacios: el sw.js sale minificado o no según el modo en que se construya
  sw = readFileSync(join(salida, "sw.js"), "utf8").replace(/\s+/g, "");
}, 120_000);

afterAll(() => {
  if (salida) rmSync(salida, { recursive: true, force: true });
});

describe("service worker construido", () => {
  it("precarga solo archivos de la aplicación que existen", () => {
    const urls = [...sw.matchAll(/"?url"?:"([^"]+)"/g)].map((m) => m[1]);
    expect(urls).toContain("index.html");
    expect(urls.length).toBeGreaterThan(3);
    expect(new Set(urls).size).toBe(urls.length);
    for (const url of urls) {
      expect(url).not.toMatch(/api/i);
      expect(existsSync(join(salida, url))).toBe(true);
    }
  });

  it("no registra rutas de caché aparte de la navegación", () => {
    expect(sw).not.toMatch(/NetworkFirst|CacheFirst|StaleWhileRevalidate|NetworkOnly/);
    // La única ruta registrada es la de navegación, con /api fuera
    expect(sw.match(/registerRoute\(/g)).toHaveLength(1);
    expect(sw).toMatch(/NavigationRoute/);
    expect(sw).toContain("denylist:[/^\\/api(\\/|$)/]");
  });

  it("el primer service worker controla la pestaña y la versión nueva espera al botón", () => {
    expect(sw).toMatch(/clientsClaim\(\)/);
    expect(sw).toMatch(/cleanupOutdatedCaches\(\)/);
    // skipWaiting solo dentro del manejador del mensaje SKIP_WAITING que manda «Actualizar»
    const llamadas = sw.match(/skipWaiting\(\)/g) ?? [];
    expect(llamadas).toHaveLength(1);
    expect(sw).toMatch(/SKIP_WAITING[^}]*skipWaiting\(\)/);
  });

  it("publica un manifiesto con iconos que existen", () => {
    const manifiesto = JSON.parse(readFileSync(join(salida, "manifest.webmanifest"), "utf8"));
    expect(manifiesto.name).toBe("CheckIt");
    for (const icono of manifiesto.icons) {
      expect(existsSync(join(salida, icono.src))).toBe(true);
    }
  });
});
