import { beforeEach, describe, expect, it, vi } from "vitest";

// IndexedDB en memoria con un almacén por store; las marcas usan clave compuesta
const stores = new Map();
const clave = (store, valor) =>
  store === "marcas" ? `${valor.inspectionId}:${valor.puntoId}` : valor.inspectionId;
const deStore = (nombre) => {
  if (!stores.has(nombre)) stores.set(nombre, new Map());
  return stores.get(nombre);
};
const claveDe = (k) => (Array.isArray(k) ? k.join(":") : k);
const operaciones = (store) => ({
  put: async (valor) => {
    deStore(store).set(clave(store, valor), { ...valor });
  },
  get: async (k) => {
    const v = deStore(store).get(claveDe(k));
    return v ? { ...v } : undefined;
  },
  getAll: async () => [...deStore(store).values()].map((v) => ({ ...v })),
  delete: async (k) => {
    deStore(store).delete(claveDe(k));
  },
});
vi.mock("idb", () => ({
  openDB: () =>
    Promise.resolve({
      put: (store, v) => operaciones(store).put(v),
      get: (store, k) => operaciones(store).get(k),
      getAll: (store) => operaciones(store).getAll(),
      delete: (store, k) => operaciones(store).delete(k),
      transaction: (store) => ({ store: operaciones(store), done: Promise.resolve() }),
    }),
}));

const {
  guardarMarcaOffline,
  getMarcasPendientes,
  quitarMarcaOffline,
  syncPendingMarcas,
  guardarChecklistCache,
  leerChecklistCache,
} = await import("../offline");

const rechazo = (status, error) => {
  const e = new Error(`HTTP ${status}`);
  e.response = { status, data: { error } };
  return e;
};

beforeEach(() => stores.clear());

describe("marcas sin conexión", () => {
  it("la última marca de un punto pisa a la anterior", async () => {
    await guardarMarcaOffline({ inspectionId: 7, puntoId: 10, resultado: "OK", marcadoEnDispositivo: "a" });
    await guardarMarcaOffline({ inspectionId: 7, puntoId: 10, resultado: "DANO", marcadoEnDispositivo: "b" });
    await guardarMarcaOffline({ inspectionId: 8, puntoId: 3, resultado: "OK", marcadoEnDispositivo: "c" });
    expect((await getMarcasPendientes(7)).map((m) => m.resultado)).toEqual(["DANO"]);
    expect(await getMarcasPendientes()).toHaveLength(2);
  });

  it("sube las pendientes y las borra", async () => {
    await guardarMarcaOffline({ inspectionId: 7, puntoId: 10, resultado: "OK", nota: "n", marcadoEnDispositivo: "a" });
    const subir = vi.fn().mockResolvedValue({});
    const r = await syncPendingMarcas(subir);
    expect(subir).toHaveBeenCalledWith(expect.objectContaining({ inspectionId: 7, puntoId: 10, resultado: "OK", nota: "n" }));
    expect(r).toMatchObject({ syncedCount: 1, failedCount: 0, rejected: [] });
    expect(await getMarcasPendientes()).toEqual([]);
  });

  it("sin red la deja para reintentar", async () => {
    await guardarMarcaOffline({ inspectionId: 7, puntoId: 10, resultado: "OK", marcadoEnDispositivo: "a" });
    const r = await syncPendingMarcas(vi.fn().mockRejectedValue(new Error("Network Error")));
    expect(r.failedCount).toBe(1);
    expect(await getMarcasPendientes()).toHaveLength(1);
  });

  it("un rechazo definitivo la quita y dice el motivo", async () => {
    await guardarMarcaOffline({ inspectionId: 7, puntoId: 10, resultado: "OK", marcadoEnDispositivo: "a" });
    const r = await syncPendingMarcas(vi.fn().mockRejectedValue(rechazo(409, "La inspección ya está completada.")));
    expect(r.rejected).toEqual([{ inspectionId: 7, puntoId: 10, motivo: "La inspección ya está completada." }]);
    expect(await getMarcasPendientes()).toEqual([]);
  });

  it("no borra una marca más nueva hecha mientras se subía la anterior", async () => {
    await guardarMarcaOffline({ inspectionId: 7, puntoId: 10, resultado: "OK", marcadoEnDispositivo: "a" });
    const subir = vi.fn(async () => {
      await guardarMarcaOffline({ inspectionId: 7, puntoId: 10, resultado: "DANO", marcadoEnDispositivo: "b" });
    });
    await syncPendingMarcas(subir);
    expect((await getMarcasPendientes(7)).map((m) => m.resultado)).toEqual(["DANO"]);
  });
});

describe("orden de las marcas", () => {
  it("una marca más antigua que llega después no pisa la nueva", async () => {
    await guardarMarcaOffline({ inspectionId: 7, puntoId: 10, resultado: "DANO", marcadoEnDispositivo: "2026-11-19T10:05:00Z" });
    await guardarMarcaOffline({ inspectionId: 7, puntoId: 10, resultado: "OK", marcadoEnDispositivo: "2026-11-19T10:00:00Z" });
    expect((await getMarcasPendientes(7)).map((m) => m.resultado)).toEqual(["DANO"]);
  });

  it("una marca subida en línea quita la pendiente anterior, no una posterior", async () => {
    await guardarMarcaOffline({ inspectionId: 7, puntoId: 10, resultado: "OK", marcadoEnDispositivo: "2026-11-19T10:00:00Z" });
    await quitarMarcaOffline(7, 10, "2026-11-19T09:00:00Z");
    expect(await getMarcasPendientes(7)).toHaveLength(1);
    await quitarMarcaOffline(7, 10, "2026-11-19T10:01:00Z");
    expect(await getMarcasPendientes(7)).toEqual([]);
  });
});

describe("caché del checklist", () => {
  it("guarda y lee el último checklist de una inspección", async () => {
    await guardarChecklistCache(7, { tiene_checklist: true, estancias: [] });
    expect(await leerChecklistCache(7)).toEqual({ tiene_checklist: true, estancias: [] });
    expect(await leerChecklistCache(8)).toBeNull();
  });
});
