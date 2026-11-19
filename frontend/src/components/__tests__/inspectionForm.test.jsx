// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../services/api", () => ({ default: { get: vi.fn(), post: vi.fn(), patch: vi.fn() } }));
vi.mock("../../services/offline", () => ({
  getPendingEvidencesByInspection: vi.fn(),
  saveEvidenceOffline: vi.fn(),
  guardarMarcaOffline: vi.fn(),
  getMarcasPendientes: vi.fn(),
  guardarChecklistCache: vi.fn(),
  leerChecklistCache: vi.fn(),
  quitarMarcaOffline: vi.fn(),
}));
vi.mock("../../utils/hash", () => ({
  calculateSHA256: vi.fn().mockResolvedValue("a".repeat(64)),
}));

const { default: api } = await import("../../services/api");
const offline = await import("../../services/offline");
const { saveEvidenceOffline } = offline;
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

const SIN_CHECKLIST = { data: { tiene_checklist: false } };

beforeEach(() => {
  vi.clearAllMocks();
  api.get.mockResolvedValue(SIN_CHECKLIST);
  offline.getPendingEvidencesByInspection.mockResolvedValue([]);
  offline.saveEvidenceOffline.mockResolvedValue(undefined);
  offline.guardarMarcaOffline.mockResolvedValue(undefined);
  offline.getMarcasPendientes.mockResolvedValue([]);
  offline.guardarChecklistCache.mockResolvedValue(undefined);
  offline.leerChecklistCache.mockResolvedValue(null);
  offline.quitarMarcaOffline.mockResolvedValue(undefined);
});
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

const CHECKLIST = {
  tiene_checklist: true,
  plantilla_nombre: "Vivienda",
  plantilla_version: 1,
  estancias: [
    {
      id: 1,
      nombre: "Cocina",
      puntos: [
        { id: 10, texto: "Encimera", requiere_foto: false, resultado: null, nota: "", evidencias: [] },
        { id: 11, texto: "Vista general", requiere_foto: true, resultado: null, nota: "", evidencias: [] },
      ],
    },
  ],
};
const conChecklist = (cambios = {}) => ({
  data: {
    ...CHECKLIST,
    estancias: [{ ...CHECKLIST.estancias[0], puntos: CHECKLIST.estancias[0].puntos.map((p) => ({ ...p, ...(cambios[p.id] || {}) })) }],
  },
});

const abrirCocina = async () => {
  render(<InspectionForm inspectionId={5} />);
  await userEvent.click(await screen.findByRole("button", { name: /Cocina, 0 de 2/ }));
};

describe("InspectionForm con checklist", () => {
  it("muestra el checklist y deja «Otras fotos» para lo imprevisto", async () => {
    api.get.mockResolvedValue(conChecklist());
    render(<InspectionForm inspectionId={5} />);
    expect(await screen.findByText(/Checklist · Vivienda/)).toBeTruthy();
    expect(screen.getByText("Otras fotos")).toBeTruthy();
    expect(api.get).toHaveBeenCalledWith("/inspecciones/5/checklist/");
    await waitFor(() => expect(offline.guardarChecklistCache).toHaveBeenCalledWith(5, CHECKLIST));
  });

  it("sin red usa el último checklist guardado en el móvil", async () => {
    api.get.mockRejectedValue(new Error("Network Error"));
    offline.leerChecklistCache.mockResolvedValue(CHECKLIST);
    render(<InspectionForm inspectionId={5} />);
    expect(await screen.findByText(/Checklist · Vivienda/)).toBeTruthy();
  });

  it("marcar un punto lo guarda con la hora del móvil", async () => {
    api.get.mockResolvedValue(conChecklist());
    api.patch.mockResolvedValue({ data: { aplicado: true, punto: { ...CHECKLIST.estancias[0].puntos[0], resultado: "OK" } } });
    await abrirCocina();
    await userEvent.click(within(screen.getByRole("group", { name: "Encimera" })).getByRole("button", { name: /^OK —/ }));
    expect(api.patch).toHaveBeenCalledWith(
      "/inspecciones/5/checklist/puntos/10/",
      expect.objectContaining({ resultado: "OK", marcado_en_dispositivo: expect.any(String) }),
    );
    expect(within(screen.getByRole("group", { name: "Encimera" })).getByRole("button", { name: /^OK —/ }).getAttribute("aria-pressed")).toBe("true");
  });

  it("sin red la marca se guarda en el móvil y se ve marcada", async () => {
    api.get.mockResolvedValue(conChecklist());
    api.patch.mockRejectedValue(new Error("Network Error"));
    await abrirCocina();
    await userEvent.click(within(screen.getByRole("group", { name: "Encimera" })).getByRole("button", { name: /^Daño —/ }));
    await waitFor(() =>
      expect(offline.guardarMarcaOffline).toHaveBeenCalledWith(
        expect.objectContaining({ inspectionId: 5, puntoId: 10, resultado: "DANO", nota: "", marcadoEnDispositivo: expect.any(String) }),
      ),
    );
    expect(within(screen.getByRole("group", { name: "Encimera" })).getByRole("button", { name: /^Daño —/ }).getAttribute("aria-pressed")).toBe("true");
  });

  it("si el servidor rechaza la marca, la deshace y dice por qué", async () => {
    api.get.mockResolvedValue(conChecklist());
    api.patch.mockRejectedValue({ response: { status: 409, data: { error: "La inspección ya está completada." } } });
    await abrirCocina();
    await userEvent.click(within(screen.getByRole("group", { name: "Encimera" })).getByRole("button", { name: /^OK —/ }));
    expect(await screen.findByText(/La inspección ya está completada/)).toBeTruthy();
    expect(within(screen.getByRole("group", { name: "Encimera" })).getByRole("button", { name: /^OK —/ }).getAttribute("aria-pressed")).toBe("false");
    expect(offline.guardarMarcaOffline).not.toHaveBeenCalled();
  });

  it("la foto de un punto va enlazada a él", async () => {
    vi.spyOn(navigator, "userAgent", "get").mockReturnValue("iPhone");
    api.get.mockResolvedValue(conChecklist({ 10: { resultado: "DANO" } }));
    api.post.mockResolvedValue({ data: { id: 77, punto: 10 } });
    render(<InspectionForm inspectionId={5} />);
    await userEvent.click(await screen.findByRole("button", { name: /Cocina, 1 de 2/ }));
    await userEvent.click(within(screen.getByRole("group", { name: "Encimera" })).getByRole("button", { name: /Añadir foto/ }));
    subirFoto();
    await waitFor(() => expect(api.post).toHaveBeenCalled());
    const fd = api.post.mock.calls[0][1];
    expect(fd.get("punto")).toBe("10");
    expect(fd.get("estancia")).toBe("Cocina");
    expect(await within(screen.getByRole("group", { name: "Encimera" })).findByText("1 foto")).toBeTruthy();
    vi.restoreAllMocks();
  });

  it("finalizar con puntos pendientes avisa sin llamar al servidor", async () => {
    api.get.mockResolvedValue(conChecklist({ 10: { resultado: "OK" } }));
    render(<InspectionForm inspectionId={5} />);
    await screen.findByText(/Checklist · Vivienda/);
    await userEvent.click(screen.getByRole("button", { name: "Finalizar inspección" }));
    expect(await screen.findByText(/Cocina · Vista general \(sin marcar\)/)).toBeTruthy();
    expect(api.patch).not.toHaveBeenCalled();
  });

  it("muestra las faltas que devuelve el servidor", async () => {
    api.get.mockResolvedValue(conChecklist({ 10: { resultado: "OK" }, 11: { resultado: "NO_APLICA" } }));
    api.patch.mockRejectedValue({
      response: { status: 409, data: { error: "Faltan puntos.", faltas: [{ punto: 10, estancia: "Cocina", texto: "Encimera", motivo: "falta_foto" }] } },
    });
    render(<InspectionForm inspectionId={5} />);
    await screen.findByText(/Checklist · Vivienda/);
    await userEvent.click(screen.getByRole("button", { name: "Finalizar inspección" }));
    expect(await screen.findByText(/Cocina · Encimera \(falta foto\)/)).toBeTruthy();
  });

  it("las marcas guardadas en el móvil también impiden finalizar", async () => {
    offline.getMarcasPendientes.mockResolvedValue([{ inspectionId: 5, puntoId: 10 }]);
    render(<InspectionForm inspectionId={5} />);
    await userEvent.click(screen.getByRole("button", { name: "Finalizar inspección" }));
    expect(await screen.findByText(/pendientes de sincronizar/)).toBeTruthy();
    expect(api.patch).not.toHaveBeenCalled();
  });
});

