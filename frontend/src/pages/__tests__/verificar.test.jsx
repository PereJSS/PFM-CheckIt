// @vitest-environment jsdom
/* global Buffer */
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import Verificar from "../verificar";
import App from "../../App";
import { consultarVerificacion } from "../../services/verificacion";
import { calculateSHA256 } from "../../utils/hash";
import vectores from "../../utils/__tests__/vectores_verificacion.json";

vi.mock("../../services/verificacion", () => ({ consultarVerificacion: vi.fn() }));
vi.mock("../../utils/hash", () => ({ calculateSHA256: vi.fn() }));

const r = vectores.respuesta;
const CLAVE = "c".repeat(64);
const sello = {
  token: r.token_raiz,
  timestamp: r.hora,
  url: "https://tsa.example/tsr",
  cualificada: false,
  verificado: true,
};
const manifiesto = (cambios = {}) => ({
  vigente: true,
  version: 1,
  arbol: "RFC6962-SHA256",
  raiz: r.raiz,
  indice: r.indice,
  total: r.total,
  prueba: r.prueba,
  sello,
  ...cambios,
});
const datos = (cambios = {}) => ({
  hash: r.foto,
  manifiestos: [manifiesto()],
  sello_individual: null,
  anomalias: 0,
  truncado: false,
  ...cambios,
});

async function comprobar(clave = CLAVE) {
  const usuario = userEvent.setup();
  render(
    <MemoryRouter>
      <Verificar />
    </MemoryRouter>,
  );
  await usuario.upload(screen.getByLabelText(/foto/i), new File(["bytes"], "foto.jpg"));
  await usuario.type(screen.getByLabelText(/clave de verificación/i), clave);
  await usuario.click(screen.getByRole("button", { name: /comprobar/i }));
  return usuario;
}

