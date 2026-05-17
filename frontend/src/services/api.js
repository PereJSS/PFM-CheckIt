// este archivo se encarga de configurar una instancia de axios para hacer solicitudes HTTP a la API del backend.
//  La baseURL se establece en "/api/v1", lo que significa que todas las solicitudes realizadas con esta instancia de axios se dirigirán a esa ruta base.

import axios from "axios";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import Login from "./pages/Login";
import AdminPage from "./pages/AdminPage";

// Componente para proteger las rutas privadas
function ProtectedRoute({ children }) {
  const token = localStorage.getItem("access_token");
  if (!token) {
    return <Navigate to="/login" replace />;
  }
  return children;
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        {/* Ruta pública */}
        <Route path="/login" element={<Login />} />

        {/* Ruta protegida por JWT */}
        <Route
          path="/"
          element={
            <ProtectedRoute>
              <AdminPage />
            </ProtectedRoute>
          }
        />
      </Routes>
    </BrowserRouter>
  );
}
