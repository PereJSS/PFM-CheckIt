/* global Buffer */
import { describe, expect, it } from "vitest";
import {
  base64ABytes,
  comprobarRespuesta,
  horaSellada,
  huellaSellada,
  normalizarClave,
  verificarInclusion,
} from "../verificacion";
import vectores from "./vectores_verificacion.json";

describe("verificarInclusion, con los vectores de merkle.py", () => {
  it("hay casos válidos e inválidos", () => {
    const validos = vectores.inclusion.filter((caso) => caso.valida).length;
    expect(validos).toBeGreaterThan(0);
    expect(validos).toBeLessThan(vectores.inclusion.length);
  });

  it.each(vectores.inclusion)("$descripcion", async (caso) => {
    await expect(
      verificarInclusion(caso.hoja, caso.indice, caso.total, caso.prueba, caso.raiz),
    ).resolves.toBe(caso.valida);
  });

  it.each([
    ["índice decimal", { indice: 0.5 }],
    ["índice en texto", { indice: "0" }],
    ["total nulo", { total: null }],
    ["hoja que no es hex", { hoja: "zz" }],
    ["hoja corta", { hoja: "ab" }],
    ["raíz nula", { raiz: null }],
    ["prueba que no es lista", { prueba: "ab" }],
    ["nodo que no es hex", { prueba: ["zz"] }],
  ])("%s no es válido y no lanza", async (_, cambio) => {
    const base = vectores.inclusion.find((caso) => caso.valida && caso.total > 1);
    const caso = { ...base, ...cambio };
    await expect(
      verificarInclusion(caso.hoja, caso.indice, caso.total, caso.prueba, caso.raiz),
    ).resolves.toBe(false);
  });
});

describe("huellaSellada, con los vectores de verificador_paquete.py", () => {
  it.each(vectores.sellos)("$descripcion", ({ token, huella }) => {
    expect(huellaSellada(base64ABytes(token))).toBe(huella);
  });

  it.each(vectores.sellos_invalidos)("rechaza: $descripcion", ({ token }) => {
    expect(() => huellaSellada(base64ABytes(token))).toThrow();
  });

  it.each(vectores.sellos)("lee la hora firmada: $descripcion", ({ token, hora }) => {
    expect(horaSellada(base64ABytes(token))).toBe(hora);
  });

  // Se cambia en su sitio, sin alterar los largos: que falle por la hora y no por el DER
  const conHora = (cambiar) => {
    const [{ token, hora }] = vectores.sellos;
    const bytes = Buffer.from(base64ABytes(token));
    const inicio = bytes.indexOf(hora.replace(/[-:T]|Z$/g, ""));
    expect(inicio).toBeGreaterThan(1);
    expect(bytes[inicio - 2]).toBe(0x18);
    const largo = bytes[inicio - 1];
    bytes.write(cambiar(bytes.toString("latin1", inicio, inicio + largo)), inicio, "latin1");
    return bytes;
  };

  it("la manipulación de los tests deja una hora legible si no se cambia nada", () => {
    expect(horaSellada(conHora((hora) => hora))).toBe(vectores.sellos[0].hora);
  });

  it.each([
    ["sin Z", (hora) => hora.replace("Z", "0")],
    ["mes imposible", (hora) => hora.slice(0, 4) + "13" + hora.slice(6)],
    ["día imposible", (hora) => hora.slice(0, 4) + "0231" + hora.slice(8)],
    ["texto", (hora) => "x".repeat(hora.length)],
  ])("rechaza una hora mal formada: %s", (_, cambiar) => {
    expect(() => horaSellada(conHora(cambiar))).toThrow(/hora del sello/);
  });
});

describe("base64ABytes", () => {
  it("decodifica", () => {
    expect([...base64ABytes("AQID")]).toEqual([1, 2, 3]);
  });

  it.each(["no base64!", "AQI", 5, null])("rechaza %s", (valor) => {
    expect(() => base64ABytes(valor)).toThrow();
  });
});

describe("normalizarClave", () => {
  it("quita los espacios y saltos que deja copiar del PDF", () => {
    expect(normalizarClave("  AB cd\nEF\t12 ")).toBe("abcdef12");
  });
});

