// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../../services/checklists", () => ({
  listarPlantillas: vi.fn(),
  crearPlantilla: vi.fn(),
  guardarPlantilla: vi.fn(),
  borrarPlantilla: vi.fn(),
  duplicarPlantilla: vi.fn(),
}));

const ck = await import("../../../services/checklists");
const { default: PlantillasPanel } = await import("../PlantillasPanel");

const VIVIENDA = {
  id: 3, nombre: "Vivienda", descripcion: "", version: 2, por_defecto: true, propiedades_count: 4,
  estructura: { estancias: [{ nombre: "Cocina", puntos: [{ texto: "A", requiere_foto: false }, { texto: "B", requiere_foto: false }] }] },
};
const COCHE = { ...VIVIENDA, id: 4, nombre: "Coche", por_defecto: false, propiedades_count: 0 };

beforeEach(() => {
  vi.clearAllMocks();
  ck.listarPlantillas.mockResolvedValue([VIVIENDA, COCHE]);
  vi.spyOn(window, "confirm").mockReturnValue(true);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const fila = async (nombre) => within((await screen.findByText(nombre)).closest("li"));

describe("PlantillasPanel", () => {
  it("lista las plantillas con sus puntos, uso y la por defecto", async () => {
    render(<PlantillasPanel />);
    const vivienda = await fila("Vivienda");
    expect(vivienda.getByText("Por defecto")).toBeTruthy();
    expect(vivienda.getByText(/2 puntos · 4 propiedades/)).toBeTruthy();
    expect((await fila("Coche")).queryByText("Por defecto")).toBeNull();
  });

  it("crea una desde un modelo", async () => {
    render(<PlantillasPanel />);
    await userEvent.click(await screen.findByRole("button", { name: "Nueva plantilla" }));
    await userEvent.click(screen.getByRole("button", { name: /Vehículo/ }));
    expect(screen.getByLabelText("Nombre de la plantilla").value).toBe("Vehículo");
    expect(screen.getByLabelText("Nombre de la estancia 1").value).toBe("Exterior");
  });

  it("duplicar y marcar por defecto recargan la lista", async () => {
    ck.duplicarPlantilla.mockResolvedValue({});
    ck.guardarPlantilla.mockResolvedValue({});
    render(<PlantillasPanel />);
    await userEvent.click((await fila("Coche")).getByRole("button", { name: /^Duplicar/ }));
    expect(ck.duplicarPlantilla).toHaveBeenCalledWith(4);
    await userEvent.click((await fila("Coche")).getByRole("button", { name: /^Marcar por defecto/ }));
    expect(ck.guardarPlantilla).toHaveBeenCalledWith(4, { por_defecto: true, version: 2 });
    await waitFor(() => expect(ck.listarPlantillas).toHaveBeenCalledTimes(3));
  });

  it("borrar avisa de las propiedades que la usan", async () => {
    ck.borrarPlantilla.mockResolvedValue({});
    render(<PlantillasPanel />);
    await userEvent.click((await fila("Vivienda")).getByRole("button", { name: /^Borrar/ }));
    expect(window.confirm).toHaveBeenCalledWith(expect.stringMatching(/4 propiedades/));
    expect(ck.borrarPlantilla).toHaveBeenCalledWith(3);
  });

  it("si no se confirma, no borra", async () => {
    window.confirm.mockReturnValue(false);
    render(<PlantillasPanel />);
    await userEvent.click((await fila("Coche")).getByRole("button", { name: /^Borrar/ }));
    expect(ck.borrarPlantilla).not.toHaveBeenCalled();
  });

  it("si falla la carga lo dice", async () => {
    ck.listarPlantillas.mockRejectedValue(new Error("x"));
    render(<PlantillasPanel />);
    expect(await screen.findByText(/No se pudieron cargar las plantillas/)).toBeTruthy();
  });
});

describe("PlantillasPanel: avisos", () => {
  it("confirma lo hecho", async () => {
    ck.duplicarPlantilla.mockResolvedValue({});
    render(<PlantillasPanel />);
    await userEvent.click((await fila("Coche")).getByRole("button", { name: /^Duplicar/ }));
    expect(await screen.findByText(/Plantilla «Coche» duplicada/)).toBeTruthy();
  });

  it("un 500 no dice «rechazó la foto»", async () => {
    ck.borrarPlantilla.mockRejectedValue({ response: { status: 500, data: "<html>" } });
    render(<PlantillasPanel />);
    await userEvent.click((await fila("Coche")).getByRole("button", { name: /^Borrar/ }));
    const aviso = await screen.findByText(/No se pudo completar la acción/);
    expect(aviso.textContent).not.toMatch(/foto/);
  });
});

describe("PlantillasPanel: la primera plantilla", () => {
  it("no se marca por defecto sin pedirlo", async () => {
    ck.listarPlantillas.mockResolvedValue([]);
    render(<PlantillasPanel />);
    await userEvent.click(await screen.findByRole("button", { name: "Nueva plantilla" }));
    await userEvent.click(screen.getByRole("button", { name: /^Vivienda/ }));
    expect(screen.getByLabelText("Plantilla por defecto del cliente").checked).toBe(false);
    ck.crearPlantilla.mockResolvedValue({ id: 9, nombre: "Pisos", por_defecto: false });
    await userEvent.type(screen.getByLabelText("Nombre de la plantilla"), "Pisos");
    await userEvent.click(screen.getByRole("button", { name: "Guardar plantilla" }));
    await waitFor(() => expect(ck.crearPlantilla).toHaveBeenCalledTimes(1));
    expect(ck.crearPlantilla).toHaveBeenCalledWith(expect.objectContaining({ por_defecto: false }));
  });

  it("se envía por defecto si el admin lo marca", async () => {
    ck.listarPlantillas.mockResolvedValue([]);
    ck.crearPlantilla.mockResolvedValue({ id: 9, nombre: "Pisos", por_defecto: true });
    render(<PlantillasPanel />);
    await userEvent.click(await screen.findByRole("button", { name: "Nueva plantilla" }));
    await userEvent.click(screen.getByRole("button", { name: /^Vivienda/ }));
    await userEvent.type(screen.getByLabelText("Nombre de la plantilla"), "Pisos");
    await userEvent.click(screen.getByLabelText("Plantilla por defecto del cliente"));
    await userEvent.click(screen.getByRole("button", { name: "Guardar plantilla" }));
    await waitFor(() => expect(ck.crearPlantilla).toHaveBeenCalledWith(expect.objectContaining({ por_defecto: true })));
  });
});

