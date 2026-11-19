// Capa de persistencia offline para evidencias usando IndexedDB.
// Se usa como respaldo cuando falla la subida al backend y para reintentar más tarde.

import { openDB } from "idb";
import { esRechazoDefinitivo, motivoDelRechazo } from "./erroresSubida";

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
  // Las rechazadas por el servidor no se reintentan ni bloquean finalizar la inspección.
  return all.filter((item) => item.synced !== true && item.rechazada !== true);
}

// Fotos que el servidor rechazó (p. ej. 400 o 409): siguen en el móvil para que el
// operario vea el motivo y decida descartarlas; reintentarlas no las arreglaría.
export async function getRejectedEvidences() {
  const db = await initDB();
  const all = await db.getAll("evidences");
  return all.filter((item) => item.rechazada === true);
}

// Devuelve false si el operario la descartó mientras se subía: no se resucita
async function marcarRechazada(id, motivo) {
  const db = await initDB();
  const actual = await db.get("evidences", id);
  if (!actual) return false;
  await db.put("evidences", { ...actual, rechazada: true, motivo });
  return true;
}

// El operario pide volver a intentarlo (p. ej. tras un 403 que ya se arregló)
export async function reintentarEvidencia(id) {
  const db = await initDB();
  const actual = await db.get("evidences", id);
  if (!actual) return;
  // eslint-disable-next-line no-unused-vars
  const { rechazada, motivo, ...pendiente } = actual;
  await db.put("evidences", pendiente);
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
// La sincronización en curso: al montar la página y con cada evento «online» (y dos
// veces en StrictMode) se pedía a la vez y la misma foto se subía dos veces
let sincronizacionEnCurso = null;

export function syncPendingEvidences(uploadFn) {
  if (!sincronizacionEnCurso) {
    sincronizacionEnCurso = sincronizar(uploadFn).finally(() => {
      sincronizacionEnCurso = null;
    });
  }
  return sincronizacionEnCurso;
}

async function sincronizar(uploadFn) {
  const pending = await getAllPendingEvidences();
  let syncedCount = 0;
  let failedCount = 0;
  const rejected = [];

  for (const evidence of pending) {
    try {
      await uploadFn(evidence);
      // Solo se borra tras confirmar subida para evitar pérdida de datos.
      await removeOfflineEvidence(evidence.id);
      syncedCount += 1;
    } catch (error) {
      if (!esRechazoDefinitivo(error)) {
        // Se mantiene en IndexedDB para reintentar en la siguiente sincronización.
        failedCount += 1;
        continue;
      }
      // Se aparta (sin borrar la foto) para no reintentarla en cada sincronización
      const motivo = motivoDelRechazo(error);
      try {
        if (await marcarRechazada(evidence.id, motivo)) {
          rejected.push({
            id: evidence.id,
            inspectionId: evidence.inspectionId,
            room: evidence.room,
            motivo,
          });
        }
      } catch (errorAlApartar) {
        // Sin poder apartarla (cuota llena…) sigue pendiente; no corta el resto
        console.error("No se pudo apartar la foto rechazada", errorAlApartar);
        failedCount += 1;
      }
    }
  }

  // Resumen para informar al usuario del resultado del proceso de sync.
  return {
    total: pending.length,
    syncedCount,
    failedCount,
    rejected,
  };
}
