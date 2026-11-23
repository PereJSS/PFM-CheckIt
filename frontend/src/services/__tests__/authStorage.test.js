// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from "vitest";
import { clearAuthTokens, getRolGuardado, guardarRol } from "../authStorage";

beforeEach(() => sessionStorage.clear());

describe("rol recordado en la pestaña (#15)", () => {
  it("guarda solo roles conocidos", () => {
    guardarRol("OPERARIO");
    expect(getRolGuardado()).toBe("OPERARIO");
    guardarRol("ADMINISTRADOR");
    expect(getRolGuardado()).toBe("ADMINISTRADOR");
  });

  it("un rol desconocido o vacío borra el anterior: nunca queda el de otra sesión", () => {
    for (const raro of ["SUPERUSUARIO", "", undefined]) {
      guardarRol("ADMINISTRADOR");
      guardarRol(raro);
      expect(getRolGuardado()).toBeNull();
    }
  });

  it("ignora al leer un valor desconocido escrito a mano", () => {
    sessionStorage.setItem("rol", "<script>");
    expect(getRolGuardado()).toBeNull();
  });

  it("cerrar sesión lo borra", () => {
    guardarRol("OPERARIO");
    clearAuthTokens();
    expect(getRolGuardado()).toBeNull();
  });
});
