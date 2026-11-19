import { describe, expect, it } from "vitest";
import { esRechazoDefinitivo, motivoDelRechazo } from "../erroresSubida";

const conEstado = (status, data) => ({ response: { status, data } });
const deLaApi = (status) => conEstado(status, { error: "Motivo de la API." });

describe("esRechazoDefinitivo", () => {
  it("sin respuesta (red caída) se reintenta", () => {
    expect(esRechazoDefinitivo(new Error("Network Error"))).toBe(false);
    expect(esRechazoDefinitivo(undefined)).toBe(false);
    expect(esRechazoDefinitivo({ response: { status: "400" } })).toBe(false);
  });

  it.each([399, 500, 502, 503, 504])("un %i se reintenta", (status) => {
    expect(esRechazoDefinitivo(deLaApi(status))).toBe(false);
  });

  // 401: sesión caducada; 408, 425 y 429: pasajeros
  it.each([401, 408, 425, 429])("un %i se reintenta aunque lo diga la API", (status) => {
    expect(esRechazoDefinitivo(deLaApi(status))).toBe(false);
  });

  it.each([400, 403, 404, 409, 418, 422, 499])(
    "un %i con el mensaje de la API no se arregla reintentando",
    (status) => {
      expect(esRechazoDefinitivo(deLaApi(status))).toBe(true);
      expect(esRechazoDefinitivo(conEstado(status, { detail: "No." }))).toBe(true);
    },
  );

  // Un proxy, un WAF o un despliegue a medias no responden con el JSON de la API
  it.each([400, 403, 404, 409])("un %i sin el mensaje de la API se reintenta", (status) => {
    expect(esRechazoDefinitivo(conEstado(status, "<html>Not Found</html>"))).toBe(false);
    expect(esRechazoDefinitivo(conEstado(status, { error: "  " }))).toBe(false);
    expect(esRechazoDefinitivo(conEstado(status))).toBe(false);
  });

  it("un 413 es definitivo venga de donde venga: la foto no va a encoger", () => {
    expect(esRechazoDefinitivo(conEstado(413, "<html>Too large</html>"))).toBe(true);
  });
});

describe("motivoDelRechazo", () => {
  it("usa el mensaje del servidor", () => {
    expect(
      motivoDelRechazo(
        conEstado(400, { error: "descripcion no puede tener más de 1000 caracteres." }),
      ),
    ).toBe("descripcion no puede tener más de 1000 caracteres.");
    expect(motivoDelRechazo(conEstado(403, { detail: "Sin permiso." }))).toBe(
      "Sin permiso.",
    );
  });

  it("un 413 sin mensaje dice que la foto es demasiado grande", () => {
    expect(motivoDelRechazo(conEstado(413, "<html>Too large</html>"))).toBe(
      "La foto es demasiado grande para el servidor.",
    );
  });

  it("sin mensaje útil, dice el código", () => {
    expect(motivoDelRechazo(conEstado(400, { error: ["a"] }))).toBe(
      "El servidor rechazó la foto (código 400).",
    );
  });
});