describe("comprobarRespuesta", () => {
  const r = vectores.respuesta;
  const manifiesto = (cambios = {}) => ({
    vigente: true,
    version: 1,
    arbol: "RFC6962-SHA256",
    raiz: r.raiz,
    indice: r.indice,
    total: r.total,
    prueba: r.prueba,
    sello: {
      token: r.token_raiz,
      timestamp: r.hora,
      url: "https://tsa.example/tsr",
      cualificada: false,
      verificado: true,
    },
    ...cambios,
  });
  const respuesta = (cambios = {}) => ({
    hash: r.foto,
    manifiestos: [manifiesto()],
    sello_individual: { token: r.token_foto, timestamp: r.hora },
    anomalias: 0,
    truncado: false,
    ...cambios,
  });

  it("todo cuadra", async () => {
    const resultado = await comprobarRespuesta(r.foto, respuesta());
    expect(resultado.hashCoincide).toBe(true);
    expect(resultado.manifiestos[0].inclusion).toBe(true);
    expect(resultado.manifiestos[0].sello.estado).toBe("coincide");
    expect(resultado.selloIndividual.estado).toBe("coincide");
    expect(resultado.todoCoincide).toBe(true);
  });

  it("una hora declarada distinta de la firmada no coincide", async () => {
    const resultado = await comprobarRespuesta(
      r.foto,
      respuesta({
        manifiestos: [manifiesto({ sello: { ...manifiesto().sello, timestamp: "2020-01-01T00:00:00+00:00" } })],
      }),
    );
    expect(resultado.manifiestos[0].sello.estado).toBe("coincide");
    expect(resultado.manifiestos[0].sello.horaInterna).toBe(r.hora);
    expect(resultado.manifiestos[0].sello.horaCoincide).toBe(false);
    expect(resultado.horasCoinciden).toBe(false);
    expect(resultado.todoCoincide).toBe(false);
  });

  it("también se compara la hora del sello individual", async () => {
    const resultado = await comprobarRespuesta(
      r.foto,
      respuesta({ sello_individual: { token: r.token_foto, timestamp: "2020-01-01T00:00:00+00:00" } }),
    );
    expect(resultado.selloIndividual.horaCoincide).toBe(false);
    expect(resultado.todoCoincide).toBe(false);
  });

  it("la misma hora con otro formato o con fracción sí coincide", async () => {
    const declarada = r.hora.replace("Z", ".250+00:00");
    const resultado = await comprobarRespuesta(
      r.foto,
      respuesta({ manifiestos: [manifiesto({ sello: { ...manifiesto().sello, timestamp: declarada } })] }),
    );
    expect(resultado.manifiestos[0].sello.horaCoincide).toBe(true);
    expect(resultado.todoCoincide).toBe(true);
  });

  it.each([null, undefined, 5, "mañana"])("sin hora declarada legible (%s) no se compara", async (hora) => {
    const resultado = await comprobarRespuesta(
      r.foto,
      respuesta({ manifiestos: [manifiesto({ sello: { ...manifiesto().sello, timestamp: hora } })] }),
    );
    expect(resultado.manifiestos[0].sello.horaInterna).toBe(r.hora);
    expect(resultado.manifiestos[0].sello.horaCoincide).toBeNull();
    expect(resultado.horasCoinciden).toBe(true);
    expect(resultado.horasSinDeclarar).toBe(true);
    expect(resultado.horasIlegibles).toBe(false);
    expect(resultado.todoCoincide).toBe(false);
  });

  it("la hora sin declarar del sello individual tampoco da todo por comprobado", async () => {
    const resultado = await comprobarRespuesta(
      r.foto,
      respuesta({ sello_individual: { token: r.token_foto, timestamp: null } }),
    );
    expect(resultado.horasSinDeclarar).toBe(true);
    expect(resultado.todoCoincide).toBe(false);
  });

  it("con las horas comprobadas no marca ninguna como pendiente", async () => {
    const resultado = await comprobarRespuesta(r.foto, respuesta());
    expect(resultado.horasSinDeclarar).toBe(false);
    expect(resultado.horasIlegibles).toBe(false);
  });

  it("un sello ausente o ilegible no cuenta como hora pendiente: ya se avisa de él", async () => {
    const resultado = await comprobarRespuesta(
      r.foto,
      respuesta({
        manifiestos: [
          manifiesto({ sello: null }),
          manifiesto({ sello: { ...manifiesto().sello, token: "AQID", timestamp: null } }),
        ],
      }),
    );
    expect(resultado.horasSinDeclarar).toBe(false);
    expect(resultado.horasIlegibles).toBe(false);
  });

  it("no recorre una ruta ni una lista de manifiestos desmesuradas", async () => {
    const caso = vectores.inclusion.find((c) => c.valida);
    await expect(
      verificarInclusion(caso.hoja, caso.indice, caso.total, Array(65).fill(caso.raiz), caso.raiz),
    ).resolves.toBe(false);
    const enorme = await comprobarRespuesta(r.foto, respuesta({ manifiestos: Array(65).fill(manifiesto()) }));
    expect(enorme.datosFiables).toBe(false);
    expect(enorme.manifiestos).toEqual([]);
    const justo = await comprobarRespuesta(r.foto, respuesta({ manifiestos: Array(64).fill(manifiesto()) }));
    expect(justo.datosFiables).toBe(true);
  });

  it("no da por buena una respuesta de otra foto", async () => {
    const resultado = await comprobarRespuesta("0".repeat(64), respuesta());
    expect(resultado.hashCoincide).toBe(false);
    expect(resultado.manifiestos[0].inclusion).toBe(false);
    expect(resultado.todoCoincide).toBe(false);
  });

  it("no acepta que la respuesta diga ser de otra foto aunque la prueba cuadre", async () => {
    const resultado = await comprobarRespuesta(r.foto, respuesta({ hash: "0".repeat(64) }));
    expect(resultado.hashCoincide).toBe(false);
    expect(resultado.manifiestos[0].inclusion).toBe(true);
    expect(resultado.todoCoincide).toBe(false);
  });

  it("el sello bueno no salva una ruta mala", async () => {
    const resultado = await comprobarRespuesta(
      r.foto,
      respuesta({ manifiestos: [manifiesto({ prueba: [] })] }),
    );
    expect(resultado.manifiestos[0].inclusion).toBe(false);
    expect(resultado.manifiestos[0].sello.estado).toBe("coincide");
    expect(resultado.todoCoincide).toBe(false);
  });

  it("detecta una prueba que no lleva a la raíz", async () => {
    const otraRaiz = "f".repeat(64);
    const resultado = await comprobarRespuesta(
      r.foto,
      respuesta({ manifiestos: [manifiesto({ raiz: otraRaiz })] }),
    );
    expect(resultado.manifiestos[0].inclusion).toBe(false);
    expect(resultado.manifiestos[0].sello.estado).toBe("no_coincide");
    expect(resultado.todoCoincide).toBe(false);
  });

  it("detecta un sello de otra raíz", async () => {
    const resultado = await comprobarRespuesta(
      r.foto,
      respuesta({
        manifiestos: [manifiesto({ sello: { ...manifiesto().sello, token: r.token_foto } })],
      }),
    );
    expect(resultado.manifiestos[0].inclusion).toBe(true);
    expect(resultado.manifiestos[0].sello.estado).toBe("no_coincide");
    expect(resultado.todoCoincide).toBe(false);
  });

  it("distingue un sello ilegible de uno ausente", async () => {
    const resultado = await comprobarRespuesta(
      r.foto,
      respuesta({
        manifiestos: [
          manifiesto({ sello: { ...manifiesto().sello, token: "AQID" } }),
          manifiesto({ sello: null }),
        ],
      }),
    );
    expect(resultado.manifiestos.map((m) => m.sello.estado)).toEqual(["ilegible", "ausente"]);
    expect(resultado.todoCoincide).toBe(false);
  });

  it("una hora ilegible no borra que la huella del sello coincide", async () => {
    const bytes = Buffer.from(base64ABytes(r.token_raiz));
    const inicio = bytes.indexOf(r.hora.replace(/[-:T]|Z$/g, ""));
    expect(inicio).toBeGreaterThan(1);
    bytes.write("x".repeat(bytes[inicio - 1]), inicio, "latin1");
    const token = bytes.toString("base64");
    expect(() => horaSellada(base64ABytes(token))).toThrow(/hora del sello/);

    const resultado = await comprobarRespuesta(
      r.foto,
      respuesta({ manifiestos: [manifiesto({ sello: { ...manifiesto().sello, token } })] }),
    );
    const { sello } = resultado.manifiestos[0];
    expect(sello.estado).toBe("coincide");
    expect(sello.horaInterna).toBeNull();
    expect(sello.horaCoincide).toBeNull();
    expect(resultado.horasIlegibles).toBe(true);
    expect(resultado.horasSinDeclarar).toBe(false);
    expect(resultado.todoCoincide).toBe(false);
  });

  it("detecta un sello individual de otro hash", async () => {
    const resultado = await comprobarRespuesta(
      r.foto,
      respuesta({ sello_individual: { token: r.token_raiz, timestamp: null } }),
    );
    expect(resultado.selloIndividual.estado).toBe("no_coincide");
    expect(resultado.todoCoincide).toBe(false);
  });

  it("no da por bueno lo que el servidor declara con anomalías o recorte", async () => {
    expect((await comprobarRespuesta(r.foto, respuesta({ anomalias: 1 }))).todoCoincide).toBe(false);
    expect((await comprobarRespuesta(r.foto, respuesta({ truncado: true }))).todoCoincide).toBe(false);
  });

  it("sin manifiestos no coincide nada", async () => {
    const resultado = await comprobarRespuesta(r.foto, respuesta({ manifiestos: [] }));
    expect(resultado.todoCoincide).toBe(false);
  });

  it.each([
    ["anomalias ausente", { anomalias: undefined }],
    ["anomalias en texto", { anomalias: "0" }],
    ["truncado ausente", { truncado: undefined }],
  ])("%s no se lee como limpio", async (_, cambios) => {
    const resultado = await comprobarRespuesta(r.foto, respuesta(cambios));
    expect(resultado.datosFiables).toBe(false);
    expect(resultado.todoCoincide).toBe(false);
  });

  it.each([null, 5, "x", {}, { manifiestos: "x" }, { manifiestos: [null, 7] }])(
    "aguanta una respuesta mal formada: %j",
    async (datos) => {
      const resultado = await comprobarRespuesta(r.foto, datos);
      expect(resultado.todoCoincide).toBe(false);
    },
  );
});
