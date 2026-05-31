// Capa de persistencia offline para evidencias usando IndexedDB.
// Se usa como respaldo cuando falla la subida al backend y para reintentar más tarde.

import { openDB } from "idb";

// Inicializa/abre la base local y crea el almacén en la primera ejecución.
export async function initDB() {
  return openDB("checkit_db", 1, {
    upgrade(db) {
      if (!db.objectStoreNames.contains("evidences")) {
        // Store principal de evidencias pendientes (id autoincremental).
        db.createObjectStore("evidences", {
          keyPath: "id",
          autoIncrement: true,
        });
      }
    },
  });
}

// Guarda una evidencia localmente para no perderla cuando no hay conexión.
// evidenceData suele incluir: inspectionId, room, description, hash, file, timestamp.
export async function saveEvidenceOffline(evidenceData) {
  const db = await initDB();
  await db.add("evidences", evidenceData);
}

// Devuelve solo las evidencias todavía no sincronizadas.
export async function getAllPendingEvidences() {
  const db = await initDB();
  const all = await db.getAll("evidences");
  // `synced` se conserva por compatibilidad; por defecto los pendientes no lo tienen en true.
  return all.filter((item) => item.synced !== true);
}

// Elimina una evidencia local cuando ya se subió correctamente al backend.
export async function removeOfflineEvidence(id) {
  const db = await initDB();
  await db.delete("evidences", id);
}

// Filtra pendientes de una inspección concreta (útil antes de permitir completarla).
export async function getPendingEvidencesByInspection(inspectionId) {
  const all = await getAllPendingEvidences();
  return all.filter(
    (item) => Number(item.inspectionId) === Number(inspectionId),
  );
}

// Sincroniza en lote: intenta subir cada evidencia y borra localmente las que suben bien.
// uploadFn es inyectado desde la UI para reutilizar esta lógica con distintos endpoints.
export async function syncPendingEvidences(uploadFn) {
  const pending = await getAllPendingEvidences();
  let syncedCount = 0;
  let failedCount = 0;

  for (const evidence of pending) {
    try {
      await uploadFn(evidence);
      // Solo se borra tras confirmar subida para evitar pérdida de datos.
      await removeOfflineEvidence(evidence.id);
      syncedCount += 1;
    } catch {
      // Se mantiene en IndexedDB para reintentar en la siguiente sincronización.
      failedCount += 1;
    }
  }

  // Resumen para informar al usuario del resultado del proceso de sync.
  return {
    total: pending.length,
    syncedCount,
    failedCount,
  };
}
