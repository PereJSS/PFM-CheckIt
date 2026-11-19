// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../../services/checklists", () => ({
  crearPlantilla: vi.fn(),
  guardarPlantilla: vi.fn(),
}));

const ck = await import("../../../services/checklists");
const { default: EditorPlantilla } = await import("../EditorPlantilla");

const NUEVA = {
  nombre: "Vivienda",
  descripcion: "",
  por_defecto: false,
  estructura: {
    estancias: [
      { nombre: "Cocina", puntos: [{ texto: "Encimera", requiere_foto: false }, { texto: "Horno", requiere_foto: false }] },
      { nombre: "Baño", puntos: [{ texto: "Grifo", requiere_foto: false }] },
    ],
  },
};

const pintar = (plantilla = NUEVA) => {
  const props = { onGuardada: vi.fn(), onCancelar: vi.fn(), onRecargar: vi.fn() };
  render(<EditorPlantilla plantilla={plantilla} {...props} />);
  return props;
};

beforeEach(() => vi.clearAllMocks());
afterEach(cleanup);

describe("EditorPlantilla", () => {
  it("crea la plantilla con lo editado", async () => {
    ck.crearPlantilla.mockResolvedValue({ id: 9 });
    const { onGuardada } = pintar();

    await userEvent.clear(screen.getByLabelText("Nombre de la plantilla"));
    await userEvent.type(screen.getByLabelText("Nombre de la plantilla"), "Piso 2 hab.");
    await userEvent.click(screen.getByRole("button", { name: "Añadir punto a Baño" }));
    await userEvent.type(screen.getByLabelText("Punto 2 de Baño"), "Espejo");
    await userEvent.click(screen.getByLabelText("Foto obligatoria del punto 2 de Baño"));
    await userEvent.click(screen.getByRole("button", { name: "Subir punto Horno" }));
    await userEvent.click(screen.getByRole("button", { name: "Quitar punto Encimera" }));
    await userEvent.click(screen.getByRole("button", { name: "Añadir estancia" }));
    await userEvent.type(screen.getByLabelText("Nombre de la estancia 3"), "Terraza");
    await userEvent.type(screen.getByLabelText("Punto 1 de Terraza"), "Suelo");
    await userEvent.click(screen.getByRole("button", { name: "Guardar plantilla" }));

    expect(ck.crearPlantilla).toHaveBeenCalledWith({
      nombre: "Piso 2 hab.",
      descripcion: "",
      por_defecto: false,
      estructura: {
        estancias: [
          { nombre: "Cocina", puntos: [{ texto: "Horno", requiere_foto: false }] },
          { nombre: "Baño", puntos: [{ texto: "Grifo", requiere_foto: false }, { texto: "Espejo", requiere_foto: true }] },
          { nombre: "Terraza", puntos: [{ texto: "Suelo", requiere_foto: false }] },
        ],
      },
    });
    expect(onGuardada).toHaveBeenCalledWith({ id: 9 });
  });

  it("mueve y quita estancias", async () => {
    ck.crearPlantilla.mockResolvedValue({ id: 9 });
    pintar();
    await userEvent.click(screen.getByRole("button", { name: "Bajar estancia Cocina" }));
    await userEvent.click(screen.getByRole("button", { name: "Quitar estancia Cocina" }));
    await userEvent.click(screen.getByRole("button", { name: "Guardar plantilla" }));
    expect(ck.crearPlantilla.mock.calls[0][0].estructura.estancias.map((e) => e.nombre)).toEqual(["Baño"]);
  });

  it("al editar manda la versión leída", async () => {
    ck.guardarPlantilla.mockResolvedValue({ id: 3, version: 5 });
    pintar({ ...NUEVA, id: 3, version: 4 });
    await userEvent.click(screen.getByLabelText("Plantilla por defecto del cliente"));
    await userEvent.click(screen.getByRole("button", { name: "Guardar plantilla" }));
    expect(ck.guardarPlantilla).toHaveBeenCalledWith(3, expect.objectContaining({ version: 4, por_defecto: true }));
  });

  it("si otra persona la cambió, avisa y deja recargar", async () => {
    ck.guardarPlantilla.mockRejectedValue({ response: { status: 409, data: { error: "Ha cambiado.", version: 5 } } });
    const { onRecargar, onGuardada } = pintar({ ...NUEVA, id: 3, version: 4 });
    await userEvent.click(screen.getByRole("button", { name: "Guardar plantilla" }));
    expect((await screen.findByRole("alert")).textContent).toMatch(/otra persona/i);
    await userEvent.click(screen.getByRole("button", { name: "Recargar" }));
    expect(onRecargar).toHaveBeenCalledWith(3);
    expect(onGuardada).not.toHaveBeenCalled();
  });

  it("muestra los errores de validación del servidor", async () => {
    ck.crearPlantilla.mockRejectedValue({
      response: { status: 400, data: { nombre: ["Ya tienes una plantilla con ese nombre."], estructura: ["La estancia «Baño» está repetida."] } },
    });
    pintar();
    await userEvent.click(screen.getByRole("button", { name: "Guardar plantilla" }));
    const alerta = await screen.findByRole("alert");
    expect(alerta.textContent).toMatch(/Ya tienes una plantilla con ese nombre/);
    expect(alerta.textContent).toMatch(/está repetida/);
  });

  it("cancelar no guarda", async () => {
    const { onCancelar } = pintar();
    await userEvent.click(screen.getByRole("button", { name: "Cancelar" }));
    expect(onCancelar).toHaveBeenCalled();
    expect(ck.crearPlantilla).not.toHaveBeenCalled();
  });
});

describe("EditorPlantilla: errores y foco", () => {
  it("aplana los errores anidados de DRF", async () => {
    ck.crearPlantilla.mockRejectedValue({
      response: { status: 400, data: { estructura: { estancias: [{ puntos: ["Texto vacío."] }] } } },
    });
    pintar();
    await userEvent.click(screen.getByRole("button", { name: "Guardar plantilla" }));
    const alerta = await screen.findByRole("alert");
    expect(alerta.textContent).toMatch(/Texto vacío/);
    expect(alerta.textContent).not.toMatch(/object Object/);
    expect(document.activeElement).toBe(alerta);
  });

  it("un 404 dice la causa, no «otra persona»", async () => {
    ck.guardarPlantilla.mockRejectedValue({ response: { status: 404, data: { detail: "No encontrado." } } });
    pintar({ ...NUEVA, id: 3, version: 4 });
    await userEvent.click(screen.getByRole("button", { name: "Guardar plantilla" }));
    expect((await screen.findByRole("alert")).textContent).toMatch(/No encontrado/);
  });

  it("al añadir un punto el foco va a su campo", async () => {
    pintar();
    await userEvent.click(screen.getByRole("button", { name: "Añadir punto a Baño" }));
    expect(document.activeElement).toBe(screen.getByLabelText("Punto 2 de Baño"));
  });

  it("al quitar un punto el foco va al vecino", async () => {
    pintar();
    await userEvent.click(screen.getByRole("button", { name: "Quitar punto Horno" }));
    expect(document.activeElement).toBe(screen.getByLabelText("Punto 1 de Cocina"));
  });

  it("muestra el aviso que le pasa el panel", () => {
    render(<EditorPlantilla plantilla={NUEVA} onGuardada={vi.fn()} onCancelar={vi.fn()} onRecargar={vi.fn()}
      avisoExterno="No se pudo recargar la plantilla." />);
    expect(screen.getByRole("alert").textContent).toMatch(/No se pudo recargar/);
  });
});

