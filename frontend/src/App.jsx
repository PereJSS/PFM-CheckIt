import { useEffect, useMemo, useState } from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import Login from "./pages/login";
import Register from "./pages/register";
import AdminPage from "./pages/admin";
import InspectionPage from "./pages/inspeccion";
import api from "./services/api";
import { clearAuthTokens, getAccessToken } from "./services/authStorage";

function normalizeRole(role) {
  return String(role || "")
    .trim()
    .toUpperCase();
}

// Protege rutas por JWT y por rol para evitar entrar a vistas no permitidas.
function ProtectedRoute({ children, allowedRoles }) {
  const token = getAccessToken();
  const [isLoading, setIsLoading] = useState(Boolean(token));
  const [role, setRole] = useState(null);

  const normalizedAllowed = useMemo(
    () => (allowedRoles || []).map((r) => normalizeRole(r)),
    [allowedRoles],
  );

  useEffect(() => {
    if (!token) {
      setIsLoading(false);
      return;
    }

    api
      .get("/auth/me/")
      .then((res) => setRole(normalizeRole(res.data?.role)))
      .catch(() => {
        clearAuthTokens();
        setRole(null);
      })
      .finally(() => setIsLoading(false));
  }, [token]);

  if (!token) {
    return <Navigate to="/login" replace />;
  }

  if (isLoading) {
    return (
      <div className="min-h-screen bg-slate-50 flex items-center justify-center text-sm text-slate-500">
        Validando sesión...
      </div>
    );
  }

  if (normalizedAllowed.length > 0 && !normalizedAllowed.includes(role)) {
    if (role === "ADMINISTRADOR" || role === "ADMIN") {
      return <Navigate to="/" replace />;
    }
    return <Navigate to="/operario" replace />;
  }

  return children;
}

export default function App() {
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
