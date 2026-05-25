import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import api from "../services/api";

const FORM_EMPTY = {
  username: "",
  email: "",
  first_name: "",
  last_name: "",
  password: "",
  password2: "",
};

export default function Register() {
  const navigate = useNavigate();
  const [form, setForm] = useState(FORM_EMPTY);
  const [errors, setErrors] = useState({});
  const [loading, setLoading] = useState(false);
  const [success, setSuccess] = useState(false);

  const handleChange = (e) => {
    setForm({ ...form, [e.target.name]: e.target.value });
    setErrors((prev) => ({ ...prev, [e.target.name]: undefined }));
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    setErrors({});
    try {
      await api.post("/auth/register/", form);
      setSuccess(true);
      setTimeout(() => navigate("/login"), 2000);
    } catch (err) {
      if (err.response?.data) {
        setErrors(err.response.data);
      } else {
        setErrors({
          non_field_errors: "Error de conexión. Inténtalo de nuevo.",
        });
      }
    } finally {
      setLoading(false);
    }
  };

  const fieldError = (field) =>
    errors[field] ? (
      <p className="mt-1 text-xs text-red-600">{errors[field]}</p>
    ) : null;

  if (success) {
    return (
      <div className="min-h-screen bg-slate-50 flex items-center justify-center px-4">
        <div className="text-center">
          <div className="w-12 h-12 rounded-full bg-emerald-100 flex items-center justify-center mx-auto mb-4">
            <svg
              className="w-6 h-6 text-emerald-600"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M5 13l4 4L19 7"
              />
            </svg>
          </div>
          <h2 className="text-lg font-semibold text-slate-800">
            ¡Cuenta creada!
          </h2>
          <p className="text-sm text-slate-500 mt-1">
            Redirigiendo al inicio de sesión…
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-slate-50 flex items-center justify-center px-4 py-12">
      <div className="w-full max-w-md">
        {/* Marca */}
        <div className="text-center mb-8">
          <span className="text-3xl font-bold tracking-tight text-slate-900">
            CheckIt
          </span>
          <p className="mt-1 text-sm text-slate-500">
            Plataforma de inspecciones certificadas
          </p>
        </div>

        <div className="bg-white rounded-2xl shadow-sm border border-slate-200 p-8">
          <h1 className="text-lg font-semibold text-slate-800 mb-6">
            Crear cuenta de administrador
          </h1>

          <form onSubmit={handleSubmit} className="space-y-4">
            {/* Nombre y apellidos */}
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs font-medium text-slate-500 mb-1">
                  Nombre
                </label>
                <input
                  name="first_name"
                  value={form.first_name}
                  onChange={handleChange}
                  placeholder="Ana"
                  className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-800 focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
                {fieldError("first_name")}
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-500 mb-1">
                  Apellidos
                </label>
                <input
                  name="last_name"
                  value={form.last_name}
                  onChange={handleChange}
                  placeholder="García"
                  className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-800 focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
                {fieldError("last_name")}
              </div>
            </div>

            {/* Usuario */}
            <div>
              <label className="block text-xs font-medium text-slate-500 mb-1">
                Nombre de usuario <span className="text-red-500">*</span>
              </label>
              <input
                name="username"
                value={form.username}
                onChange={handleChange}
                required
                placeholder="ana_garcia"
                autoComplete="username"
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-800 focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
              {fieldError("username")}
            </div>

            {/* Email */}
            <div>
              <label className="block text-xs font-medium text-slate-500 mb-1">
                Correo electrónico
              </label>
              <input
                name="email"
                type="email"
                value={form.email}
                onChange={handleChange}
                placeholder="ana@empresa.com"
                autoComplete="email"
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-800 focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
              {fieldError("email")}
            </div>

            {/* Contraseña */}
            <div>
              <label className="block text-xs font-medium text-slate-500 mb-1">
                Contraseña <span className="text-red-500">*</span>
              </label>
              <input
                name="password"
                type="password"
                value={form.password}
                onChange={handleChange}
                required
                autoComplete="new-password"
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-800 focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
              {fieldError("password")}
            </div>

            {/* Confirmar contraseña */}
            <div>
              <label className="block text-xs font-medium text-slate-500 mb-1">
                Confirmar contraseña <span className="text-red-500">*</span>
              </label>
              <input
                name="password2"
                type="password"
                value={form.password2}
                onChange={handleChange}
                required
                autoComplete="new-password"
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-800 focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
              {fieldError("password2")}
            </div>

            {errors.non_field_errors && (
              <p className="text-xs text-red-600">{errors.non_field_errors}</p>
            )}

            <button
              type="submit"
              disabled={loading}
              className="w-full py-2.5 rounded-lg bg-blue-600 text-white text-sm font-semibold hover:bg-blue-700 disabled:opacity-50 transition mt-2"
            >
              {loading ? "Creando cuenta…" : "Crear cuenta"}
            </button>
          </form>
        </div>

        <p className="text-center text-sm text-slate-500 mt-6">
          ¿Ya tienes cuenta?{" "}
          <Link
            to="/login"
            className="text-blue-600 font-medium hover:underline"
          >
            Inicia sesión
          </Link>
        </p>
      </div>
    </div>
  );
}
