import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import App from "./App.jsx";

// Punto de entrada del frontend: monta la app React en el contenedor #root.
createRoot(document.getElementById("root")).render(
  // StrictMode ayuda a detectar efectos secundarios y patrones inseguros en desarrollo.
  <StrictMode>
    <App />
  </StrictMode>,
);
