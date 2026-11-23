import { useEffect, useMemo, useState } from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import Login from "./pages/login";
import Register from "./pages/register";
import AdminPage from "./pages/admin";
import InspectionPage from "./pages/inspeccion";
import Verificar from "./pages/verificar";
import AvisoActualizacion from "./components/AvisoActualizacion";
import api from "./services/api";
import { clearAuthTokens, getAccessToken, getRolGuardado, guardarRol } from "./services/authStorage";

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

  // Sin red y sin rol recordado: se avisa en vez de cerrar la sesión
  const [sinConexion, setSinConexion] = useState(false);
  const [intento, setIntento] = useState(0);

  useEffect(() => {
    // Sin token isLoading ya parte en false (ver useState), no hay nada que validar.
    if (!token) return;

    // Valida sesión vigente y obtiene rol real del usuario autenticado.
    let vigente = true;
    api
      .get("/auth/me/")
      .then((res) => {
        if (!vigente) return;
        const rol = normalizeRole(res.data?.role);
        guardarRol(rol);
        setRole(rol);
        setSinConexion(false);
      })
      .catch((error) => {
        if (!vigente) return;
        const estado = error.response?.status;
        // Solo un rechazo del servidor cierra la sesión (token inválido o usuario de baja)
        if (estado === 401 || estado === 403) {
          clearAuthTokens();
          setRole(null);
          return;
        }
        // Sin red, 5xx o 429 (#15): la sesión sigue. El rol recordado solo elige la
        // pantalla; cada petición a la API la autoriza el servidor.
        const recordado = getRolGuardado();
        if (recordado) setRole(normalizeRole(recordado));
        else setSinConexion(true);
      })
      .finally(() => {
        if (vigente) setIsLoading(false);
      });
    return () => {
      vigente = false;
    };
  }, [token, intento]);

  const reintentar = () => {
    setSinConexion(false);
    setIsLoading(true);
    setIntento((n) => n + 1);
  };

  // Sin JWT no se permite continuar: se fuerza login.
  if (!token) {
    return <Navigate to="/login" replace />;
  }

  // Estado intermedio mientras se confirma identidad/rol.
  if (isLoading) {
    return (
      <div role="status" className="min-h-screen bg-slate-50 flex items-center justify-center text-sm text-slate-600">
        Validando sesión...
      </div>
    );
  }

  if (sinConexion) {
    return (
      <div className="min-h-screen bg-slate-50 flex flex-col items-center justify-center gap-4 p-4 text-center">
        <p role="alert" className="text-slate-800">
          No se pudo comprobar la sesión: no hay conexión. Las fotos guardadas en este móvil se conservan.
        </p>
        <button
          type="button"
          onClick={reintentar}
          className="min-h-11 px-4 rounded-lg bg-blue-600 text-white font-semibold hover:bg-blue-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600"
        >
          Reintentar
        </button>
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
      {/* PWA (#15): versión nueva o lista para usar sin conexión; arriba y en el flujo */}
      <AvisoActualizacion />
      <Routes>
        {/* Rutas públicas */}
        <Route path="/login" element={<Login />} />
        <Route path="/register" element={<Register />} />
        <Route path="/verificar" element={<Verificar />} />

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
