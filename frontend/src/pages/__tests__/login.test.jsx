// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../services/api", () => ({ default: { post: vi.fn(), get: vi.fn() } }));

const { default: api } = await import("../../services/api");
const { default: Login } = await import("../login");

beforeEach(() => {
  vi.clearAllMocks();
  sessionStorage.clear();
});
afterEach(cleanup);

describe("Login: rol recordado (#15)", () => {
  it("guarda el rol del usuario que entra, no el de una sesión anterior", async () => {
    sessionStorage.setItem("rol", "ADMINISTRADOR");
    api.post.mockResolvedValue({ data: { access: "a", refresh: "r" } });
    api.get.mockResolvedValue({ data: { role: " operario " } });
    render(<MemoryRouter><Login /></MemoryRouter>);
    await userEvent.type(screen.getByLabelText(/Usuario/i), "op");
    await userEvent.type(screen.getByLabelText(/Contraseña/i), "clave");
    await userEvent.click(screen.getByRole("button", { name: /Entrar/i }));
    await waitFor(() => expect(sessionStorage.getItem("rol")).toBe("OPERARIO"));
  });
});

describe("Login: registro cerrado (B3)", () => {
  it("no enlaza a un registro público", () => {
    render(<MemoryRouter><Login /></MemoryRouter>);
    expect(screen.queryByRole("link", { name: /Regístrate/i })).toBeNull();
  });
});
