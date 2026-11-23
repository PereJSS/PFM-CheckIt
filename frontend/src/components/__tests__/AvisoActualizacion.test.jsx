// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Estado que devuelve el hook del plugin en cada prueba
const estado = {
  hayVersion: false,
  listaSinConexion: false,
  setHayVersion: vi.fn(),
  setListaSinConexion: vi.fn(),
  updateServiceWorker: vi.fn(),
  opciones: null,
};

vi.mock("virtual:pwa-register/react", () => ({
  useRegisterSW: (opciones) => {
    estado.opciones = opciones;
    return {
      needRefresh: [estado.hayVersion, estado.setHayVersion],
      offlineReady: [estado.listaSinConexion, estado.setListaSinConexion],
      updateServiceWorker: estado.updateServiceWorker,
    };
  },
}));

vi.mock("../../services/offline", () => ({
  getAllPendingEvidences: vi.fn(),
  getMarcasPendientes: vi.fn(),
}));

const offline = await import("../../services/offline");
const { default: AvisoActualizacion, INTERVALO_COMPROBACION_MS, MARGEN_AL_VOLVER_MS } = await import(
  "../AvisoActualizacion"
);

// navigator.serviceWorker y location.reload simulados (jsdom no tiene ninguno)
function conServiceWorker(prueba) {
  return async () => {
    const sw = new EventTarget();
    const recargar = vi.fn();
    const ubicacion = window.location;
    Object.defineProperty(navigator, "serviceWorker", { value: sw, configurable: true });
    Object.defineProperty(window, "location", { value: { ...ubicacion, reload: recargar }, configurable: true });
    try {
      await prueba({ sw, recargar });
    } finally {
      Object.defineProperty(window, "location", { value: ubicacion, configurable: true });
      delete navigator.serviceWorker;
    }
  };
}

