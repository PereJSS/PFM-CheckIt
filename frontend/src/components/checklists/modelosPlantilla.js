// Modelos de partida para crear una plantilla de checklist (#19). Solo son un punto de
// partida: el cliente los edita antes de guardar. «Vista general» pide foto siempre.
const p = (texto, requiere_foto = false) => ({ texto, requiere_foto });
const vista = p("Vista general", true);

export const MODELOS = [
  {
    id: "vivienda",
    nombre: "Vivienda",
    descripcion: "Salón, cocina, baño, dormitorios y terraza",
    estructura: {
      estancias: [
        { nombre: "Salón", puntos: [vista, p("Paredes y techo"), p("Suelo"), p("Ventanas y persianas"), p("Mobiliario")] },
        { nombre: "Cocina", puntos: [vista, p("Encimera"), p("Electrodomésticos"), p("Fregadero y grifo"), p("Armarios")] },
        { nombre: "Baño Principal", puntos: [vista, p("Sanitarios"), p("Grifería"), p("Azulejos"), p("Espejo")] },
        { nombre: "Dormitorio 1", puntos: [vista, p("Cama y colchón"), p("Armario"), p("Paredes")] },
        { nombre: "Dormitorio 2", puntos: [vista, p("Cama y colchón"), p("Armario"), p("Paredes")] },
        { nombre: "Terraza", puntos: [vista, p("Suelo"), p("Barandilla"), p("Mobiliario exterior")] },
      ],
    },
  },
  {
    id: "vehiculo",
    nombre: "Vehículo",
    descripcion: "Exterior, interior y mecánica (rent-a-car, flotas)",
    estructura: {
      estancias: [
        {
          nombre: "Exterior",
          puntos: [
            p("Frontal", true), p("Lateral izquierdo", true), p("Lateral derecho", true), p("Trasera", true),
            p("Lunas"), p("Llantas y neumáticos"),
          ],
        },
        { nombre: "Interior", puntos: [vista, p("Salpicadero"), p("Asientos y tapicería"), p("Maletero")] },
        { nombre: "Mecánica", puntos: [p("Cuentakilómetros", true), p("Nivel de combustible", true), p("Testigos del cuadro")] },
      ],
    },
  },
  {
    id: "maquinaria",
    nombre: "Maquinaria",
    descripcion: "Estado general, estructura y elementos de trabajo",
    estructura: {
      estancias: [
        { nombre: "Estado general", puntos: [vista, p("Placa de identificación", true), p("Horómetro", true)] },
        { nombre: "Estructura", puntos: [p("Chasis"), p("Brazo o pluma"), p("Cabina")] },
        { nombre: "Elementos de trabajo", puntos: [p("Orugas o neumáticos"), p("Cazo o implemento"), p("Fugas de aceite")] },
      ],
    },
  },
  {
    id: "vacia",
    nombre: "En blanco",
    descripcion: "Una estancia y un punto para empezar",
    estructura: { estancias: [{ nombre: "Estancia 1", puntos: [vista] }] },
  },
];