beforeEach(() => {
  calculateSHA256.mockResolvedValue(r.foto);
  consultarVerificacion.mockResolvedValue({ tipo: "ok", datos: datos() });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("Verificar: qué sale del navegador", () => {
  it("solo envía el hash y la clave limpia, nunca el archivo", async () => {
    await comprobar(`  ${"C".repeat(32)} ${"c".repeat(32)}  `);
    await screen.findByText(/incluida en este manifiesto/i);
    expect(consultarVerificacion).toHaveBeenCalledExactlyOnceWith(CLAVE, r.foto);
  });

  it("no pone la clave en la dirección de la página", async () => {
    const antes = window.location.href;
    await comprobar();
    await screen.findByText(/incluida en este manifiesto/i);
    expect(window.location.href).toBe(antes);
    expect(window.location.href).not.toContain(CLAVE);
  });

  it("dice que la foto no sale del navegador", () => {
    render(
      <MemoryRouter>
        <Verificar />
      </MemoryRouter>,
    );
    expect(screen.getByText(/no sale de este navegador/i)).toBeTruthy();
  });

  it("con una clave mal formada no consulta", async () => {
    await comprobar("abc");
    expect(await screen.findByRole("alert")).toHaveProperty(
      "textContent",
      expect.stringMatching(/64 caracteres/),
    );
    expect(consultarVerificacion).not.toHaveBeenCalled();
    expect(calculateSHA256).not.toHaveBeenCalled();
  });

  it("sin foto la pide y no consulta", async () => {
    const usuario = userEvent.setup();
    render(
      <MemoryRouter>
        <Verificar />
      </MemoryRouter>,
    );
    await usuario.type(screen.getByLabelText(/clave de verificación/i), CLAVE);
    await usuario.click(screen.getByRole("button", { name: /comprobar/i }));
    expect((await screen.findByRole("alert")).textContent).toMatch(/elige la foto/i);
    expect(consultarVerificacion).not.toHaveBeenCalled();
  });

  it("si no se puede leer la foto lo dice y no consulta", async () => {
    calculateSHA256.mockRejectedValue(new Error("sin memoria"));
    await comprobar();
    expect((await screen.findByRole("alert")).textContent).toMatch(/leer la foto/i);
    expect(consultarVerificacion).not.toHaveBeenCalled();
  });
});

describe("Verificar: lo que promete", () => {
  it("separa lo calculado aquí de lo declarado por CheckIt, sin veredicto global", async () => {
    await comprobar();
    await screen.findByText(/incluida en este manifiesto/i);
    expect(screen.getByText(/huella del sello coincide con esta raíz; su firma sigue sin comprobar/i)).toBeTruthy();
    expect(screen.getAllByText(/declarado por checkit/i).length).toBeGreaterThan(0);
    expect(screen.queryByText(/\bválid[oa]\b/i)).toBeNull();
    expect(screen.queryByText(/\bauténtic[oa]\b/i)).toBeNull();
  });

  it("avisa de que la firma del sello no se valida aquí", async () => {
    await comprobar();
    await screen.findByText(/incluida en este manifiesto/i);
    expect(screen.getAllByText(/firma.*no se valida en este navegador/i).length).toBeGreaterThan(0);
  });

  it("da el comando de openssl con la raíz y permite descargar el sello", async () => {
    await comprobar();
    await screen.findByText(/incluida en este manifiesto/i);
    const comando = screen.getByText(/openssl ts -verify/);
    expect(comando.textContent).toContain(`-digest ${r.raiz}`);
    expect(comando.textContent).toContain("-token_in -in sello-manifiesto-1.tst");
    expect(screen.getByText(/openssl ts -reply -in sello-manifiesto-1.tst/)).toBeTruthy();
  });

  it("descarga los bytes del sello con su nombre y revoca la URL después", async () => {
    const crear = vi.fn(() => "blob:sello");
    const revocar = vi.fn();
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: crear, revokeObjectURL: revocar }));
    const pulsar = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function () {
      expect(this.download).toBe("sello-manifiesto-1.tst");
      expect(document.body.contains(this)).toBe(true);
      expect(revocar).not.toHaveBeenCalled();
    });
    try {
      const usuario = await comprobar();
      await usuario.click(await screen.findByRole("button", { name: /descargar el sello/i }));
      expect(pulsar).toHaveBeenCalledOnce();
      // Dentro del clic aún no se habría revocado: hay que mirarlo también al volver de él
      expect(revocar).not.toHaveBeenCalled();
      const blob = crear.mock.calls[0][0];
      const leido = await new Promise((resolver, rechazar) => {
        const lector = new FileReader();
        lector.onload = () => resolver(lector.result);
        lector.onerror = rechazar;
        lector.readAsDataURL(blob);
      });
      expect(leido.split(",")[1]).toBe(r.token_raiz);
      expect(screen.getByText(/sello descargado/i)).toBeTruthy();
      await waitFor(() => expect(revocar).toHaveBeenCalledWith("blob:sello"), { timeout: 2000 });
    } finally {
      pulsar.mockRestore();
      vi.unstubAllGlobals();
    }
  });

  it("copia el comando exacto y lo confirma", async () => {
    const usuario = await comprobar();
    await usuario.click(await screen.findByRole("button", { name: /copiar el comando/i }));
    expect(await navigator.clipboard.readText()).toBe(screen.getByText(/openssl ts -verify/).textContent);
    expect(screen.getByText(/comando copiado/i)).toBeTruthy();
  });

  it("si no puede copiar lo dice", async () => {
    const usuario = await comprobar();
    const escribir = vi.spyOn(navigator.clipboard, "writeText").mockRejectedValue(new Error("denegado"));
    await usuario.click(await screen.findByRole("button", { name: /copiar el comando/i }));
    expect(await screen.findByText(/selecciona el comando a mano/i)).toBeTruthy();
    escribir.mockRestore();
  });

  it("muestra la hora firmada del sello, aparte de la declarada", async () => {
    await comprobar();
    await screen.findByText(/incluida en este manifiesto/i);
    const esperada = new Date(r.hora).toLocaleString("es-ES", { timeZone: "UTC" });
    expect(screen.getByText(new RegExp(`dentro del sello: ${esperada}`)).textContent).toMatch(/sin comprobar/);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("nada coincidente se pinta en verde y avisa de que solo la firma cierra la prueba", async () => {
    await comprobar();
    const resumen = await screen.findByText(/hechas en este navegador coinciden/i);
    expect(resumen.textContent).toMatch(/servidor que mintiera/);
    expect(document.body.innerHTML).not.toMatch(/emerald|green/);
  });

  it("presenta el sello de un archivado como archivado, y del más antiguo al vigente", async () => {
    consultarVerificacion.mockResolvedValue({
      tipo: "ok",
      datos: datos({ manifiestos: [manifiesto({ vigente: false }), manifiesto()] }),
    });
    await comprobar();
    const tarjetas = await screen.findAllByRole("article");
    expect(within(tarjetas[0]).getByText(/archivado/i)).toBeTruthy();
    expect(within(tarjetas[1]).getByText(/vigente/i)).toBeTruthy();
    expect(screen.getByText(/primer sello.*anterioridad/i).textContent).toMatch(/declara/i);
  });

  it.each([
    ["hay más manifiestos sin mostrar", { truncado: true, manifiestos: [manifiesto({ vigente: false }), manifiesto()] }],
    ["el primero no incluye la foto", { manifiestos: [manifiesto({ vigente: false, prueba: [] }), manifiesto()] }],
    [
      "el sello del primero no coincide",
      { manifiestos: [manifiesto({ vigente: false, sello: { ...sello, token: r.token_foto } }), manifiesto()] },
    ],
    ["el primero no tiene sello", { manifiestos: [manifiesto({ vigente: false, sello: null }), manifiesto()] }],
  ])("no atribuye la anterioridad al primer sello si %s", async (_, cambios) => {
    consultarVerificacion.mockResolvedValue({ tipo: "ok", datos: datos(cambios) });
    await comprobar();
    expect((await screen.findAllByRole("article")).length).toBe(2);
    expect(screen.queryByText(/anterioridad/i)).toBeNull();
  });

  it("muestra el sello individual de la foto con su propio comando", async () => {
    consultarVerificacion.mockResolvedValue({
      tipo: "ok",
      datos: datos({ sello_individual: { token: r.token_foto, timestamp: "2026-10-22T20:19:34+00:00" } }),
    });
    await comprobar();
    await screen.findByText(/sello individual/i);
    expect(screen.getByText(new RegExp(`-digest ${r.foto}`))).toBeTruthy();
  });
});