beforeEach(() => {
  estado.hayVersion = false;
  estado.listaSinConexion = false;
  vi.clearAllMocks();
  offline.getAllPendingEvidences.mockResolvedValue([]);
  offline.getMarcasPendientes.mockResolvedValue([]);
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("AvisoActualizacion", () => {
  it("sin nada que avisar no muestra texto ni botones, pero la zona de anuncio ya existe", () => {
    render(<AvisoActualizacion />);
    // Montada y vacía: un lector de pantalla anuncia lo que entre después (#15)
    expect(screen.getByRole("status").textContent).toBe("");
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.queryByRole("region")).toBeNull();
  });

  it("avisa de la versión nueva cuando ya sabe los pendientes y no recarga hasta que se pide", async () => {
    estado.hayVersion = true;
    let resolver;
    offline.getAllPendingEvidences.mockReturnValue(new Promise((r) => { resolver = r; }));
    render(<><input aria-label="campo" /><AvisoActualizacion /></>);
    const campo = screen.getByLabelText("campo");
    campo.focus();
    // Mientras cuenta los pendientes no anuncia nada: así se anuncia una sola vez
    expect(screen.getByRole("status").textContent).toBe("");
    resolver([]);
    await screen.findByText(/Hay una versión nueva de CheckIt/);
    const region = screen.getByRole("region", { name: "Avisos de CheckIt" });
    expect(screen.getByRole("status").textContent).toContain("Hay una versión nueva de CheckIt");
    expect(estado.updateServiceWorker).not.toHaveBeenCalled();
    // No roba el foco a lo que esté haciendo el operario
    expect(document.activeElement).toBe(campo);
    expect(region.contains(document.activeElement)).toBe(false);

    await userEvent.click(screen.getByRole("button", { name: "Actualizar" }));
    expect(estado.updateServiceWorker).toHaveBeenCalledWith(true);
  });

  it("los botones quedan fuera de la zona que se anuncia", async () => {
    estado.hayVersion = true;
    render(<AvisoActualizacion />);
    await screen.findByText(/Hay una versión nueva/);
    expect(screen.getByRole("status").querySelector("button")).toBeNull();
  });

  it("recarga cuando el service worker nuevo toma el control, aunque el plugin no lo haga", conServiceWorker(async ({ sw, recargar }) => {
    // Pestaña abierta desde la primera visita: workbox-window no marca el cambio como
    // actualización y el plugin no recargaría
    estado.hayVersion = true;
    estado.updateServiceWorker.mockResolvedValue(undefined);
    render(<AvisoActualizacion />);
    await screen.findByRole("button", { name: "Actualizar" });
    // Un cambio de controlador sin haber pulsado no recarga
    sw.dispatchEvent(new Event("controllerchange"));
    expect(recargar).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: "Actualizar" }));
    sw.dispatchEvent(new Event("controllerchange"));
    sw.dispatchEvent(new Event("controllerchange"));
    expect(recargar).toHaveBeenCalledTimes(1);
  }));

  it("pulsar dos veces no apila recargas, y si falla la actualización un cambio ajeno no recarga", conServiceWorker(async ({ sw, recargar }) => {
    estado.hayVersion = true;
    estado.updateServiceWorker.mockRejectedValueOnce(new Error("sin worker en espera"));
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    render(<AvisoActualizacion />);
    await userEvent.click(await screen.findByRole("button", { name: "Actualizar" }));
    await waitFor(() => expect(error).toHaveBeenCalled());
    // Otra pestaña actualiza: esta no debe recargarse a mitad de una inspección
    sw.dispatchEvent(new Event("controllerchange"));
    expect(recargar).not.toHaveBeenCalled();
    error.mockRestore();

    estado.updateServiceWorker.mockResolvedValue(undefined);
    await userEvent.click(screen.getByRole("button", { name: "Actualizar" }));
    await userEvent.click(screen.getByRole("button", { name: "Actualizar" }));
    sw.dispatchEvent(new Event("controllerchange"));
    expect(recargar).toHaveBeenCalledTimes(1);
  }));

  it("al desmontarse deja de esperar el cambio de service worker", conServiceWorker(async ({ sw, recargar }) => {
    estado.hayVersion = true;
    estado.updateServiceWorker.mockResolvedValue(undefined);
    const { unmount } = render(<AvisoActualizacion />);
    await userEvent.click(await screen.findByRole("button", { name: "Actualizar" }));
    unmount();
    sw.dispatchEvent(new Event("controllerchange"));
    expect(recargar).not.toHaveBeenCalled();
  }));

  it("«Más tarde» también quita el aviso de sin conexión", async () => {
    estado.hayVersion = true;
    estado.listaSinConexion = true;
    render(<AvisoActualizacion />);
    await userEvent.click(await screen.findByRole("button", { name: "Más tarde" }));
    expect(estado.setListaSinConexion).toHaveBeenCalledWith(false);
  });

  it("«Más tarde» cierra el aviso sin actualizar", async () => {
    estado.hayVersion = true;
    render(<AvisoActualizacion />);
    await userEvent.click(await screen.findByRole("button", { name: "Más tarde" }));
    expect(estado.setHayVersion).toHaveBeenCalledWith(false);
    expect(estado.updateServiceWorker).not.toHaveBeenCalled();
  });

  it("si hay fotos o marcas pendientes, dice que se conservan", async () => {
    estado.hayVersion = true;
    offline.getAllPendingEvidences.mockResolvedValue([{ id: 1 }, { id: 2 }]);
    offline.getMarcasPendientes.mockResolvedValue([{ puntoId: 3 }]);
    render(<AvisoActualizacion />);
    expect(await screen.findByText(/3 elementos pendientes de subir se conservan en este móvil/)).toBeTruthy();
  });

  it("con un solo pendiente lo dice en singular", async () => {
    estado.hayVersion = true;
    offline.getAllPendingEvidences.mockResolvedValue([{ id: 1 }]);
    render(<AvisoActualizacion />);
    expect(await screen.findByText(/El elemento pendiente de subir se conserva en este móvil/)).toBeTruthy();
  });

  it("si no se pueden contar los pendientes, el aviso sigue funcionando", async () => {
    estado.hayVersion = true;
    offline.getAllPendingEvidences.mockRejectedValue(new Error("IndexedDB bloqueada"));
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    render(<AvisoActualizacion />);
    await waitFor(() => expect(error).toHaveBeenCalled());
    expect(await screen.findByRole("button", { name: "Actualizar" })).toBeTruthy();
    expect(screen.queryByText(/pendientes de subir/)).toBeNull();
    error.mockRestore();
  });

  it("avisa una vez de que ya funciona sin conexión", async () => {
    estado.listaSinConexion = true;
    render(<AvisoActualizacion />);
    expect(screen.getByRole("status").textContent).toContain("CheckIt ya funciona sin conexión en este dispositivo");
    await userEvent.click(screen.getByRole("button", { name: "Entendido" }));
    expect(estado.setListaSinConexion).toHaveBeenCalledWith(false);
  });

  it("busca versiones nuevas cada cierto tiempo, solo con red", () => {
    vi.useFakeTimers();
    render(<AvisoActualizacion />);
    const registro = { update: vi.fn().mockResolvedValue(undefined) };
    estado.opciones.onRegisteredSW("/sw.js", registro);

    vi.advanceTimersByTime(INTERVALO_COMPROBACION_MS);
    expect(registro.update).toHaveBeenCalledTimes(1);

    const enLinea = vi.spyOn(navigator, "onLine", "get").mockReturnValue(false);
    vi.advanceTimersByTime(INTERVALO_COMPROBACION_MS);
    expect(registro.update).toHaveBeenCalledTimes(1);
    enLinea.mockRestore();
  });

  it("una segunda llamada de registro no duplica las comprobaciones", () => {
    vi.useFakeTimers();
    render(<AvisoActualizacion />);
    const registro = { update: vi.fn().mockResolvedValue(undefined) };
    estado.opciones.onRegisteredSW("/sw.js", registro);
    estado.opciones.onRegisteredSW("/sw.js", registro);
    vi.advanceTimersByTime(INTERVALO_COMPROBACION_MS);
    expect(registro.update).toHaveBeenCalledTimes(1);
  });

  it("si el registro llega tras desmontarse, no deja un temporizador vivo", () => {
    vi.useFakeTimers();
    const { unmount } = render(<AvisoActualizacion />);
    unmount();
    const registro = { update: vi.fn().mockResolvedValue(undefined) };
    estado.opciones.onRegisteredSW("/sw.js", registro);
    vi.advanceTimersByTime(INTERVALO_COMPROBACION_MS * 2);
    expect(registro.update).not.toHaveBeenCalled();
  });

  it("al volver a la app busca versión nueva, como mucho cada cierto margen", () => {
    vi.useFakeTimers();
    render(<AvisoActualizacion />);
    const registro = { update: vi.fn().mockResolvedValue(undefined) };
    estado.opciones.onRegisteredSW("/sw.js", registro);
    const visible = vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
    vi.advanceTimersByTime(MARGEN_AL_VOLVER_MS);
    document.dispatchEvent(new Event("visibilitychange"));
    expect(registro.update).toHaveBeenCalledTimes(1);
    document.dispatchEvent(new Event("visibilitychange"));
    expect(registro.update).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(MARGEN_AL_VOLVER_MS);
    document.dispatchEvent(new Event("visibilitychange"));
    expect(registro.update).toHaveBeenCalledTimes(2);
    visible.mockRestore();
  });

  it("deja de comprobar al desmontarse", () => {
    vi.useFakeTimers();
    const { unmount } = render(<AvisoActualizacion />);
    const registro = { update: vi.fn().mockResolvedValue(undefined) };
    estado.opciones.onRegisteredSW("/sw.js", registro);
    unmount();
    vi.advanceTimersByTime(INTERVALO_COMPROBACION_MS * 2);
    expect(registro.update).not.toHaveBeenCalled();
  });
});
