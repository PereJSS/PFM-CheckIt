// @vitest-environment jsdom
// ProtectedRoute (#15): sin red no se cierra la sesión; solo un 401/403 la cierra.
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../services/api", () => ({ default: { get: vi.fn() } }));
vi.mock("../pages/inspeccion", () => ({ default: () => <p>Página del operario</p> }));
vi.mock("../pages/admin", () => ({ default: () => <p>Página del admin</p> }));
vi.mock("../pages/login", () => ({ default: () => <p>Página de login</p> }));
vi.mock("../pages/register", () => ({ default: () => null }));
vi.mock("../pages/verificar", () => ({ default: () => null }));
vi.mock("../components/AvisoActualizacion", () => ({ default: () => null }));

const { default: api } = await import("../services/api");
const { default: App } = await import("../App");

const sinRed = () => Object.assign(new Error("Network Error"), { code: "ERR_NETWORK" });
const conEstado = (status) => Object.assign(new Error(`HTTP ${status}`), { response: { status } });

beforeEach(() => {
  vi.clearAllMocks();
  sessionStorage.clear();
  sessionStorage.setItem("access_token", "a");
  sessionStorage.setItem("refresh_token", "r");
  window.history.pushState({}, "", "/operario");
});
afterEach(cleanup);

describe("ProtectedRoute", () => {
  it("con red entra y recuerda el rol en la pestaña", async () => {
    api.get.mockResolvedValue({ data: { role: "OPERARIO" } });
    render(<App />);
    expect(await screen.findByText("Página del operario")).toBeTruthy();
    expect(sessionStorage.getItem("rol")).toBe("OPERARIO");
  });

  it("sin red usa el rol recordado y no cierra la sesión", async () => {
    sessionStorage.setItem("rol", "OPERARIO");
    api.get.mockRejectedValue(sinRed());
    render(<App />);
    expect(await screen.findByText("Página del operario")).toBeTruthy();
    expect(sessionStorage.getItem("refresh_token")).toBe("r");
  });

  it.each([500, 503, 429])("un %i tampoco cierra la sesión", async (status) => {
    sessionStorage.setItem("rol", "OPERARIO");
    api.get.mockRejectedValue(conEstado(status));
    render(<App />);
    expect(await screen.findByText("Página del operario")).toBeTruthy();
    expect(sessionStorage.getItem("access_token")).toBe("a");
  });

  it.each([503, 429])("un %i sin rol recordado deja reintentar", async (status) => {
    api.get.mockRejectedValue(conEstado(status));
    render(<App />);
    expect(await screen.findByRole("button", { name: "Reintentar" })).toBeTruthy();
    expect(sessionStorage.getItem("refresh_token")).toBe("r");
  });

  it("si reintentar vuelve a fallar, avisa otra vez", async () => {
    api.get.mockRejectedValue(sinRed());
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "Reintentar" }));
    expect(await screen.findByRole("button", { name: "Reintentar" })).toBeTruthy();
    expect(api.get).toHaveBeenCalledTimes(2);
  });

  it("sin red y sin rol recordado, avisa y deja reintentar", async () => {
    api.get.mockRejectedValueOnce(sinRed());
    render(<App />);
    expect(await screen.findByText(/No se pudo comprobar la sesión: no hay conexión/)).toBeTruthy();
    expect(sessionStorage.getItem("refresh_token")).toBe("r");

    api.get.mockResolvedValueOnce({ data: { role: "OPERARIO" } });
    await userEvent.click(screen.getByRole("button", { name: "Reintentar" }));
    expect(await screen.findByText("Página del operario")).toBeTruthy();
  });

  it("un 401 cierra la sesión y lleva al login", async () => {
    sessionStorage.setItem("rol", "OPERARIO");
    api.get.mockRejectedValue(conEstado(401));
    render(<App />);
    expect(await screen.findByText("Página de login")).toBeTruthy();
    expect(sessionStorage.getItem("refresh_token")).toBeNull();
    expect(sessionStorage.getItem("rol")).toBeNull();
  });

  it("un 403 también cierra la sesión", async () => {
    api.get.mockRejectedValue(conEstado(403));
    sessionStorage.setItem("rol", "OPERARIO");
    render(<App />);
    expect(await screen.findByText("Página de login")).toBeTruthy();
    expect(sessionStorage.getItem("access_token")).toBeNull();
    expect(sessionStorage.getItem("rol")).toBeNull();
  });

  it("el rol recordado no da acceso a otra área: un operario no entra en el admin", async () => {
    sessionStorage.setItem("rol", "OPERARIO");
    window.history.pushState({}, "", "/");
    api.get.mockRejectedValue(sinRed());
    render(<App />);
    expect(await screen.findByText("Página del operario")).toBeTruthy();
    expect(screen.queryByText("Página del admin")).toBeNull();
  });
});
