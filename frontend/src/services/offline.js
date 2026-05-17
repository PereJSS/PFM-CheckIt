// Función para guardar la foto localmente cuando no hay internet
// La idea es que esta función se llame cuando el operario intente subir una foto sin conexión,
//  para luego sincronizarla con el backend cuando vuelva a tener internet.
//  La función recibe un objeto evidenceData que contiene la información de la evidencia a guardar.

import { openDB } from "idb";

// Inicializa la base de datos local en el móvil del operario
export async function initDB() {
  return openDB("ckeckii_db", 1, {
    upgrade(db) {
      if (!db.objectStoreNames.contains("evidences")) {
        // Creamos una "tabla" local para las evidencias pendientes
        db.createObjectStore("evidences", {
          keyPath: "id",
          autoIncrement: true,
        });
      }
    },
  });
}

export async function saveEvidenceOffline(evidenceData) {
  const db = await initDB();
  await db.add("evidences", evidenceData);
}
