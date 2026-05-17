// Este componente representa la página de inicio de sesión, donde los usuarios pueden ingresar sus credenciales para acceder a su cuenta.
//  Al enviar el formulario, se realiza una solicitud al backend para autenticar al usuario y obtener los tokens de acceso y actualización,
//  que se almacenan en el localStorage para su uso posterior.

import { useState } from "react";
import api from "../services/api";

export default function Login() {
  const [credentials, setCredentials] = useState({
    username: "",
    password: "",
  });
  const [error, setError] = useState(null);

  const handleChange = (e) => {
    setCredentials({ ...credentials, [e.target.name]: e.target.value });
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    try {
      const response = await api.post("/auth/login/", credentials);

      localStorage.setItem("access_token", response.data.access);
      localStorage.setItem("refresh_token", response.data.refresh);

      alert("¡Login exitoso! Tokens guardados.");
      window.location.href = "/";
    } catch (err) {
      setError("Credenciales incorrectas. Inténtalo de nuevo.");
    }
  };

  return (
    <div
      style={{
        maxWidth: "420px",
        margin: "100px auto",
        fontFamily: "sans-serif",
      }}
    >
      <h2>Iniciar Sesión en Ckeckii</h2>
      {error && <p style={{ color: "red" }}>{error}</p>}

      <form
        onSubmit={handleSubmit}
        style={{ display: "flex", flexDirection: "column", gap: "15px" }}
      >
        <input
          type="text"
          name="username"
          placeholder="Nombre de usuario"
          onChange={handleChange}
          required
          style={{ padding: "10px" }}
        />
        <input
          type="password"
          name="password"
          placeholder="Contraseña"
          onChange={handleChange}
          required
          style={{ padding: "10px" }}
        />
        <button
          type="submit"
          style={{
            padding: "10px",
            background: "#0056b3",
            color: "white",
            border: "none",
            borderRadius: "4px",
          }}
        >
          Entrar
        </button>
      </form>
    </div>
  );
}
