// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import ChecklistFlow from "../ChecklistFlow";

const checklist = {
  tiene_checklist: true,
  plantilla_nombre: "Vivienda",
  estancias: [
    {
      id: 1,
      nombre: "Cocina",
      puntos: [
        { id: 10, texto: "Encimera", requiere_foto: false, resultado: "DANO", nota: "", evidencias: [] },
        { id: 11, texto: "Vista general", requiere_foto: true, resultado: null, nota: "", evidencias: [] },
      ],
    },
    { id: 2, nombre: "Baño", puntos: [{ id: 20, texto: "Grifo", requiere_foto: false, resultado: "OK", nota: "", evidencias: [] }] },
  ],
};

const pintar = (props = {}) => {
  const onMarcar = vi.fn();
  const onFoto = vi.fn();
  render(<ChecklistFlow checklist={checklist} fotosPendientes={{}} onMarcar={onMarcar} onFoto={onFoto} {...props} />);
  return { onMarcar, onFoto };
};

afterEach(cleanup);

describe("ChecklistFlow", () => {
  it("lista las estancias con su progreso", () => {
    pintar();
    expect(screen.getByRole("button", { name: /Cocina, 1 de 2/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /Baño, 1 de 1/ })).toBeTruthy();
  });

  it("dentro de una estancia marca puntos con botones que dicen su estado", async () => {
    const { onMarcar } = pintar();
    await userEvent.click(screen.getByRole("button", { name: /Cocina/ }));
    const encimera = screen.getByRole("group", { name: "Encimera" });
    expect(within(encimera).getByRole("button", { name: /^Daño —/ }).getAttribute("aria-pressed")).toBe("true");
    expect(within(encimera).getByRole("button", { name: /^OK —/ }).getAttribute("aria-pressed")).toBe("false");

    const vista = screen.getByRole("group", { name: "Vista general" });
    await userEvent.click(within(vista).getByRole("button", { name: /^OK —/ }));
    expect(onMarcar).toHaveBeenCalledWith(checklist.estancias[0].puntos[1], { resultado: "OK" });
  });

  it("avisa de la foto que falta y de la obligatoria", async () => {
    pintar();
    await userEvent.click(screen.getByRole("button", { name: /Cocina/ }));
    expect(within(screen.getByRole("group", { name: "Encimera" })).getByText(/Falta una foto del daño/)).toBeTruthy();
    expect(within(screen.getByRole("group", { name: "Vista general" })).getByText(/Foto obligatoria/)).toBeTruthy();
  });

  it("una foto guardada en el móvil cuenta", async () => {
    pintar({ fotosPendientes: { 10: 1 } });
    await userEvent.click(screen.getByRole("button", { name: /Cocina/ }));
    const encimera = screen.getByRole("group", { name: "Encimera" });
    expect(within(encimera).queryByText(/Falta una foto/)).toBeNull();
    expect(within(encimera).getByText(/1 foto/)).toBeTruthy();
  });

  it("la nota se guarda al salir del campo, solo si cambió", async () => {
    const { onMarcar } = pintar();
    await userEvent.click(screen.getByRole("button", { name: /Cocina/ }));
    const nota = within(screen.getByRole("group", { name: "Encimera" })).getByLabelText(/Nota/);
    expect(nota.maxLength).toBe(1000);
    fireEvent.blur(nota);
    expect(onMarcar).not.toHaveBeenCalled();
    await userEvent.type(nota, "Quemadura");
    fireEvent.blur(nota);
    expect(onMarcar).toHaveBeenCalledWith(checklist.estancias[0].puntos[0], { nota: "Quemadura" });
  });

  it("añadir foto avisa con el punto y se puede volver a las estancias", async () => {
    const { onFoto } = pintar();
    await userEvent.click(screen.getByRole("button", { name: /Baño/ }));
    await userEvent.click(within(screen.getByRole("group", { name: "Grifo" })).getByRole("button", { name: /Añadir foto/ }));
    expect(onFoto).toHaveBeenCalledWith(checklist.estancias[1].puntos[0], "Baño");
    await userEvent.click(screen.getByRole("button", { name: /Estancias/ }));
    expect(screen.getByRole("button", { name: /Cocina/ })).toBeTruthy();
  });

  it("deshabilitado no deja marcar", async () => {
    const { onMarcar } = pintar({ deshabilitado: true });
    await userEvent.click(screen.getByRole("button", { name: /Baño/ }));
    const ok = within(screen.getByRole("group", { name: "Grifo" })).getByRole("button", { name: /^No aplica —/ });
    expect(ok.disabled).toBe(true);
    await userEvent.click(ok);
    expect(onMarcar).not.toHaveBeenCalled();
  });
});
