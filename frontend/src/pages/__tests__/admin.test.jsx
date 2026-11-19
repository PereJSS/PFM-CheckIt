// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../services/api", () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}));
// El panel de integraciones hace sus propias llamadas: no interesa aquí
vi.mock("../../components/integraciones/IntegracionesPanel", () => ({ default: () => null }));

const { default: api } = await import("../../services/api");
const { default: AdminPage } = await import("../admin");

const VIVIENDA = {
  id: 3, nombre: "Vivienda", version: 1, por_defecto: true, propiedades_count: 1, descripcion: "",
  estructura: { estancias: [{ nombre: "Cocina", puntos: [{ texto: "A", requiere_foto: false }] }] },
};
const COCHE = { ...VIVIENDA, id: 4, nombre: "Coche", por_defecto: false, propiedades_count: 0 };
const PISO = {
  id: 1, nombre: "Piso", direccion: "C1", descripcion: "", external_id: null,
  plantilla_checklist: null, plantilla_efectiva: { id: 3, nombre: "Vivienda", origen: "defecto" },
};
const CON = {
  id: 7, propiedad: 1, propiedad_nombre: "Piso", operario_nombre: "op", estado: "EN_PROGRESO",
  fecha_creacion: "2026-11-19T10:00:00Z", checklist_resumen: { puntos: 3, ok: 0, danos: 1, no_aplica: 0, sin_marcar: 2 },
};
const SIN = { ...CON, id: 8, checklist_resumen: null };

const RUTAS = {
  "/inspecciones/": [CON, SIN],
  "/propiedades/": [PISO],
  "/usuarios/operarios/": [],
  "/plantillas-checklist/": [VIVIENDA, COCHE],
};

beforeEach(() => {
  vi.clearAllMocks();
  api.get.mockImplementation((url) => Promise.resolve({ data: RUTAS[url] ?? [] }));
  api.post.mockResolvedValue({ data: {} });
});
afterEach(cleanup);

describe("AdminPage: checklists", () => {
  it("cada inspección muestra su checklist y se puede aplicar a las que no lo tienen", async () => {
    render(<AdminPage />);
    const fila = (await screen.findByText("#7")).closest("tr");
    expect(within(fila).getByText(/1 de 3 puntos · 1 daño/)).toBeTruthy();
    const otra = screen.getByText("#8").closest("tr");
    await userEvent.click(within(otra).getByRole("button", { name: /^Aplicar checklist/ }));
    expect(api.post).toHaveBeenCalledWith("/inspecciones/8/checklist/", {});
    await waitFor(() => expect(api.get.mock.calls.filter((c) => c[0] === "/inspecciones/").length).toBe(2));
  });

  it("si no se puede aplicar, dice por qué", async () => {
    api.post.mockRejectedValue({ response: { status: 400, data: { error: "La propiedad no tiene plantilla de checklist." } } });
    render(<AdminPage />);
    const otra = (await screen.findByText("#8")).closest("tr");
    await userEvent.click(within(otra).getByRole("button", { name: /^Aplicar checklist/ }));
    expect(await screen.findByText(/La propiedad no tiene plantilla/)).toBeTruthy();
  });

  it("la propiedad elige plantilla o usa la por defecto", async () => {
    render(<AdminPage />);
    await userEvent.click(screen.getByRole("button", { name: "Propiedades" }));
    expect(await screen.findByText(/Vivienda \(por defecto\)/)).toBeTruthy();

    await userEvent.click(screen.getByRole("button", { name: "+ Nueva propiedad" }));
    fireEvent.change(screen.getByPlaceholderText("Piso Calle Mayor 3"), { target: { value: "Ático" } });
    fireEvent.change(screen.getByPlaceholderText("Calle Mayor 3, 2ºA, 28001 Madrid"), { target: { value: "C2" } });
    const select = screen.getByLabelText("Plantilla de checklist");
    expect(within(select).getByRole("option", { name: "Por defecto del cliente (Vivienda)" })).toBeTruthy();
    await userEvent.selectOptions(select, "4");
    await userEvent.click(screen.getByRole("button", { name: "Crear propiedad" }));
    expect(api.post).toHaveBeenCalledWith("/propiedades/", expect.objectContaining({ nombre: "Ático", plantilla_checklist: 4 }));
  });

  it("sin elegir, manda null para usar la por defecto", async () => {
    render(<AdminPage />);
    await userEvent.click(screen.getByRole("button", { name: "Propiedades" }));
    await userEvent.click(await screen.findByRole("button", { name: "+ Nueva propiedad" }));
    fireEvent.change(screen.getByPlaceholderText("Piso Calle Mayor 3"), { target: { value: "Ático" } });
    fireEvent.change(screen.getByPlaceholderText("Calle Mayor 3, 2ºA, 28001 Madrid"), { target: { value: "C2" } });
    await userEvent.click(screen.getByRole("button", { name: "Crear propiedad" }));
    expect(api.post).toHaveBeenCalledWith("/propiedades/", expect.objectContaining({ plantilla_checklist: null }));
  });

  it("la pestaña Checklists muestra las plantillas", async () => {
    render(<AdminPage />);
    await userEvent.click(screen.getByRole("button", { name: "Checklists" }));
    expect(await screen.findByText("Coche")).toBeTruthy();
  });
});

describe("AdminPage: plantillas que no cargan", () => {
  it("avisa y bloquea el selector en vez de decir «ninguna»", async () => {
    api.get.mockImplementation((url) =>
      url === "/plantillas-checklist/" ? Promise.reject(new Error("x")) : Promise.resolve({ data: RUTAS[url] ?? [] }),
    );
    render(<AdminPage />);
    await userEvent.click(screen.getByRole("button", { name: "Propiedades" }));
    await userEvent.click(await screen.findByRole("button", { name: "+ Nueva propiedad" }));
    expect(await screen.findByText(/No se pudieron cargar las plantillas/)).toBeTruthy();
    expect(screen.getByLabelText("Plantilla de checklist").disabled).toBe(true);
  });
});