describe("InspectionForm: carga y orden de las marcas", () => {
  it("si no se puede leer el checklist, lo dice y bloquea finalizar hasta reintentar", async () => {
    api.get.mockRejectedValueOnce({ response: { status: 403, data: { detail: "No." } } });
    render(<InspectionForm inspectionId={5} />);
    expect(await screen.findByText(/No se ha podido cargar el checklist/)).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Finalizar inspección" }));
    expect(api.patch).not.toHaveBeenCalled();
    api.get.mockResolvedValue(conChecklist());
    await userEvent.click(screen.getByRole("button", { name: "Reintentar" }));
    expect(await screen.findByText(/Checklist · Vivienda/)).toBeTruthy();
  });

  it("al abrir pinta encima las marcas pendientes del móvil", async () => {
    api.get.mockResolvedValue(conChecklist());
    offline.getMarcasPendientes.mockResolvedValue([{ inspectionId: 5, puntoId: 10, resultado: "DANO", nota: "x" }]);
    render(<InspectionForm inspectionId={5} />);
    expect(await screen.findByRole("button", { name: /Cocina, 1 de 2/ })).toBeTruthy();
  });

  it("un rechazo tardío de la primera marca no deshace la segunda", async () => {
    api.get.mockResolvedValue(conChecklist());
    let rechazarPrimera;
    api.patch
      .mockImplementationOnce(() => new Promise((_, reject) => {
        rechazarPrimera = () => reject({ response: { status: 400, data: { error: "No válida." } } });
      }))
      .mockResolvedValueOnce({ data: { aplicado: true, punto: { ...CHECKLIST.estancias[0].puntos[0], resultado: "DANO" } } });
    await abrirCocina();
    const grupo = () => within(screen.getByRole("group", { name: "Encimera" }));
    await userEvent.click(grupo().getByRole("button", { name: /^OK —/ }));
    await userEvent.click(grupo().getByRole("button", { name: /^Daño —/ }));
    rechazarPrimera();
    expect(await screen.findByText(/No válida/)).toBeTruthy();
    expect(grupo().getByRole("button", { name: /^Daño —/ }).getAttribute("aria-pressed")).toBe("true");
  });

  it("una marca subida quita la pendiente anterior del móvil", async () => {
    api.get.mockResolvedValue(conChecklist());
    api.patch.mockResolvedValue({ data: { aplicado: true, punto: { ...CHECKLIST.estancias[0].puntos[0], resultado: "OK" } } });
    await abrirCocina();
    await userEvent.click(within(screen.getByRole("group", { name: "Encimera" })).getByRole("button", { name: /^OK —/ }));
    await waitFor(() => expect(offline.quitarMarcaOffline).toHaveBeenCalledWith(5, 10, expect.any(String)));
  });
});

