import { useEffect, useState } from "react";
import api from "../services/api";

// Este componente representa la página de administración del usuario, donde se muestran las inspecciones activas del usuario y se ofrece la opción de cerrar sesión.
export default function AdminPage() {
  const [inspecciones, setInspecciones] = useState([]);
  const [error, setError] = useState(null);

  // cargamos las inspecciones del usuario.
  useEffect(() => {
    const fetchInspecciones = async () => {
      try {
        const response = await api.get("/inspections/");
        setInspecciones(response.data);
      } catch (err) {
        setError("Error al cargar datos. Tu sesión puede haber caducado.");
      }
    };
    fetchInspecciones();
  }, []);

  const handleLogout = () => {
    localStorage.removeItem("access_token");
    localStorage.removeItem("refresh_token");
    window.location.href = "/login";
  };

  return (
    <div
      style={{
        maxWidth: "800px",
        margin: "40px auto",
        fontFamily: "sans-serif",
      }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <h2>Panel de Administración - Ckeckii</h2>
        <button
          onClick={handleLogout}
          style={{
            padding: "8px 12px",
            background: "#dc3545",
            color: "white",
            border: "none",
            borderRadius: "4px",
          }}
        >
          Cerrar Sesión
        </button>
      </div>

      <hr />

      <h3>Tus Inspecciones Activas</h3>
      {error && <p style={{ color: "red" }}>{error}</p>}

      {inspecciones.length === 0 ? (
        <p>No hay inspecciones registradas todavía.</p>
      ) : (
        <ul style={{ listStyleType: "none", padding: 0 }}>
          {inspecciones.map((insp) => (
            <li
              key={insp.id}
              style={{
                background: "#f8f9fa",
                margin: "10px 0",
                padding: "15px",
                border: "1px solid #ddd",
                borderRadius: "4px",
              }}
            >
              <strong>Inspección #{insp.id}</strong> - Estado: {insp.estado}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
