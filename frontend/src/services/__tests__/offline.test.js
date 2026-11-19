import { beforeEach, describe, expect, it, vi } from "vitest";

// IndexedDB en memoria: jsdom no la trae y basta con lo que usa offline.js
const almacen = new Map();
let siguienteId = 1;
let putFalla = false;
vi.mock("idb", () => ({
  openDB: async () => ({
    add: async (_s, valor) => {
      const id = siguienteId++;
      almacen.set(id, { ...valor, id });
      return id;
    },
    get: async (_s, id) => (almacen.has(id) ? { ...almacen.get(id) } : undefined),
    put: async (_s, valor) => {
      if (putFalla) throw new Error("QuotaExceededError");
      almacen.set(valor.id, { ...valor });
      return valor.id;
    },
    getAll: async () => [...almacen.values()].map((v) => ({ ...v })),
    delete: async (_s, id) => {
      almacen.delete(id);
    },
  }),
}));

const {
  getAllPendingEvidences,
  getPendingEvidencesByInspection,
  getRejectedEvidences,
  reintentarEvidencia,
  removeOfflineEvidence,
  saveEvidenceOffline,
  syncPendingEvidences,
} = await import("../offline");

const rechazo = (status, error) => {
  const e = new Error(`HTTP ${status}`);
  e.response = { status, data: { error } };
  return e;
};

beforeEach(() => {
  almacen.clear();
  siguienteId = 1;
  putFalla = false;
});

describe("syncPendingEvidences", () => {
  it("sube y borra las que el servidor acepta", async () => {
    await saveEvidenceOffline({ inspectionId: 1, room: "Cocina" });
    const subir = vi.fn().mockResolvedValue({});
    const r = await syncPendingEvidences(subir);
    expect(r).toMatchObject({ total: 1, syncedCount: 1, failedCount: 0, rejected: [] });
    expect(almacen.size).toBe(0);
  });

  it.each([
    ["sin red", new Error("Network Error")],
    ["con un 503", rechazo(503, "Mantenimiento")],
    ["con un 429", rechazo(429, "Demasiadas peticiones")],
  ])("%s la deja pendiente para reintentar", async (_caso, error) => {
    await saveEvidenceOffline({ inspectionId: 1, room: "Cocina" });
    const r = await syncPendingEvidences(vi.fn().mockRejectedValue(error));
    expect(r).toMatchObject({ failedCount: 1, rejected: [] });
    expect(await getAllPendingEvidences()).toHaveLength(1);
  });

  it("un rechazo definitivo la aparta con el motivo, sin borrar la foto", async () => {
    await saveEvidenceOffline({ inspectionId: 7, room: "Baño Principal", file: "foto" });
    const subir = vi
      .fn()
      .mockRejectedValue(rechazo(400, "descripcion no puede contener caracteres de control."));

    const r = await syncPendingEvidences(subir);

    expect(r.failedCount).toBe(0);
    expect(r.rejected).toEqual([
      {
        id: 1,
        inspectionId: 7,
        room: "Baño Principal",
        motivo: "descripcion no puede contener caracteres de control.",
      },
    ]);
    // Ni se reintenta ni bloquea finalizar la inspección
    expect(await getAllPendingEvidences()).toEqual([]);
    expect(await getPendingEvidencesByInspection(7)).toEqual([]);
    // Pero la foto sigue en el móvil hasta que el operario la descarte
    const apartadas = await getRejectedEvidences();
    expect(apartadas).toHaveLength(1);
    expect(apartadas[0]).toMatchObject({ file: "foto", rechazada: true });

    // Una pendiente nueva se sube; la apartada no se vuelve a intentar
    await saveEvidenceOffline({ inspectionId: 7, room: "Cocina" });
    subir.mockReset().mockResolvedValue({});
    await syncPendingEvidences(subir);
    expect(subir).toHaveBeenCalledTimes(1);
    expect(subir.mock.calls[0][0].room).toBe("Cocina");

    await removeOfflineEvidence(1);
    expect(await getRejectedEvidences()).toEqual([]);
  });

  it("sigue con las demás tras un rechazo", async () => {
    await saveEvidenceOffline({ inspectionId: 1, room: "A" });
    await saveEvidenceOffline({ inspectionId: 1, room: "B" });
    const subir = vi
      .fn()
      .mockRejectedValueOnce(rechazo(409, "La inspección ya está completada."))
      .mockResolvedValueOnce({});
    const r = await syncPendingEvidences(subir);
    expect(r.syncedCount).toBe(1);
    expect(r.rejected.map((x) => x.room)).toEqual(["A"]);
  });

  it("si no puede apartarla, la cuenta como fallida y sigue con las demás", async () => {
    await saveEvidenceOffline({ inspectionId: 1, room: "A" });
    await saveEvidenceOffline({ inspectionId: 1, room: "B" });
    putFalla = true;
    const subir = vi
      .fn()
      .mockRejectedValueOnce(rechazo(400, "No válida."))
      .mockResolvedValueOnce({});
    const r = await syncPendingEvidences(subir);
    expect(r).toMatchObject({ syncedCount: 1, failedCount: 1, rejected: [] });
    expect(await getAllPendingEvidences()).toHaveLength(1);
  });

  it("no resucita una foto descartada mientras se subía", async () => {
    await saveEvidenceOffline({ inspectionId: 1, room: "A" });
    const subir = vi.fn(async () => {
      await removeOfflineEvidence(1);
      throw rechazo(400, "No válida.");
    });
    const r = await syncPendingEvidences(subir);
    expect(r.rejected).toEqual([]);
    expect(almacen.size).toBe(0);
  });

  it("dos sincronizaciones a la vez no suben dos veces la misma foto", async () => {
    await saveEvidenceOffline({ inspectionId: 1, room: "A" });
    const subir = vi.fn().mockResolvedValue({});
    const [a, b] = await Promise.all([
      syncPendingEvidences(subir),
      syncPendingEvidences(subir),
    ]);
    expect(subir).toHaveBeenCalledTimes(1);
    expect(a).toEqual(b);
    // Terminada la primera, se puede volver a sincronizar
    await saveEvidenceOffline({ inspectionId: 1, room: "B" });
    await syncPendingEvidences(subir);
    expect(subir).toHaveBeenCalledTimes(2);
  });
});

describe("reintentarEvidencia", () => {
  it("devuelve una rechazada a la cola de pendientes", async () => {
    await saveEvidenceOffline({ inspectionId: 1, room: "A" });
    await syncPendingEvidences(vi.fn().mockRejectedValue(rechazo(404, "No encontrada.")));
    expect(await getAllPendingEvidences()).toEqual([]);

    await reintentarEvidencia(1);

    const pendientes = await getAllPendingEvidences();
    expect(pendientes).toHaveLength(1);
    expect(pendientes[0]).not.toHaveProperty("rechazada");
    expect(pendientes[0]).not.toHaveProperty("motivo");
    expect(await getRejectedEvidences()).toEqual([]);
  });

  it("no hace nada si ya no existe", async () => {
    await reintentarEvidencia(99);
    expect(almacen.size).toBe(0);
  });
});
