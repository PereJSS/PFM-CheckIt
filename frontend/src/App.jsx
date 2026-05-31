import { useEffect, useMemo, useState } from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import Login from "./pages/login";
import Register from "./pages/register";
import AdminPage from "./pages/admin";
import InspectionPage from "./pages/inspeccion";
import api from "./services/api";
import { clearAuthTokens, getAccessToken } from "./services/authStorage";

// Normaliza el rol para comparar sin depender de mayúsculas/espacios del backend.
function normalizeRole(role) {
  return String(role || "")
    .trim()
    .toUpperCase();
}

// Protege rutas por JWT y por rol para evitar entrar a vistas no permitidas.
function ProtectedRoute({ children, allowedRoles }) {
  // Si no hay token, no hace falta esperar validación remota.
  const token = getAccessToken();
  // Mientras consulta /auth/me, evita parpadeos o redirecciones prematuras.
  const [isLoading, setIsLoading] = useState(Boolean(token));
  // Rol resuelto desde backend para autorización fina por ruta.
  const [role, setRole] = useState(null);

  // Pre-normaliza roles permitidos para comparar con include de forma consistente.
  const normalizedAllowed = useMemo(
    () => (allowedRoles || []).map((r) => normalizeRole(r)),
    [allowedRoles],
  );

  useEffect(() => {
    if (!token) {
      setIsLoading(false);
      return;
    }

    // Valida sesión vigente y obtiene rol real del usuario autenticado.
    api
      .get("/auth/me/")
      .then((res) => setRole(normalizeRole(res.data?.role)))
      .catch(() => {
        // Si el token es inválido/expirado, limpiamos sesión local.
        clearAuthTokens();
        setRole(null);
      })
      .finally(() => setIsLoading(false));
  }, [token]);

  // Sin JWT no se permite continuar: se fuerza login.
  if (!token) {
    return <Navigate to="/login" replace />;
  }

  // Estado intermedio mientras se confirma identidad/rol.
  if (isLoading) {
    return (
      <div className="min-h-screen bg-slate-50 flex items-center justify-center text-sm text-slate-500">
        Validando sesión...
      </div>
    );
  }

  // Si el rol autenticado no está autorizado para la ruta, redirige a su panel.
  if (normalizedAllowed.length > 0 && !normalizedAllowed.includes(role)) {
    if (role === "ADMINISTRADOR" || role === "ADMIN") {
      return <Navigate to="/" replace />;
    }
    return <Navigate to="/operario" replace />;
  }

  // Caso permitido: renderiza el contenido protegido.
  return children;
}

export default function App() {
  // Router raíz: define rutas públicas y privadas de la SPA.
  return (
    <BrowserRouter>
      <Routes>
        {/* Rutas públicas */}
        <Route path="/login" element={<Login />} />
        <Route path="/register" element={<Register />} />

        {/* Ruta protegida para el operario */}
        <Route
          path="/operario"
          element={
            <ProtectedRoute allowedRoles={["OPERARIO"]}>
              <InspectionPage />
            </ProtectedRoute>
          }
        />

        {/* Ruta principal protegida por JWT (Panel de Administrador) */}
        <Route
          path="/"
          element={
            <ProtectedRoute allowedRoles={["ADMINISTRADOR", "ADMIN"]}>
              <AdminPage />
            </ProtectedRoute>
          }
        />

        {/* Cualquier ruta desconocida redirige al login */}
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
