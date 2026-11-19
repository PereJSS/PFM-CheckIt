// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../services/api", () => ({
  default: { get: vi.fn().mockResolvedValue({ data: [] }), post: vi.fn() },
}));
vi.mock("../../services/offline", () => ({
  syncPendingEvidences: vi.fn(),
  getRejectedEvidences: vi.fn(),
  removeOfflineEvidence: vi.fn(),
  reintentarEvidencia: vi.fn(),
  syncPendingMarcas: vi.fn(),
  EVENTO_BD_BLOQUEADA: "checkit-bd-bloqueada",
}));

const offline = await import("../../services/offline");
const { default: InspectionPage } = await import("../inspeccion");

const rechazada = {
  id: 3,
  inspectionId: 5,
  room: "Cocina",
  motivo: "La inspección ya está completada; no admite más evidencias.",
  rechazada: true,
};
const sinNada = { total: 0, syncedCount: 0, failedCount: 0, rejected: [] };

beforeEach(() => {
  vi.clearAllMocks();
  vi.spyOn(console, "error").mockImplementation(() => {});
  vi.spyOn(window, "confirm").mockReturnValue(true);
  // jsdom dice que hay red; se fija para que no dependa del entorno
  vi.spyOn(navigator, "onLine", "get").mockReturnValue(true);
  offline.syncPendingEvidences.mockResolvedValue(sinNada);
  offline.removeOfflineEvidence.mockResolvedValue(undefined);
  offline.reintentarEvidencia.mockResolvedValue(undefined);
  offline.syncPendingMarcas.mockResolvedValue({ total: 0, syncedCount: 0, failedCount: 0, rejected: [] });
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("InspectionPage: fotos rechazadas por el servidor", () => {
  it("las muestra con la estancia y el motivo en un aviso", async () => {
    offline.getRejectedEvidences.mockResolvedValue([rechazada]);
    render(<InspectionPage />);

    expect(await screen.findByText(/no admite más evidencias/)).toBeTruthy();
    expect(screen.getByText("Cocina")).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toMatch(/no aceptó una foto/);
    expect(screen.getByRole("button", { name: /Descartar la foto 1 de 1 \(Cocina\)/ })).toBeTruthy();
  });

  it("descartar pide confirmación y la borra", async () => {
    offline.getRejectedEvidences.mockResolvedValue([rechazada]);
    render(<InspectionPage />);
    const boton = await screen.findByRole("button", { name: /Descartar/ });

    offline.getRejectedEvidences.mockResolvedValue([]);
    await userEvent.click(boton);

    expect(window.confirm).toHaveBeenCalledWith(expect.stringMatching(/no se podrá recuperar/));
    expect(offline.removeOfflineEvidence).toHaveBeenCalledWith(3);
    await waitFor(() =>
      expect(screen.queryByText(/no admite más evidencias/)).toBeNull(),
    );
  });

  it("si no se confirma, no la borra", async () => {
    window.confirm.mockReturnValue(false);
    offline.getRejectedEvidences.mockResolvedValue([rechazada]);
    render(<InspectionPage />);
    await userEvent.click(await screen.findByRole("button", { name: /Descartar/ }));
    expect(offline.removeOfflineEvidence).not.toHaveBeenCalled();
  });

  it("si no se puede descartar, lo dice y la deja", async () => {
    offline.removeOfflineEvidence.mockRejectedValue(new Error("IndexedDB cerrada"));
    offline.getRejectedEvidences.mockResolvedValue([rechazada]);
    render(<InspectionPage />);
    await userEvent.click(await screen.findByRole("button", { name: /Descartar/ }));

    expect(await screen.findByText(/No se pudo descartar la foto/)).toBeTruthy();
    expect(screen.getByText(/no admite más evidencias/)).toBeTruthy();
  });

  it("reintentar la devuelve a la cola y sincroniza", async () => {
    offline.getRejectedEvidences.mockResolvedValue([rechazada]);
    render(<InspectionPage />);
    const boton = await screen.findByRole("button", { name: /Reintentar la foto 1 de 1/ });
    await waitFor(() => expect(offline.syncPendingEvidences).toHaveBeenCalledTimes(1));

    offline.getRejectedEvidences.mockResolvedValue([]);
    await userEvent.click(boton);

    expect(offline.reintentarEvidencia).toHaveBeenCalledWith(3);
    await waitFor(() => expect(offline.syncPendingEvidences).toHaveBeenCalledTimes(2));
    await waitFor(() =>
      expect(screen.queryByText(/no admite más evidencias/)).toBeNull(),
    );
  });

  it("una sincronización con rechazos muestra el aviso", async () => {
    offline.getRejectedEvidences
      .mockResolvedValueOnce([])
      .mockResolvedValue([rechazada]);
    offline.syncPendingEvidences.mockResolvedValue({
      ...sinNada,
      total: 1,
      rejected: [{ id: 3, inspectionId: 5, room: "Cocina", motivo: rechazada.motivo }],
    });
    render(<InspectionPage />);
    expect(await screen.findByText(/no admite más evidencias/)).toBeTruthy();
  });

  it("si falla la lectura del móvil, avisa en vez de esconder las rechazadas", async () => {
    offline.getRejectedEvidences.mockRejectedValue(new Error("IndexedDB cerrada"));
    render(<InspectionPage />);
    expect(await screen.findByText(/No se pudieron comprobar las fotos guardadas/)).toBeTruthy();
  });

  it("si falla la sincronización, avisa", async () => {
    offline.getRejectedEvidences.mockResolvedValue([]);
    offline.syncPendingEvidences.mockRejectedValue(new Error("IndexedDB cerrada"));
    render(<InspectionPage />);
    expect(await screen.findByText(/No se pudieron sincronizar las fotos guardadas/)).toBeTruthy();
  });

  it("sin rechazadas no muestra el aviso", async () => {
    offline.getRejectedEvidences.mockResolvedValue([]);
    render(<InspectionPage />);
    await waitFor(() => expect(offline.syncPendingEvidences).toHaveBeenCalled());
    await waitFor(() => expect(offline.getRejectedEvidences).toHaveBeenCalled());
    expect(screen.queryByRole("button", { name: /Descartar/ })).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

describe("InspectionPage: marcas del checklist guardadas en el móvil", () => {
  it("las sincroniza y avisa de las que el servidor rechazó", async () => {
    offline.getRejectedEvidences.mockResolvedValue([]);
    offline.syncPendingMarcas.mockResolvedValue({
      total: 1, syncedCount: 0, failedCount: 0,
      rejected: [{ inspectionId: 5, puntoId: 10, motivo: "La inspección ya está completada." }],
    });
    render(<InspectionPage />);
    expect(await screen.findByText(/1 marca del checklist no se aplicó: La inspección ya está completada/)).toBeTruthy();
  });
});

describe("InspectionPage: base del móvil bloqueada por otra pestaña", () => {
  it("avisa de que hay que cerrar la otra pestaña", async () => {
    offline.getRejectedEvidences.mockResolvedValue([]);
    render(<InspectionPage />);
    await waitFor(() => expect(offline.syncPendingEvidences).toHaveBeenCalled());
    window.dispatchEvent(new CustomEvent("checkit-bd-bloqueada"));
    expect(await screen.findByText(/abierto en otra pestaña/)).toBeTruthy();
  });

  it("varios rechazos de marcas se resumen con sus motivos", async () => {
    offline.getRejectedEvidences.mockResolvedValue([]);
    offline.syncPendingMarcas.mockResolvedValue({
      total: 3, syncedCount: 0, failedCount: 0,
      rejected: [
        { inspectionId: 5, puntoId: 1, motivo: "Completada." },
        { inspectionId: 5, puntoId: 2, motivo: "Completada." },
        { inspectionId: 6, puntoId: 3, motivo: "El punto no existe." },
      ],
    });
    render(<InspectionPage />);
    expect(await screen.findByText(/3 marcas del checklist no se aplicaron: Completada\. \/ El punto no existe\./)).toBeTruthy();
  });
});

