// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../services/api", () => ({ default: { post: vi.fn(), get: vi.fn() } }));

const { default: api } = await import("../../services/api");
const { default: Register } = await import("../register");

function Ubicacion() {
  const { pathname, hash } = useLocation();
  return <output data-testid="ubicacion">{pathname + hash}</output>;
}

const abrir = (entrada = "/register#invitacion=tok123") =>
  render(
    <MemoryRouter initialEntries={[entrada]}>
      <Register />
      <Ubicacion />
    </MemoryRouter>,
  );

const error400 = (data) => Object.assign(new Error("400"), { response: { status: 400, data } });

const rellenar = async () => {
  await userEvent.type(screen.getByLabelText(/Nombre de usuario/i), "ana");
  await userEvent.type(screen.getByLabelText(/^Contraseña/i), "SecurePass123!");
  await userEvent.type(screen.getByLabelText(/Confirmar contraseña/i), "SecurePass123!");
};

beforeEach(() => vi.clearAllMocks());
afterEach(cleanup);

describe("Alta por invitación (B3)", () => {
  it("sin invitación en el enlace no enseña el formulario ni llama a la API", () => {
    abrir("/register");
    expect(screen.getByText(/Necesitas una invitación/i)).toBeTruthy();
    expect(screen.queryByLabelText(/Nombre de usuario/i)).toBeNull();
    expect(api.post).not.toHaveBeenCalled();
  });

  it("comprueba la invitación del fragmento y la quita de la barra de direcciones", async () => {
    api.post.mockResolvedValue({ data: { email: "" } });
    abrir();
    await screen.findByLabelText(/Nombre de usuario/i);
    expect(api.post).toHaveBeenCalledWith("/auth/invitacion/", { invitacion: "tok123" });
    expect(screen.getByTestId("ubicacion").textContent).toBe("/register");
  });

  it("invitación no válida: avisa y no enseña el formulario", async () => {
    api.post.mockRejectedValue(error400({ invitacion: ["Invitación no válida o caducada."] }));
    abrir();
    expect(await screen.findByText(/no es válida o ha caducado/i)).toBeTruthy();
    expect(screen.queryByLabelText(/Nombre de usuario/i)).toBeNull();
  });

  it("sin conexión al comprobar: permite reintentar", async () => {
    api.post.mockRejectedValueOnce(new Error("Network Error"));
    api.post.mockResolvedValueOnce({ data: { email: "" } });
    abrir();
    await userEvent.click(await screen.findByRole("button", { name: /Reintentar/i }));
    await screen.findByLabelText(/Nombre de usuario/i);
    expect(api.post).toHaveBeenCalledTimes(2);
  });

  it("nominativa: el email viene fijado y no se puede cambiar", async () => {
    api.post.mockResolvedValue({ data: { email: "ana@empresa.com" } });
    abrir();
    const email = await screen.findByLabelText(/Correo electrónico/i);
    expect(email.value).toBe("ana@empresa.com");
    expect(email.readOnly).toBe(true);
  });

  it("envía el alta con la invitación y el email fijado", async () => {
    api.post.mockResolvedValueOnce({ data: { email: "ana@empresa.com" } });
    api.post.mockResolvedValueOnce({ data: { username: "ana" } });
    abrir();
    await screen.findByLabelText(/Nombre de usuario/i);
    await rellenar();
    await userEvent.click(screen.getByRole("button", { name: /Crear cuenta/i }));
    await screen.findByText(/Cuenta creada/i);
    expect(api.post).toHaveBeenLastCalledWith(
      "/auth/register/",
      expect.objectContaining({
        invitacion: "tok123",
        username: "ana",
        email: "ana@empresa.com",
        password: "SecurePass123!",
        password2: "SecurePass123!",
      }),
    );
  });

  it("si la invitación se gasta mientras rellena, lo dice al enviar", async () => {
    api.post.mockResolvedValueOnce({ data: { email: "" } });
    api.post.mockRejectedValueOnce(error400({ invitacion: ["Invitación no válida o caducada."] }));
    abrir();
    await screen.findByLabelText(/Nombre de usuario/i);
    await rellenar();
    await userEvent.click(screen.getByRole("button", { name: /Crear cuenta/i }));
    const aviso = await screen.findByRole("alert");
    expect(aviso.textContent).toMatch(/Invitación no válida o caducada/);
  });

  it("muestra el error de un campo junto a ese campo", async () => {
    api.post.mockResolvedValueOnce({ data: { email: "" } });
    api.post.mockRejectedValueOnce(error400({ username: ["Ya existe un usuario con ese nombre."] }));
    abrir();
    await screen.findByLabelText(/Nombre de usuario/i);
    await rellenar();
    await userEvent.click(screen.getByRole("button", { name: /Crear cuenta/i }));
    await screen.findByText(/Ya existe un usuario/i);
    expect(screen.getByLabelText(/Nombre de usuario/i).getAttribute("aria-invalid")).toBe("true");
  });

  it("429 o 500 al comprobar: no culpa a la conexión y permite reintentar", async () => {
    const e429 = Object.assign(new Error("Demasiados intentos, espera 30 s y vuelve a probar."), {
      response: { status: 429, data: {} },
    });
    api.post.mockRejectedValueOnce(e429);
    abrir();
    expect(await screen.findByText(/Demasiados intentos/i)).toBeTruthy();
    expect(screen.queryByText(/Revisa la conexión/i)).toBeNull();
    cleanup();
    api.post.mockRejectedValueOnce(Object.assign(new Error("500"), { response: { status: 500, data: "<html>" } }));
    abrir();
    expect(await screen.findByText(/error del servidor/i)).toBeTruthy();
    expect(screen.getByRole("button", { name: /Reintentar/i })).toBeTruthy();
  });

  it("el foco va al título al cambiar de pantalla", async () => {
    api.post.mockResolvedValue({ data: { email: "" } });
    abrir();
    const titulo = await screen.findByRole("heading", { name: /Crear cuenta de administrador/i });
    await screen.findByLabelText(/Nombre de usuario/i);
    expect(document.activeElement).toBe(
      screen.getByRole("heading", { name: /Crear cuenta de administrador/i }),
    );
    expect(titulo).toBeTruthy();
  });

  it("tras un envío fallido, el foco va al primer campo con error", async () => {
    api.post.mockResolvedValueOnce({ data: { email: "" } });
    api.post.mockRejectedValueOnce(error400({ username: ["Ya existe un usuario con ese nombre."] }));
    abrir();
    await screen.findByLabelText(/Nombre de usuario/i);
    await rellenar();
    await userEvent.click(screen.getByRole("button", { name: /Crear cuenta/i }));
    await screen.findByText(/Ya existe un usuario/i);
    expect(document.activeElement).toBe(screen.getByLabelText(/Nombre de usuario/i));
  });

  it("sin red al enviar: avisa y enfoca el aviso", async () => {
    api.post.mockResolvedValueOnce({ data: { email: "" } });
    api.post.mockRejectedValueOnce(new Error("Network Error"));
    abrir();
    await screen.findByLabelText(/Nombre de usuario/i);
    await rellenar();
    await userEvent.click(screen.getByRole("button", { name: /Crear cuenta/i }));
    const aviso = await screen.findByRole("alert");
    expect(aviso.textContent).toMatch(/Error de conexión/);
    expect(document.activeElement).toBe(aviso);
  });

  it("un 500 con HTML al enviar muestra un error de servidor, no de conexión", async () => {
    api.post.mockResolvedValueOnce({ data: { email: "" } });
    api.post.mockRejectedValueOnce(Object.assign(new Error("500"), { response: { status: 500, data: "<html>" } }));
    abrir();
    await screen.findByLabelText(/Nombre de usuario/i);
    await rellenar();
    await userEvent.click(screen.getByRole("button", { name: /Crear cuenta/i }));
    expect((await screen.findByRole("alert")).textContent).toMatch(/servidor/);
  });

  it("la nota del email nominativo está enlazada al campo", async () => {
    api.post.mockResolvedValue({ data: { email: "ana@empresa.com" } });
    abrir();
    const email = await screen.findByLabelText(/Correo electrónico/i);
    const ayuda = document.getElementById(email.getAttribute("aria-describedby"));
    expect(ayuda.textContent).toMatch(/para este correo/);
  });
});