describe("Verificar: cuando algo no cuadra", () => {
  it("una prueba que no lleva a la raíz se ve como fallo", async () => {
    consultarVerificacion.mockResolvedValue({
      tipo: "ok",
      datos: datos({ manifiestos: [manifiesto({ prueba: [] })] }),
    });
    await comprobar();
    expect(await screen.findByText(/la foto no está incluida|no lleva a la raíz/i)).toBeTruthy();
    expect((await screen.findByRole("alert")).textContent).toMatch(/no se sostiene/i);
  });

  it("un sello de otra raíz se ve como fallo", async () => {
    consultarVerificacion.mockResolvedValue({
      tipo: "ok",
      datos: datos({ manifiestos: [manifiesto({ sello: { ...sello, token: r.token_foto } })] }),
    });
    await comprobar();
    expect(await screen.findByText(/el sello no es de esta raíz/i)).toBeTruthy();
    expect((await screen.findByRole("alert")).textContent).toMatch(/no se sostiene/i);
  });

  it("una hora declarada que no es la firmada se ve como fallo", async () => {
    consultarVerificacion.mockResolvedValue({
      tipo: "ok",
      datos: datos({
        manifiestos: [manifiesto({ sello: { ...sello, timestamp: "2020-01-01T00:00:00+00:00" } })],
      }),
    });
    await comprobar();
    expect((await screen.findByRole("alert")).textContent).toMatch(/lleva dentro el sello/i);
    expect(screen.queryByText(/hechas en este navegador coinciden/i)).toBeNull();
  });

  it.each([
    ["hora que no es texto", { timestamp: { a: 1 } }],
    ["hora absurda", { timestamp: "mañana" }],
    ["url que no es texto", { url: { a: 1 } }],
    ["cualificada rara", { cualificada: "sí" }],
    ["verificado raro", { verificado: 7 }],
  ])("un dato raro del sello no rompe la página: %s", async (_, cambio) => {
    consultarVerificacion.mockResolvedValue({
      tipo: "ok",
      datos: datos({ manifiestos: [manifiesto({ sello: { ...sello, ...cambio } })] }),
    });
    await comprobar();
    expect(await screen.findByText(/incluida en este manifiesto/i)).toBeTruthy();
  });

  it.each([
    ["sello que no es un objeto", { sello: 5 }],
    ["raíz que no es texto", { raiz: 5 }],
    ["índice raro", { indice: "x" }],
  ])("un manifiesto raro se ve como fallo y no rompe la página: %s", async (_, cambio) => {
    consultarVerificacion.mockResolvedValue({ tipo: "ok", datos: datos({ manifiestos: [manifiesto(cambio)] }) });
    await comprobar();
    expect(await screen.findByRole("article")).toBeTruthy();
    expect(screen.queryByText(/hechas en este navegador coinciden/i)).toBeNull();
  });

  it("un fallo inesperado al recalcular se dice, y no deja la pantalla en blanco", async () => {
    const digest = vi.spyOn(crypto.subtle, "digest").mockRejectedValue(new Error("sin memoria"));
    try {
      await comprobar();
      expect((await screen.findByRole("alert")).textContent).toMatch(/completar la comprobación/i);
      expect(screen.getByRole("button", { name: /comprobar/i }).getAttribute("aria-disabled")).toBe("false");
    } finally {
      digest.mockRestore();
    }
  });

  it("un sello ilegible no ofrece descarga ni comando", async () => {
    consultarVerificacion.mockResolvedValue({
      tipo: "ok",
      datos: datos({ manifiestos: [manifiesto({ sello: { ...sello, token: "AQID" } })] }),
    });
    await comprobar();
    expect(await screen.findByText(/no se puede leer/i)).toBeTruthy();
    expect(screen.queryByText(/openssl ts -verify/)).toBeNull();
    expect(screen.getByRole("alert").textContent).toMatch(/no se sostiene/i);
  });

  it("un sello individual de otra huella se ve como fallo y sin comando", async () => {
    consultarVerificacion.mockResolvedValue({
      tipo: "ok",
      datos: datos({ sello_individual: { token: r.token_raiz, timestamp: null } }),
    });
    await comprobar();
    expect(await screen.findByText(/este sello no es de la huella/i)).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toMatch(/no se sostiene/i);
    expect(screen.queryByText(new RegExp(`-digest ${r.foto}`))).toBeNull();
  });

  it("el resumen tranquilizador solo sale si todo coincide", async () => {
    await comprobar();
    expect(await screen.findByText(/hechas en este navegador coinciden/i)).toBeTruthy();
    cleanup();
    consultarVerificacion.mockResolvedValue({
      tipo: "ok",
      datos: datos({ manifiestos: [manifiesto({ prueba: [] })] }),
    });
    await comprobar();
    await screen.findByRole("alert");
    expect(screen.queryByText(/hechas en este navegador coinciden/i)).toBeNull();
  });

  it("una hora ilegible dentro del sello se avisa y no deja el resumen tranquilizador", async () => {
    const bytes = Buffer.from(r.token_raiz, "base64");
    const inicio = bytes.indexOf(r.hora.replace(/[-:T]|Z$/g, ""));
    expect(inicio).toBeGreaterThan(1);
    bytes.write("x".repeat(bytes[inicio - 1]), inicio, "latin1");
    consultarVerificacion.mockResolvedValue({
      tipo: "ok",
      datos: datos({ manifiestos: [manifiesto({ sello: { ...sello, token: bytes.toString("base64") } })] }),
    });
    await comprobar();
    expect((await screen.findByRole("alert")).textContent).toMatch(/no se ha podido leer la hora/i);
    expect(screen.queryByText(/hechas en este navegador coinciden/i)).toBeNull();
  });

  it("sin hora declarada legible se avisa y no deja el resumen tranquilizador", async () => {
    consultarVerificacion.mockResolvedValue({
      tipo: "ok",
      datos: datos({ manifiestos: [manifiesto({ sello: { ...sello, timestamp: null } })] }),
    });
    await comprobar();
    expect((await screen.findByRole("alert")).textContent).toMatch(/no declara una hora legible/i);
    expect(screen.queryByText(/hechas en este navegador coinciden/i)).toBeNull();
  });

  it("una lista vacía de otra foto no se lee como que esta no tiene manifiesto", async () => {
    consultarVerificacion.mockResolvedValue({
      tipo: "ok",
      datos: datos({ hash: "0".repeat(64), manifiestos: [] }),
    });
    await comprobar();
    expect((await screen.findByRole("alert")).textContent).toMatch(/no se sostiene/i);
    expect(screen.queryByText(/no está en ningún manifiesto/i)).toBeNull();
  });

  it("con anomalías nunca dice que la foto no esté sellada", async () => {
    consultarVerificacion.mockResolvedValue({
      tipo: "ok",
      datos: datos({ manifiestos: [], anomalias: 2 }),
    });
    await comprobar();
    const aviso = await screen.findByRole("alert");
    expect(aviso.textContent).toMatch(/no se ha podido determinar/i);
    expect(screen.queryByText(/no está en ningún manifiesto/i)).toBeNull();
  });

  it("sin manifiestos ni anomalías lo dice sin alarma", async () => {
    consultarVerificacion.mockResolvedValue({ tipo: "ok", datos: datos({ manifiestos: [] }) });
    await comprobar();
    expect(await screen.findByText(/no está en ningún manifiesto/i)).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("con recorte avisa de que hay más", async () => {
    consultarVerificacion.mockResolvedValue({
      tipo: "ok",
      datos: datos({ manifiestos: [], truncado: true }),
    });
    await comprobar();
    expect((await screen.findByRole("alert")).textContent).toMatch(/más manifiestos/i);
    expect(screen.queryByText(/no está en ningún manifiesto/i)).toBeNull();
  });

  it("una respuesta incomprensible no se presenta como resultado", async () => {
    consultarVerificacion.mockResolvedValue({ tipo: "ok", datos: { hash: r.foto } });
    await comprobar();
    expect((await screen.findByRole("alert")).textContent).toMatch(/no se entiende/i);
  });

  it("una respuesta que dice ser de otra foto se ve como fallo", async () => {
    consultarVerificacion.mockResolvedValue({ tipo: "ok", datos: datos({ hash: "0".repeat(64) }) });
    await comprobar();
    expect((await screen.findByRole("alert")).textContent).toMatch(/no se sostiene/i);
  });

  it.each([
    [{ tipo: "no_encontrada" }, /no hay prueba de esta foto.*original/is],
    [{ tipo: "formato" }, /64 caracteres/],
    [{ tipo: "espera", segundos: 42 }, /42 s/],
    [{ tipo: "espera", segundos: null }, /espera un momento/i],
    [{ tipo: "servidor" }, /no ha podido responder/i],
    [{ tipo: "red" }, /conexión/i],
    [{ tipo: "tiempo" }, /despertando/i],
  ])("%j", async (resultado, mensaje) => {
    consultarVerificacion.mockResolvedValue(resultado);
    await comprobar();
    expect((await screen.findByRole("alert")).textContent).toMatch(mensaje);
  });
});

describe("Verificar: uso", () => {
  it("bloquea el botón mientras consulta y borra el resultado anterior", async () => {
    const usuario = await comprobar();
    await screen.findByText(/incluida en este manifiesto/i);

    let responder;
    consultarVerificacion.mockReturnValue(new Promise((resolver) => (responder = resolver)));
    await usuario.click(screen.getByRole("button", { name: /comprobar/i }));

    const ocupado = await screen.findByRole("button", { name: /comprobando/i });
    expect(ocupado.getAttribute("aria-disabled")).toBe("true");
    expect(screen.queryByText(/incluida en este manifiesto/i)).toBeNull();

    // Sigue pudiendo recibir el foco, pero no lanza otra consulta
    await usuario.click(ocupado);
    expect(document.activeElement).toBe(ocupado);
    expect(consultarVerificacion).toHaveBeenCalledTimes(2);

    responder({ tipo: "no_encontrada" });
    await screen.findByRole("alert");
    expect(screen.getByRole("button", { name: /comprobar/i }).getAttribute("aria-disabled")).toBe("false");
  });

  it("no deja cambiar la foto ni la clave mientras consulta, sin quitar el foco a la clave", async () => {
    let responder;
    consultarVerificacion.mockReturnValue(new Promise((resolver) => (responder = resolver)));
    const usuario = userEvent.setup();
    render(
      <MemoryRouter>
        <Verificar />
      </MemoryRouter>,
    );
    const foto = screen.getByLabelText(/foto/i);
    const clave = screen.getByLabelText(/clave de verificación/i);
    await usuario.upload(foto, new File(["bytes"], "foto.jpg"));
    await usuario.type(clave, `${CLAVE}{Enter}`);
    await screen.findByRole("button", { name: /comprobando/i });

    expect(foto.disabled).toBe(true);
    expect(clave.disabled).toBe(false);
    expect(document.activeElement).toBe(clave);
    await usuario.keyboard("a");
    expect(clave.value).toBe(CLAVE);

    responder({ tipo: "ok", datos: datos() });
    await screen.findByText(/incluida en este manifiesto/i);
    expect(foto.disabled).toBe(false);
    expect(clave.readOnly).toBe(false);
  });

  it("reintentar borra el error anterior", async () => {
    consultarVerificacion.mockResolvedValue({ tipo: "red" });
    const usuario = await comprobar();
    await screen.findByRole("alert");
    consultarVerificacion.mockResolvedValue({ tipo: "ok", datos: datos() });
    await usuario.click(screen.getByRole("button", { name: /comprobar/i }));
    await screen.findByText(/incluida en este manifiesto/i);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("los campos tienen etiqueta y el progreso se anuncia", async () => {
    render(
      <MemoryRouter>
        <Verificar />
      </MemoryRouter>,
    );
    expect(screen.getByLabelText(/foto/i).type).toBe("file");
    expect(screen.getByLabelText(/clave de verificación/i)).toBeTruthy();
    expect(document.querySelector('[aria-live="polite"]')).toBeTruthy();
    expect(screen.getByRole("main")).toBeTruthy();
    expect(document.title).toMatch(/verificar una foto/i);
  });

  it("el foco va al resultado, también cuando todo coincide", async () => {
    await comprobar();
    const encabezado = await screen.findByRole("heading", { name: /^resultado$/i, level: 2 });
    await waitFor(() => expect(document.activeElement).toBe(encabezado));
  });

  it("un error de la clave se asocia al campo y le lleva el foco", async () => {
    await comprobar("abc");
    const alerta = await screen.findByRole("alert");
    const campo = screen.getByLabelText(/clave de verificación/i);
    expect(campo.getAttribute("aria-invalid")).toBe("true");
    expect(campo.getAttribute("aria-describedby")).toContain(alerta.id);
    expect(document.activeElement).toBe(campo);
    expect(screen.getByLabelText(/foto/i).getAttribute("aria-invalid")).toBe("false");
  });

  it("un error de la foto se asocia al campo y le lleva el foco", async () => {
    const usuario = userEvent.setup();
    render(
      <MemoryRouter>
        <Verificar />
      </MemoryRouter>,
    );
    await usuario.type(screen.getByLabelText(/clave de verificación/i), CLAVE);
    await usuario.click(screen.getByRole("button", { name: /comprobar/i }));
    const alerta = await screen.findByRole("alert");
    const campo = screen.getByLabelText(/foto/i);
    expect(campo.getAttribute("aria-invalid")).toBe("true");
    expect(campo.getAttribute("aria-describedby")).toContain(alerta.id);
    expect(document.activeElement).toBe(campo);
  });

  it("un error del servidor no señala ningún campo", async () => {
    consultarVerificacion.mockResolvedValue({ tipo: "red" });
    await comprobar();
    await screen.findByRole("alert");
    expect(screen.getByLabelText(/clave de verificación/i).getAttribute("aria-invalid")).toBe("false");
    expect(screen.getByLabelText(/clave de verificación/i).getAttribute("aria-describedby")).not.toContain("error");
  });

  it("corregir un campo retira el resultado anterior", async () => {
    const usuario = await comprobar();
    await screen.findByText(/incluida en este manifiesto/i);
    await usuario.type(screen.getByLabelText(/clave de verificación/i), "a");
    expect(screen.queryByText(/incluida en este manifiesto/i)).toBeNull();
  });
});

describe("Ruta pública", () => {
  it("/verificar se abre sin sesión y sin redirigir al login", async () => {
    sessionStorage.clear();
    window.history.pushState({}, "", "/verificar");
    render(<App />);
    expect(await screen.findByRole("heading", { name: /verificar una foto/i })).toBeTruthy();
    expect(window.location.pathname).toBe("/verificar");
  });
});
