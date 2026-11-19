// Capa de persistencia offline para evidencias usando IndexedDB.
// Se usa como respaldo cuando falla la subida al backend y para reintentar más tarde.

import { openDB } from "idb";
import { esRechazoDefinitivo, motivoDelRechazo } from "./erroresSubida";

// Inicializa/abre la base local y crea el almacén en la primera ejecución.
// v2 (#19): marcas del checklist sin conexión y caché del último checklist leído.
// Una sola conexión por pestaña: abrir una por llamada dejaba conexiones colgadas que
// bloquean la subida de versión de otra pestaña.
let conexion = null;

export function initDB() {
  if (!conexion) {
    conexion = openDB("checkit_db", 2, {
      upgrade(db, oldVersion) {
        if (oldVersion < 1 && !db.objectStoreNames.contains("evidences")) {
          // Store principal de evidencias pendientes (id autoincremental).
          db.createObjectStore("evidences", {
            keyPath: "id",
            autoIncrement: true,
          });
        }
        if (oldVersion < 2) {
          // Una marca por punto: la más reciente del móvil gana
          db.createObjectStore("marcas", { keyPath: ["inspectionId", "puntoId"] });
          db.createObjectStore("checklists", { keyPath: "inspectionId" });
        }
      },
      // Otra pestaña con la versión anterior abierta bloquea la subida de versión
      blocked() {
        console.warn("CheckIt está abierto en otra pestaña con una versión anterior.");
        window.dispatchEvent(new CustomEvent(EVENTO_BD_BLOQUEADA));
      },
      // Esta pestaña bloquea a otra más nueva: se cierra para dejarla actualizar
      blocking(_actual, _nueva, evento) {
        evento?.target?.close();
        conexion = null;
      },
      terminated() {
        conexion = null;
      },
    }).catch((e) => {
      conexion = null;
      throw e;
    });
  }
  return conexion;
}

export const EVENTO_BD_BLOQUEADA = "checkit-bd-bloqueada";

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

// ── Marcas del checklist sin conexión (#19) ─────────────────────────────────
// Solo si es más reciente que la guardada: dos marcas del mismo punto que fallan a la
// vez pueden terminar en cualquier orden
export async function guardarMarcaOffline(marca) {
  const db = await initDB();
  const fila = { ...marca, inspectionId: Number(marca.inspectionId), puntoId: Number(marca.puntoId) };
  const tx = db.transaction("marcas", "readwrite");
  const actual = await tx.store.get([fila.inspectionId, fila.puntoId]);
  if (!actual || actual.marcadoEnDispositivo <= fila.marcadoEnDispositivo) {
    await tx.store.put(fila);
  }
  await tx.done;
}

// Una marca subida en línea deja obsoleta la pendiente del mismo punto (si es anterior)
export async function quitarMarcaOffline(inspectionId, puntoId, hasta) {
  const db = await initDB();
  const clave = [Number(inspectionId), Number(puntoId)];
  const tx = db.transaction("marcas", "readwrite");
  const actual = await tx.store.get(clave);
  if (actual && actual.marcadoEnDispositivo <= hasta) await tx.store.delete(clave);
  await tx.done;
}

export async function getMarcasPendientes(inspectionId) {
  const db = await initDB();
  const todas = await db.getAll("marcas");
  if (inspectionId == null) return todas;
  return todas.filter((m) => m.inspectionId === Number(inspectionId));
}

let marcasEnCurso = null;

// Sube las marcas pendientes. Un rechazo definitivo (p. ej. la inspección ya está
// completada) la quita: es un clic que el operario puede repetir, y se informa.
export function syncPendingMarcas(patchFn) {
  if (!marcasEnCurso) {
    marcasEnCurso = sincronizarMarcas(patchFn).finally(() => {
      marcasEnCurso = null;
    });
  }
  return marcasEnCurso;
}

async function sincronizarMarcas(patchFn) {
  const db = await initDB();
  const pendientes = await db.getAll("marcas");
  let syncedCount = 0;
  let failedCount = 0;
  const rejected = [];
  for (const marca of pendientes) {
    const clave = [marca.inspectionId, marca.puntoId];
    try {
      await patchFn(marca);
      syncedCount += 1;
    } catch (error) {
      if (!esRechazoDefinitivo(error)) {
        failedCount += 1;
        continue;
      }
      rejected.push({ inspectionId: marca.inspectionId, puntoId: marca.puntoId, motivo: motivoDelRechazo(error) });
    }
    try {
      // Solo si no hay otra más nueva del mismo punto, hecha mientras se subía esta
      const actual = await db.get("marcas", clave);
      if (actual && actual.marcadoEnDispositivo === marca.marcadoEnDispositivo) {
        await db.delete("marcas", clave);
      }
    } catch (e) {
      console.error("No se pudo quitar la marca ya sincronizada", e);
    }
  }
  return { total: pendientes.length, syncedCount, failedCount, rejected };
}

// ── Caché del checklist (para cortes de red durante la inspección) ──────────
export async function guardarChecklistCache(inspectionId, checklist) {
  const db = await initDB();
  await db.put("checklists", { inspectionId: Number(inspectionId), checklist });
}

export async function leerChecklistCache(inspectionId) {
  const db = await initDB();
  const fila = await db.get("checklists", Number(inspectionId));
  return fila ? fila.checklist : null;
}

