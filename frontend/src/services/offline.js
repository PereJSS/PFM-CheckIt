// Función para guardar la foto localmente cuando no hay internet
// La idea es que esta función se llame cuando el operario intente subir una foto sin conexión,
//  para luego sincronizarla con el backend cuando vuelva a tener internet.
//  La función recibe un objeto evidenceData que contiene la información de la evidencia a guardar.

import { openDB } from "idb";

// Inicializa la base de datos local en el móvil del operario
export async function initDB() {
  return openDB("checkit_db", 1, {
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

export async function getAllPendingEvidences() {
  const db = await initDB();
  const all = await db.getAll("evidences");
  return all.filter((item) => item.synced !== true);
}

export async function removeOfflineEvidence(id) {
  const db = await initDB();
  await db.delete("evidences", id);
}

export async function getPendingEvidencesByInspection(inspectionId) {
  const all = await getAllPendingEvidences();
  return all.filter(
    (item) => Number(item.inspectionId) === Number(inspectionId),
  );
}

export async function syncPendingEvidences(uploadFn) {
  const pending = await getAllPendingEvidences();
  let syncedCount = 0;
  let failedCount = 0;

  for (const evidence of pending) {
    try {
      await uploadFn(evidence);
      await removeOfflineEvidence(evidence.id);
      syncedCount += 1;
    } catch {
      failedCount += 1;
    }
  }

  return {
    total: pending.length,
    syncedCount,
    failedCount,
  };
}
