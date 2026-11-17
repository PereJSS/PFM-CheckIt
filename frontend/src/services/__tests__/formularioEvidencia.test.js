// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { formularioEvidencia } from "../formularioEvidencia";

const foto = new File(["x"], "foto.jpg", { type: "image/jpeg" });

const campos = (fd) =>
  Object.fromEntries([...fd.entries()].filter(([k]) => k !== "foto"));

describe("formularioEvidencia", () => {
  it("manda estancia y descripción por separado", () => {
    const fd = formularioEvidencia({
      file: foto,
      room: "Cocina",
      description: "Grieta junto al fregadero",
      hash: "a".repeat(64),
      capturedAt: "2026-11-17T10:00:00.000Z",
    });
    expect(campos(fd)).toEqual({
      estancia: "Cocina",
      descripcion: "Grieta junto al fregadero",
      hash_sha256: "a".repeat(64),
      capturada_en: "2026-11-17T10:00:00.000Z",
    });
    expect(fd.get("foto").name).toBe("foto.jpg");
  });

  it("sin descripción no la rellena con la estancia", () => {
    const fd = formularioEvidencia({ file: foto, room: "Baño Principal" });
    expect(fd.get("estancia")).toBe("Baño Principal");
    expect(fd.has("descripcion")).toBe(false);
  });

  it("omite lo que no hay: sin estancia, hash ni hora", () => {
    const fd = formularioEvidencia({ file: foto, description: "Algo" });
    expect(campos(fd)).toEqual({ descripcion: "Algo" });
  });

  it("da nombre a un archivo sin nombre (registros antiguos de la cola)", () => {
    const blob = new Blob(["x"], { type: "image/jpeg" });
    const fd = formularioEvidencia({ file: blob, room: "Salón" });
    expect(fd.get("foto").name).toMatch(/^evidencia_\d+\.jpg$/);
  });

  it("hash y hora nulos no se mandan", () => {
    const fd = formularioEvidencia({
      file: foto,
      room: "Cocina",
      hash: null,
      capturedAt: null,
    });
    expect(campos(fd)).toEqual({ estancia: "Cocina" });
  });

  it("un archivo con nombre vacío recibe uno de respaldo", () => {
    const sinNombre = new File(["x"], "", { type: "image/jpeg" });
    const fd = formularioEvidencia({ file: sinNombre, room: "Salón" });
    expect(fd.get("foto").name).toMatch(/^evidencia_\d+\.jpg$/);
  });
});
