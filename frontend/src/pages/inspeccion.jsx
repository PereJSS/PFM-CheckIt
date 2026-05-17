// vista de inspección para el operario, con captura de evidencia y hash criptográfico

import { useState } from "react";
import { calculateSHA256 } from "../utils/hash";
import { saveEvidenceOffline } from "../services/offlineSync";

export default function InspectionPage() {
  const [status, setStatus] = useState("");
  const [evidenceHash, setEvidenceHash] = useState(null);

  const handleCapture = async (e) => {
    const file = e.target.files;
    if (!file) return;

    try {
      // 1. Calculamos el Hash SHA-256 en el cliente
      setStatus("Sellando evidencia (SHA-256)...");
      const hash = await calculateSHA256(file);
      setEvidenceHash(hash);

      // 2. Simulamos el guardado Offline-First en IndexedDB
      setStatus("Guardando en base de datos local (Offline)...");
      const evidence = {
        file: file,
        hash: hash,
        timestamp: new Date().toISOString(),
        synced: false,
      };

      await saveEvidenceOffline(evidence);
      setStatus("¡Evidencia capturada y guardada de forma segura!");
    } catch (error) {
      setStatus("Error al procesar la evidencia.");
    }
  };

  return (
    <div
      style={{
        maxWidth: "420px",
        margin: "20px auto",
        padding: "20px",
        fontFamily: "sans-serif",
      }}
    >
      <h2>Panel de Operario</h2>
      <p>
        Captura el estado del activo. La cámara registrará el hash criptográfico
        para garantizar su validez legal.
      </p>

      <div
        style={{
          margin: "30px 0",
          padding: "20px",
          border: "2px dashed #ccc",
          textAlign: "center",
        }}
      >
        {/* El atributo capture="environment" abre la cámara trasera del móvil automáticamente */}
        <input
          type="file"
          accept="image/*"
          capture="environment"
          onChange={handleCapture}
        />
      </div>

      {status && (
        <p style={{ color: "#0056b3", fontWeight: "bold" }}>{status}</p>
      )}
      {evidenceHash && (
        <p style={{ fontSize: "12px", color: "gray", wordBreak: "break-all" }}>
          Hash: {evidenceHash}
        </p>
      )}
    </div>
  );
}
