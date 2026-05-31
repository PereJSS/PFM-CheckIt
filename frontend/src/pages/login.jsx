import { useState } from "react";
import { Link } from "react-router-dom";
import api from "../services/api";
import { setAuthTokens } from "../services/authStorage";

// Acepta roles legacy para evitar fallos de redirección por variantes de nombre.
const isAdminRole = (role) => {
  const normalized = String(role || "")
    .trim()
    .toUpperCase();
  return normalized === "ADMINISTRADOR" || normalized === "ADMIN";
};

export default function Login() {
  // Estado del formulario de acceso y flags de UX.
  const [credentials, setCredentials] = useState({
    username: "",
    password: "",
  });
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);

  // Actualiza campo por nombre para reutilizar handler en ambos inputs.
  const handleChange = (e) => {
    setCredentials({ ...credentials, [e.target.name]: e.target.value });
  };

  // Login en dos pasos: obtener tokens y luego consultar perfil para decidir ruta.
  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      // 1) Solicita JWT al backend y persiste tokens para futuras llamadas.
      const response = await api.post("/auth/login/", credentials);
      setAuthTokens({
        accessToken: response.data.access,
        refreshToken: response.data.refresh,
      });

      // 2) Carga el usuario autenticado para enrutar según rol.
      const meResponse = await api.get("/auth/me/");
      const role = meResponse.data.role;
      window.location.href = isAdminRole(role) ? "/" : "/operario";
    } catch (err) {
      // Mensaje genérico para no filtrar detalles de autenticación.
      setError("Credenciales incorrectas. Inténtalo de nuevo.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-slate-50 flex items-center justify-center px-4">
      <div className="w-full max-w-sm">
        {/* Logo / marca */}
        <div className="text-center mb-10">
          <span className="text-3xl font-bold tracking-tight text-slate-900">
            CheckIt
          </span>
          <p className="mt-1 text-sm text-slate-500">
            Plataforma de inspecciones certificadas
          </p>
        </div>

        <div className="bg-white rounded-2xl shadow-sm border border-slate-200 p-8">
          <h1 className="text-lg font-semibold text-slate-800 mb-6">
            Iniciar sesión
          </h1>

          {error && (
            <div className="mb-4 px-4 py-3 rounded-lg bg-red-50 border border-red-200 text-sm text-red-700">
              {error}
            </div>
          )}

          <form onSubmit={handleSubmit} className="flex flex-col gap-4">
            <div>
              {/* Campo de usuario */}
              <label className="block text-xs font-medium text-slate-600 mb-1.5">
                Usuario
              </label>
              <input
                type="text"
                name="username"
                placeholder="nombre de usuario"
                onChange={handleChange}
                required
                className="w-full px-3 py-2.5 rounded-lg border border-slate-300 text-sm text-slate-900 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent transition"
              />
            </div>

            <div>
              {/* Campo de contraseña */}
              <label className="block text-xs font-medium text-slate-600 mb-1.5">
                Contraseña
              </label>
              <input
                type="password"
                name="password"
                placeholder="••••••••"
                onChange={handleChange}
                required
                className="w-full px-3 py-2.5 rounded-lg border border-slate-300 text-sm text-slate-900 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent transition"
              />
            </div>

            {/* Botón bloqueado mientras la petición de login está en curso. */}
            <button
              type="submit"
              disabled={loading}
              className="mt-2 w-full py-2.5 rounded-lg bg-blue-600 text-white text-sm font-semibold hover:bg-blue-700 active:bg-blue-800 disabled:opacity-50 transition"
            >
              {loading ? "Entrando..." : "Entrar"}
            </button>
          </form>
        </div>

        <p className="text-center text-sm text-slate-500 mt-6">
          ¿No tienes cuenta?{" "}
          <Link
            to="/register"
            className="text-blue-600 font-medium hover:underline"
          >
            Regístrate
          </Link>
        </p>
      </div>
    </div>
  );
}
