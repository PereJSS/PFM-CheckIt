import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}));

const { default: api } = await import("../api");
const ck = await import("../checklists");

const checklist = {
  tiene_checklist: true,
  estancias: [
    {
      id: 1,
      nombre: "Cocina",
      puntos: [
        { id: 10, texto: "Encimera", requiere_foto: false, resultado: "DANO", evidencias: [] },
        { id: 11, texto: "Vista general", requiere_foto: true, resultado: "OK", evidencias: [5] },
        { id: 12, texto: "Horno", requiere_foto: false, resultado: null, evidencias: [] },
      ],
    },
    {
      id: 2,
      nombre: "Baño",
      puntos: [{ id: 20, texto: "Grifo", requiere_foto: true, resultado: "NO_APLICA", evidencias: [] }],
    },
  ],
};

beforeEach(() => vi.clearAllMocks());

describe("faltasLocales", () => {
  it("igual que el servidor: sin marcar y daños o fotos obligatorias sin foto", () => {
    expect(ck.faltasLocales(checklist)).toEqual([
      { punto: 10, estancia: "Cocina", texto: "Encimera", motivo: "falta_foto" },
      { punto: 12, estancia: "Cocina", texto: "Horno", motivo: "sin_marcar" },
    ]);
  });

  it("cuenta las fotos guardadas en el móvil para ese punto", () => {
    expect(ck.faltasLocales(checklist, { 10: 1 }).map((f) => f.punto)).toEqual([12]);
  });

  it("sin checklist no falta nada", () => {
    expect(ck.faltasLocales({ tiene_checklist: false })).toEqual([]);
    expect(ck.faltasLocales(null)).toEqual([]);
  });
});

describe("progreso", () => {
  it("marcados sobre total por estancia", () => {
    expect(ck.progreso(checklist.estancias[0])).toEqual({ marcados: 2, total: 3 });
  });
});

describe("textoFaltas", () => {
  it("frase legible para el operario", () => {
    expect(
      ck.textoFaltas([
        { estancia: "Cocina", texto: "Encimera", motivo: "falta_foto" },
        { estancia: "Cocina", texto: "Horno", motivo: "sin_marcar" },
      ]),
    ).toBe("Cocina · Encimera (falta foto); Cocina · Horno (sin marcar)");
  });

  it("resume a partir de 5", () => {
    const muchas = Array.from({ length: 7 }, (_, i) => ({ estancia: "E", texto: `P${i}`, motivo: "sin_marcar" }));
    expect(ck.textoFaltas(muchas)).toMatch(/y 2 más$/);
  });
});

describe("llamadas a la API", () => {
  it("usan las rutas del backend", async () => {
    api.get.mockResolvedValue({ data: [] });
    api.post.mockResolvedValue({ data: {} });
    api.patch.mockResolvedValue({ data: {} });
    api.delete.mockResolvedValue({});

    await ck.listarPlantillas();
    await ck.crearPlantilla({ nombre: "X" });
    await ck.guardarPlantilla(3, { nombre: "Y", version: 2 });
    await ck.borrarPlantilla(3);
    await ck.duplicarPlantilla(3);
    await ck.obtenerChecklist(7);
    await ck.aplicarChecklist(7, { plantilla: 3 });
    await ck.marcarPunto(7, 10, { resultado: "OK" });

    expect(api.get.mock.calls.map((c) => c[0])).toEqual(["/plantillas-checklist/", "/inspecciones/7/checklist/"]);
    expect(api.post.mock.calls.map((c) => c[0])).toEqual([
      "/plantillas-checklist/",
      "/plantillas-checklist/3/duplicar/",
      "/inspecciones/7/checklist/",
    ]);
    expect(api.patch.mock.calls).toEqual([
      ["/plantillas-checklist/3/", { nombre: "Y", version: 2 }],
      ["/inspecciones/7/checklist/puntos/10/", { resultado: "OK" }],
    ]);
    expect(api.delete).toHaveBeenCalledWith("/plantillas-checklist/3/");
  });
});

describe("superponerMarcas", () => {
  it("pinta encima las marcas pendientes del móvil", () => {
    const r = ck.superponerMarcas(checklist, [{ puntoId: 12, resultado: "OK", nota: "n" }]);
    expect(r.estancias[0].puntos[2]).toMatchObject({ id: 12, resultado: "OK", nota: "n" });
    expect(r.estancias[0].puntos[0].resultado).toBe("DANO");
    expect(ck.superponerMarcas(checklist, [])).toBe(checklist);
  });
});

