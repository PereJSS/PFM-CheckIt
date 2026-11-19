// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../services/api", () => ({ default: { post: vi.fn(), patch: vi.fn() } }));
vi.mock("../../services/offline", () => ({
  getPendingEvidencesByInspection: vi.fn().mockResolvedValue([]),
  saveEvidenceOffline: vi.fn().mockResolvedValue(undefined),
}));
vi.mock("../../utils/hash", () => ({
  calculateSHA256: vi.fn().mockResolvedValue("a".repeat(64)),
}));

const { default: api } = await import("../../services/api");
const { saveEvidenceOffline } = await import("../../services/offline");
const { default: InspectionForm } = await import("../inspectionForm");

const irAlPaso2 = () => {
  render(<InspectionForm inspectionId={5} />);
  fireEvent.change(screen.getByRole("combobox"), { target: { value: "Cocina" } });
  fireEvent.click(screen.getByRole("button", { name: /Registrar daño en Cocina/ }));
};

const subirFoto = () => {
  const foto = new File(["x"], "foto.jpg", { type: "image/jpeg" });
  fireEvent.change(document.body.querySelector('input[type="file"]'), {
    target: { files: [foto] },
  });
};

beforeEach(() => vi.clearAllMocks());
afterEach(cleanup);

describe("InspectionForm: descripción y errores de subida", () => {
  it("limita la descripción a 1000 caracteres", () => {
    irAlPaso2();
    expect(screen.getByRole("textbox")).toHaveProperty("maxLength", 1000);
  });

  it("si el servidor rechaza la foto, avisa y no la guarda en el móvil", async () => {
    api.post.mockRejectedValue({
      response: { status: 400, data: { error: "descripcion no puede contener caracteres de control." } },
    });
    irAlPaso2();
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "Grieta" } });
    subirFoto();

    expect(
      await screen.findByText(/descripcion no puede contener caracteres de control/),
    ).toBeTruthy();
    expect(saveEvidenceOffline).not.toHaveBeenCalled();
    // Se queda en el paso 2 con su texto para corregirlo
    expect(screen.getByRole("textbox").value).toBe("Grieta");
  });

  it("sin conexión la guarda en el móvil como hasta ahora", async () => {
    api.post.mockRejectedValue(new Error("Network Error"));
    irAlPaso2();
    subirFoto();

    await waitFor(() => expect(saveEvidenceOffline).toHaveBeenCalledTimes(1));
    expect(await screen.findByText(/guardada en el móvil/)).toBeTruthy();
  });
});
