import { useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import api from "../services/api";

// Alta de administrador (cliente), solo con invitación (B3). El enlace trae el token tras `#`
// (`/register#invitacion=…`): el navegador no lo envía al servidor ni en el Referer.

const FORM_EMPTY = {
  username: "",
  email: "",
  first_name: "",
  last_name: "",
  password: "",
  password2: "",
};

const INPUT =
  "w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-800 placeholder:text-slate-500 focus:outline-none focus:ring-2 focus:ring-blue-500 aria-invalid:border-red-600";
const LABEL = "block text-xs font-medium text-slate-600 mb-1";

const leerInvitacion = (hash) =>
  new URLSearchParams(hash.replace(/^#/, "")).get("invitacion") || "";

// DRF devuelve listas de mensajes por campo
const texto = (valor) => (Array.isArray(valor) ? valor.join(" ") : valor);

const CAMPOS = Object.keys(FORM_EMPTY);
const ERROR_SERVIDOR = "El servidor no ha podido completar la petición. Inténtalo de nuevo.";

// Mensaje cuando la comprobación no da un 400 (invitación no válida)
const motivoFallo = (err) => {
  if (!err.response) return "No se pudo comprobar la invitación. Revisa la conexión.";
  if (err.response.status === 429) return err.message; // el interceptor ya lo redacta
  return "No se pudo comprobar la invitación por un error del servidor.";
};

function Marco({ children }) {
  return (
    <div className="min-h-screen bg-slate-50 flex items-center justify-center px-4 py-12">
      <div className="w-full max-w-md">
        <div className="text-center mb-8">
          <span className="text-3xl font-bold tracking-tight text-slate-900">
            CheckIt
          </span>
          <p className="mt-1 text-sm text-slate-600">
            Plataforma de inspecciones certificadas
          </p>
        </div>
        {children}
        <p className="text-center text-sm text-slate-600 mt-6">
          ¿Ya tienes cuenta?{" "}
          <Link to="/login" className="text-blue-600 font-medium hover:underline">
            Inicia sesión
          </Link>
        </p>
      </div>
    </div>
  );
}

// El título recibe el foco en cada cambio de estado: si no, se perdería al desmontarse la
// pantalla anterior (WCAG 2.4.3)
function Titulo({ children, className = "mb-2" }) {
  const ref = useRef(null);
  useEffect(() => {
    ref.current?.focus();
  }, []);
  return (
    <h1
      ref={ref}
      tabIndex={-1}
      className={`text-lg font-semibold text-slate-800 focus:outline-none ${className}`}
    >
      {children}
    </h1>
  );
}

function Aviso({ titulo, children }) {
  return (
    <Marco>
      <div className="bg-white rounded-2xl shadow-sm border border-slate-200 p-8">
        <Titulo>{titulo}</Titulo>
        {children}
      </div>
    </Marco>
  );
}

export default function Register() {
  const navigate = useNavigate();
  const location = useLocation();
  // Se lee una vez: justo después se quita de la barra de direcciones
  const [invitacion] = useState(() => leerInvitacion(location.hash));
  // sin | comprobando | invalida | red | lista
  const [estado, setEstado] = useState(invitacion ? "comprobando" : "sin");
  const [fallo, setFallo] = useState("");
  const [emailFijo, setEmailFijo] = useState("");
  const [form, setForm] = useState(FORM_EMPTY);
  const [errors, setErrors] = useState({});
  const [loading, setLoading] = useState(false);
  const [success, setSuccess] = useState(false);
  const [intento, setIntento] = useState(0);

  // Que el token no quede en el historial ni a la vista al compartir pantalla
  useEffect(() => {
    if (location.hash) navigate(location.pathname, { replace: true });
  }, [location.hash, location.pathname, navigate]);

  // Comprueba la invitación sin gastarla; `intento` la repite tras un fallo de red
  useEffect(() => {
    if (!invitacion) return undefined;
    let vigente = true;
    api
      .post("/auth/invitacion/", { invitacion })
      .then(({ data }) => {
        if (!vigente) return;
        const email = data?.email || "";
        setEmailFijo(email);
        setForm((prev) => ({ ...prev, email }));
        setEstado("lista");
      })
      .catch((err) => {
        if (!vigente) return;
        if (err.response?.status === 400) {
          setEstado("invalida");
        } else {
          setFallo(motivoFallo(err));
          setEstado("red");
        }
      });
    return () => {
      vigente = false;
    };
  }, [invitacion, intento]);

  const reintentar = () => {
    setEstado("comprobando");
    setIntento((n) => n + 1);
  };

  const handleChange = (e) => {
    const { name, value } = e.target;
    setForm((prev) => ({ ...prev, [name]: value }));
    setErrors((prev) => ({ ...prev, [name]: undefined }));
  };

  // Tras un envío fallido, el foco va al primer campo con error o al aviso general
  const formRef = useRef(null);
  const enfocarError = useRef(false);
  useEffect(() => {
    if (!enfocarError.current) return;
    enfocarError.current = false;
    formRef.current?.querySelector('[aria-invalid="true"], [data-error-general]')?.focus();
  }, [errors]);

  // Tras el alta, al login; se cancela si se sale antes de la pantalla
  useEffect(() => {
    if (!success) return undefined;
    const t = setTimeout(() => navigate("/login"), 4000);
    return () => clearTimeout(t);
  }, [success, navigate]);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    setErrors({});
    try {
      await api.post("/auth/register/", { ...form, invitacion });
      setSuccess(true);
    } catch (err) {
      const data = err.response?.data;
      let nuevos;
      if (!err.response) {
        nuevos = { non_field_errors: "Error de conexión. Inténtalo de nuevo." };
      } else if (data && typeof data === "object") {
        // Lo que no se pinta junto a un campo ni en el aviso general no debe perderse
        const visible =
          CAMPOS.some((c) => data[c]) || data.invitacion || data.non_field_errors || data.detail;
        nuevos = visible ? data : { non_field_errors: ERROR_SERVIDOR };
      } else {
        nuevos = { non_field_errors: ERROR_SERVIDOR };
      }
      enfocarError.current = true;
      setErrors(nuevos);
    } finally {
      setLoading(false);
    }
  };

  // Etiqueta asociada, aria-invalid y mensaje enlazado con aria-describedby
  const campo = (name, etiqueta, props = {}, ayuda = "") => {
    const id = `registro-${name}`;
    const error = errors[name];
    const descritoPor = [ayuda && `${id}-ayuda`, error && `${id}-error`].filter(Boolean).join(" ");
    return (
      <div>
        <label htmlFor={id} className={LABEL}>
          {etiqueta}
          {props.required && <span className="text-red-600"> *</span>}
        </label>
        <input
          id={id}
          name={name}
          value={form[name]}
          onChange={handleChange}
          {...props}
          aria-invalid={error ? "true" : undefined}
          aria-describedby={descritoPor || undefined}
          className={`${INPUT} read-only:bg-slate-100 read-only:text-slate-600`}
        />
        {ayuda && (
          <p id={`${id}-ayuda`} className="mt-1 text-xs text-slate-600">
            {ayuda}
          </p>
        )}
        {error && (
          <p id={`${id}-error`} className="mt-1 text-sm text-red-700">
            <span aria-hidden="true">⚠ </span>
            {texto(error)}
          </p>
        )}
      </div>
    );
  };

  if (estado === "sin") {
    return (
      <Aviso titulo="Necesitas una invitación">
        <p className="text-sm text-slate-600">
          Las cuentas de CheckIt se crean con un enlace de invitación. Si
          esperabas uno, ábrelo tal como te llegó o pide uno nuevo.
        </p>
      </Aviso>
    );
  }

  if (estado === "comprobando") {
    return (
      <Aviso titulo="Crear cuenta de administrador">
        <p role="status" className="text-sm text-slate-600">
          Comprobando la invitación…
        </p>
      </Aviso>
    );
  }

  if (estado === "invalida") {
    return (
      <Aviso titulo="Invitación no válida">
        <p className="text-sm text-slate-600">
          La invitación no es válida o ha caducado. Pide un enlace nuevo.
        </p>
      </Aviso>
    );
  }

  if (estado === "red") {
    return (
      <Aviso titulo="No se pudo comprobar la invitación">
        <p className="text-sm text-slate-600 mb-4">{fallo}</p>
        <button
          type="button"
          onClick={reintentar}
          className="w-full py-2.5 rounded-lg bg-blue-600 text-white text-sm font-semibold hover:bg-blue-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"
        >
          Reintentar
        </button>
      </Aviso>
    );
  }

  if (success) {
    return (
      <div className="min-h-screen bg-slate-50 flex items-center justify-center px-4">
        <div className="text-center" role="status">
          <div className="w-12 h-12 rounded-full bg-emerald-100 flex items-center justify-center mx-auto mb-4">
            <svg
              aria-hidden="true"
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
          <Titulo className="">¡Cuenta creada!</Titulo>
          <p className="text-sm text-slate-600 mt-1">
            Te llevamos al inicio de sesión…{" "}
            <Link to="/login" className="text-blue-600 font-medium hover:underline">
              Ir ahora
            </Link>
          </p>
        </div>
      </div>
    );
  }

  // Errores que no son de un campo del formulario (la invitación incluida)
  const general = errors.invitacion || errors.non_field_errors || errors.detail;

  return (
    <Marco>
      <div className="bg-white rounded-2xl shadow-sm border border-slate-200 p-8">
        <Titulo className="mb-6">Crear cuenta de administrador</Titulo>

        <form ref={formRef} onSubmit={handleSubmit} className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            {campo("first_name", "Nombre", { placeholder: "Ana", autoComplete: "given-name" })}
            {campo("last_name", "Apellidos", { placeholder: "García", autoComplete: "family-name" })}
          </div>
          {campo("username", "Nombre de usuario", {
            required: true,
            placeholder: "ana_garcia",
            autoComplete: "username",
          })}
          {campo(
            "email",
            "Correo electrónico",
            {
              type: "email",
              placeholder: "ana@empresa.com",
              autoComplete: "email",
              readOnly: Boolean(emailFijo),
              required: Boolean(emailFijo),
            },
            emailFijo ? "La invitación es para este correo." : "",
          )}
          {campo("password", "Contraseña", {
            type: "password",
            required: true,
            autoComplete: "new-password",
          })}
          {campo("password2", "Confirmar contraseña", {
            type: "password",
            required: true,
            autoComplete: "new-password",
          })}

          {general && (
            <p
              role="alert"
              tabIndex={-1}
              data-error-general
              className="text-sm text-red-700 focus:outline-none"
            >
              <span aria-hidden="true">⚠ </span>
              {texto(general)}
            </p>
          )}

          <button
            type="submit"
            disabled={loading}
            className="w-full py-2.5 rounded-lg bg-blue-600 text-white text-sm font-semibold hover:bg-blue-700 disabled:opacity-50 motion-safe:transition mt-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"
          >
            {loading ? "Creando cuenta…" : "Crear cuenta"}
          </button>
        </form>
      </div>
    </Marco>
  );
}
