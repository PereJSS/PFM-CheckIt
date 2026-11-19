import { useState, useRef, useEffect, useCallback } from "react";
import { calculateSHA256 } from "../utils/hash";
import { horaDeArchivo } from "../utils/hora";
import {
  getMarcasPendientes,
  getPendingEvidencesByInspection,
  guardarChecklistCache,
  guardarMarcaOffline,
  leerChecklistCache,
  quitarMarcaOffline,
  saveEvidenceOffline,
} from "../services/offline";
import {
  faltasLocales,
  marcarPunto,
  obtenerChecklist,
  superponerMarcas,
  textoFaltas,
} from "../services/checklists";
import ChecklistFlow from "./ChecklistFlow";
import api from "../services/api";
import {
  DESCRIPCION_MAX,
  formularioEvidencia,
} from "../services/formularioEvidencia";
import { esRechazoDefinitivo, motivoDelRechazo } from "../services/erroresSubida";

const ROOMS = [
  "Salón",
  "Cocina",
  "Baño Principal",
  "Dormitorio 1",
  "Dormitorio 2",
  "Terraza",
];

// ── Visor de cámara en vivo ────────────────────────────────────────────────
// Modal responsable de abrir cámara, mostrar preview y devolver una foto capturada.
function CameraModal({ onCapture, onClose }) {
  // Referencias a elementos de DOM para flujo de captura en tiempo real.
  const videoRef = useRef(null);
  const canvasRef = useRef(null);
  const streamRef = useRef(null);
  // ready controla cuándo puede dispararse la foto; error muestra fallback de UI.
  const [ready, setReady] = useState(false);
  // El soporte de getUserMedia se conoce al montar: se deriva en el estado inicial.
  const [error, setError] = useState(() =>
    navigator.mediaDevices?.getUserMedia
      ? null
      : "Tu navegador no soporta acceso a la cámara.",
  );

  useEffect(() => {
    // mounted evita actualizar estado si el modal se desmonta durante tareas async.
    let mounted = true;
    // Copia del nodo <video> para usarla de forma estable en el cleanup.
    const videoNode = videoRef.current;

    if (!navigator.mediaDevices?.getUserMedia) return;

    // Espera a que el video reciba un frame real para evitar capturas negras.
    const waitForFirstFrame = (videoEl, timeout = 3000) => {
      return new Promise((resolve, reject) => {
        const start = Date.now();

        const done = () => {
          if (
            videoEl.readyState >= 2 &&
            videoEl.videoWidth > 0 &&
            videoEl.videoHeight > 0
          ) {
            resolve(true);
            return true;
          }
          return false;
        };

        const tick = () => {
          if (done()) return;
          if (Date.now() - start >= timeout) {
            reject(new Error("timeout"));
            return;
          }
          requestAnimationFrame(tick);
        };

        if (typeof videoEl.requestVideoFrameCallback === "function") {
          const id = videoEl.requestVideoFrameCallback(() => {
            if (!done()) {
              tick();
            }
          });

          setTimeout(() => {
            try {
              videoEl.cancelVideoFrameCallback?.(id);
            } catch {
              // Ignorar fallback de cancelación no soportado.
            }
          }, timeout);
        }

        tick();
      });
    };

    // Cierra el stream activo y limpia el video antes de reintentar.
    const stopCurrentStream = () => {
      if (streamRef.current) {
        streamRef.current.getTracks().forEach((t) => t.stop());
        streamRef.current = null;
      }
      if (videoRef.current) {
        videoRef.current.srcObject = null;
      }
    };

    // Intenta varias configuraciones de cámara para maximizar compatibilidad.
    const initCamera = async () => {
      const videoEl = videoRef.current;
      if (!videoEl) return;

      // Safari/iOS necesita esta combinación para no abrir fullscreen ni dejar negro.
      videoEl.setAttribute("playsinline", "true");
      videoEl.setAttribute("webkit-playsinline", "true");

      const cameraAttempts = [
        { video: { facingMode: { exact: "environment" } }, audio: false },
        {
          video: {
            facingMode: { ideal: "environment" },
            width: { ideal: 1280 },
            height: { ideal: 720 },
          },
          audio: false,
        },
        { video: true, audio: false },
        { video: { facingMode: "user" }, audio: false },
      ];

      let lastErr = null;

      // Se prueban constraints de más específicos a más genéricos.
      for (const constraints of cameraAttempts) {
        try {
          const stream = await navigator.mediaDevices.getUserMedia(constraints);

          if (!mounted) {
            stream.getTracks().forEach((t) => t.stop());
            return;
          }

          stopCurrentStream();
          streamRef.current = stream;
          videoEl.srcObject = stream;

          await videoEl.play();
          await waitForFirstFrame(videoEl);

          if (!mounted) {
            stopCurrentStream();
            return;
          }

          setError(null);
          setReady(true);
          return;
        } catch (err) {
          // Guarda último error para reportar un mensaje útil al usuario.
          lastErr = err;
          stopCurrentStream();
        }
      }

      if (
        lastErr?.name === "NotAllowedError" ||
        lastErr?.name === "PermissionDeniedError"
      ) {
        setError(
          "Permiso denegado. Permite el acceso a la cámara en la barra de dirección del navegador.",
        );
      } else if (
        lastErr?.name === "NotFoundError" ||
        lastErr?.name === "DevicesNotFoundError"
      ) {
        setError("No se encontró ninguna cámara en este dispositivo.");
      } else {
        setError(
          "No se pudo iniciar la vista previa de la cámara. Prueba a cerrar otras apps que usen la cámara e inténtalo de nuevo.",
        );
      }
    };

    initCamera();

    // Limpieza al cerrar modal: corta cámara y suelta recursos del navegador.
    return () => {
      mounted = false;
      setReady(false);

      if (videoNode) {
        videoNode.srcObject = null;
      }

      if (streamRef.current) {
        streamRef.current.getTracks().forEach((t) => t.stop());
      }
    };
  }, []);

  // Captura el frame actual del video, lo convierte a Blob y lo devuelve como File.
  const handleShoot = useCallback(() => {
    const video = videoRef.current;
    const canvas = canvasRef.current;
    if (!video || !canvas) return;
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    canvas.getContext("2d").drawImage(video, 0, 0);
    canvas.toBlob(
      (blob) => {
        const file = new File([blob], `evidencia_${Date.now()}.jpg`, {
          type: "image/jpeg",
          lastModified: Date.now(),
        });
        onCapture(file);
      },
      "image/jpeg",
      0.92,
    );
  }, [onCapture]);

  // Fallback de carga manual cuando la cámara no puede abrirse.
  const handleFileChange = (e) => {
    const file = e.target.files[0];
    // Archivo elegido (galería, disco): su lastModified no es la hora de la foto
    if (file) onCapture(file, { desdeCamara: false });
  };

  return (
    <div className="fixed inset-0 z-50 bg-black/80 flex flex-col items-center justify-center p-4">
      <div className="w-full max-w-lg bg-black rounded-2xl overflow-hidden shadow-2xl">
        {/* Cabecera */}
        <div className="flex items-center justify-between px-4 py-3 bg-slate-900">
          <span className="text-sm font-semibold text-white">Cámara</span>
          <button
            onClick={onClose}
            className="text-slate-400 hover:text-white transition text-lg leading-none"
          >
            ✕
          </button>
        </div>

        {/* Visor */}
        <div className="relative bg-slate-950 aspect-video flex items-center justify-center">
          {error ? (
            <div className="flex flex-col items-center gap-4 px-8 text-center">
              <svg
                className="w-10 h-10 text-slate-500"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={1.5}
                  d="M18.364 18.364A9 9 0 005.636 5.636m12.728 12.728A9 9 0 015.636 5.636m12.728 12.728L5.636 5.636"
                />
              </svg>
              <p className="text-slate-300 text-sm">{error}</p>
            </div>
          ) : (
            <video
              ref={videoRef}
              autoPlay
              playsInline
              muted
              className="w-full h-full object-cover"
            />
          )}
          {ready && (
            <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
              <div className="w-3/4 h-3/4 border-2 border-white/30 rounded-lg" />
            </div>
          )}
        </div>
        <canvas ref={canvasRef} className="hidden" />

        {/* Acciones */}
        <div className="flex items-center justify-center gap-3 px-6 py-5 bg-slate-900">
          {error ? (
            <>
              <label className="px-5 py-2 rounded-lg bg-blue-600 hover:bg-blue-700 text-white text-sm font-semibold cursor-pointer transition">
                Seleccionar archivo
                <input
                  type="file"
                  accept="image/*"
                  className="hidden"
                  onChange={handleFileChange}
                />
              </label>
              <button
                onClick={onClose}
                className="px-5 py-2 rounded-lg bg-slate-700 hover:bg-slate-600 text-white text-sm font-semibold transition"
              >
                Cancelar
              </button>
            </>
          ) : (
            <button
              onClick={handleShoot}
              disabled={!ready}
              className="w-16 h-16 rounded-full bg-white border-4 border-slate-400 hover:border-blue-400 disabled:opacity-40 transition shadow-lg active:scale-95"
              title="Tomar foto"
            />
          )}
        </div>
      </div>
    </div>
  );
}

// Avisos del flujo: las regiones viven siempre montadas para que los lectores de
// pantalla anuncien cada cambio (un role=status recién insertado se anuncia mal)
function AvisoEstado({ status }) {
  const clase = (tipo) =>
    `mt-4 px-4 py-3 rounded-lg text-sm font-medium ${
      tipo === "success"
        ? "bg-emerald-50 text-emerald-800 border border-emerald-200"
        : tipo === "error"
          ? "bg-red-50 text-red-800 border border-red-200"
          : "bg-blue-50 text-blue-800 border border-blue-200"
    }`;
  return (
    <>
      <div role="status" aria-live="polite" aria-atomic="true">
        {status && status.type !== "error" && <p className={clase(status.type)}>{status.text}</p>}
      </div>
      <div role="alert" aria-atomic="true">
        {status?.type === "error" && <p className={clase("error")}>{status.text}</p>}
      </div>
    </>
  );
}

// ── Formulario principal ───────────────────────────────────────────────────
export default function InspectionForm({ inspectionId, recarga = 0 }) {
  // Estado del flujo en 2 pasos: elegir estancia y registrar evidencia.
  const [step, setStep] = useState(1);
  const [estanciaElegida, setRoom] = useState("");
  const [description, setDescription] = useState("");
  const [status, setStatus] = useState(null);
  const [cameraOpen, setCameraOpen] = useState(false);
  // Input oculto para abrir cámara nativa en móvil.
  const fileInputRef = useRef(null);
  // Checklist de la inspección (#19): null mientras carga; sin checklist, el flujo de siempre
  const [checklist, setChecklist] = useState(null);
  // Fotos de cada punto guardadas en el móvil sin subir: cuentan para las faltas
  const [fotosPendientes, setFotosPendientes] = useState({});
  // Punto al que irá la próxima foto (la cámara se abre fuera de este render)
  const puntoFoto = useRef(null);

  // ok | movil (copia guardada en el móvil) | error (no se pudo leer)
  const [cargaChecklist, setCargaChecklist] = useState("cargando");
  const [reintentos, setReintentos] = useState(0);
  // Por punto, la última marca hecha: una respuesta o un rechazo tardío de una anterior no la pisa
  const secuencia = useRef({});

  useEffect(() => {
    let vigente = true;
    const conMarcasPendientes = (datos) =>
      getMarcasPendientes(inspectionId)
        .catch(() => [])
        .then((marcas) => superponerMarcas(datos, marcas));
    obtenerChecklist(inspectionId)
      .then((datos) => {
        guardarChecklistCache(inspectionId, datos).catch((e) =>
          console.warn("No se pudo guardar el checklist en el móvil", e),
        );
        return conMarcasPendientes(datos).then((d) => {
          if (!vigente) return;
          setChecklist(d);
          setCargaChecklist("ok");
        });
      })
      .catch((error) => {
        // Sin red o con el servidor caído: la copia de este móvil. Otro error (403, 404…) no
        const sinServidor = !error?.response || error.response.status >= 500;
        const guardado = sinServidor ? leerChecklistCache(inspectionId).catch(() => null) : Promise.resolve(null);
        return guardado
          .then((datos) => (datos ? conMarcasPendientes(datos) : null))
          .then((d) => {
            if (!vigente) return;
            if (d) {
              setChecklist(d);
              setCargaChecklist("movil");
            } else {
              console.error("No se pudo cargar el checklist de la inspección", error);
              setChecklist({ tiene_checklist: false });
              setCargaChecklist("error");
            }
          });
      });
    getPendingEvidencesByInspection(inspectionId)
      .then((pendientes) => {
        const porPunto = {};
        for (const ev of pendientes) if (ev.punto) porPunto[ev.punto] = (porPunto[ev.punto] || 0) + 1;
        if (vigente) setFotosPendientes(porPunto);
      })
      .catch((e) => console.warn("No se pudieron leer las fotos guardadas en el móvil", e));
    return () => {
      vigente = false;
    };
  }, [inspectionId, recarga, reintentos]);

  const actualizarPunto = (puntoId, cambios) =>
    setChecklist((actual) =>
      actual && {
        ...actual,
        estancias: actual.estancias.map((e) => ({
          ...e,
          puntos: e.puntos.map((p) => (p.id === puntoId ? { ...p, ...cambios } : p)),
        })),
      },
    );

  // Marca optimista: se ve al momento; sin red queda en el móvil y se sube después
  const marcar = async (punto, cambios) => {
    const id = punto.id;
    const mia = (secuencia.current[id] || 0) + 1;
    secuencia.current[id] = mia;
    const esLaUltima = () => secuencia.current[id] === mia;
    // Solo se deshace lo que cambió esta marca (no la nota si se marcó el resultado)
    const anterior = Object.fromEntries(
      Object.keys(cambios).map((k) => [k, k === "nota" ? punto.nota || "" : (punto[k] ?? null)]),
    );
    const nuevo = { resultado: punto.resultado ?? null, nota: punto.nota || "", ...cambios };
    const ahora = new Date().toISOString();
    actualizarPunto(id, cambios);

    let respuesta;
    try {
      respuesta = await marcarPunto(inspectionId, id, { ...cambios, marcado_en_dispositivo: ahora });
    } catch (error) {
      if (esRechazoDefinitivo(error)) {
        if (esLaUltima()) actualizarPunto(id, anterior);
        setStatus({ type: "error", text: `No se ha guardado la marca: ${motivoDelRechazo(error, "la marca")}` });
        return;
      }
      try {
        await guardarMarcaOffline({ inspectionId, puntoId: id, ...nuevo, marcadoEnDispositivo: ahora });
        setStatus({
          type: "info",
          text: error?.response
            ? "No se pudo contactar con el servidor: la marca se ha guardado en el móvil y se subirá después."
            : "Sin conexión: la marca se ha guardado en el móvil y se subirá después.",
        });
      } catch (e) {
        console.error("No se pudo guardar la marca en el móvil", e);
        if (esLaUltima()) actualizarPunto(id, anterior);
        setStatus({ type: "error", text: "No se ha podido guardar la marca. Inténtalo de nuevo." });
      }
      return;
    }

    // Guardada en el servidor: una marca anterior del mismo punto pendiente en el móvil sobra
    quitarMarcaOffline(inspectionId, id, ahora).catch((e) =>
      console.warn("No se pudo quitar la marca pendiente del móvil", e),
    );
    if (respuesta?.punto && esLaUltima()) {
      const { resultado, nota, evidencias } = respuesta.punto;
      actualizarPunto(id, { resultado, nota, evidencias });
    }
    if (respuesta?.aplicado === false) {
      setStatus({ type: "info", text: "Este punto ya tenía una marca más reciente: se muestra la guardada." });
    }
  };

  // Detección heurística de móvil para elegir cámara nativa vs modal web.
  const isMobileDevice =
    typeof window !== "undefined" &&
    (navigator.userAgentData?.mobile ||
      /Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent) ||
      navigator.maxTouchPoints > 1);

  // Procesa archivo: calcula hash, intenta subir online y si falla guarda offline.
  const processFile = async (file, { desdeCamara = true } = {}) => {
    setCameraOpen(false);
    setStatus({ type: "loading", text: "Calculando huella SHA-256…" });
    // Foto de un punto del checklist: su estancia manda
    const punto = puntoFoto.current;
    puntoFoto.current = null;
    const room = punto ? punto.estancia : estanciaElegida;

    if (!room) {
      setStatus({
        type: "error",
        text: "Selecciona una estancia antes de adjuntar evidencia.",
      });
      return;
    }

    try {
      // Hash local para trazabilidad e integridad de la evidencia.
      const hash = await calculateSHA256(file);
      // Hora de la foto según el dispositivo (#29): lastModified la fija la cámara
      // al crear el archivo; el servidor la guarda como dato declarado, no certificado.
      const capturedAt = desdeCamara ? horaDeArchivo(file) : null;

      const formData = formularioEvidencia({
        file,
        room,
        description: punto ? "" : description,
        hash,
        capturedAt,
        punto: punto?.id,
      });

      try {
        const { data } = await api.post(`/inspecciones/${inspectionId}/evidencias/`, formData);
        if (punto && data?.punto === punto.id) {
          setChecklist((actual) =>
            actual && {
              ...actual,
              estancias: actual.estancias.map((e) => ({
                ...e,
                puntos: e.puntos.map((p) =>
                  p.id === punto.id ? { ...p, evidencias: [...(p.evidencias || []), data.id] } : p,
                ),
              })),
            },
          );
        }
        setStatus(
          data?.aviso
            ? { type: "info", text: `Foto subida, pero: ${data.aviso}` }
            : { type: "success", text: "Evidencia subida y sellada correctamente." },
        );
      } catch (error) {
        // Un rechazo del servidor (datos no válidos, inspección completada…) no se
        // arregla reintentando: se avisa y se deja el texto para corregirlo
        if (esRechazoDefinitivo(error)) {
          setStatus({
            type: "error",
            text: `No se ha subido la foto: ${motivoDelRechazo(error)}`,
          });
          return;
        }
        // Si falla la subida, conservar evidencia local evita pérdida de datos.
        await saveEvidenceOffline({
          inspectionId,
          room,
          description: punto ? "" : description,
          punto: punto?.id ?? null,
          hash,
          file,
          capturedAt,
          timestamp: new Date().toISOString(),
          synced: false,
        });
        if (punto) setFotosPendientes((actual) => ({ ...actual, [punto.id]: (actual[punto.id] || 0) + 1 }));
        setStatus({
          type: "info",
          text: "Sin conexión o error de red: evidencia guardada en el móvil. Aún no aparecerá en el PDF hasta sincronizar.",
        });
      }
      if (punto) return;

      setStep(1);
      setRoom("");
      setDescription("");
    } catch (e) {
      // Hash o guardado en el móvil fallidos: no se limpia el formulario
      console.error("No se pudo procesar la evidencia", e);
      setStatus({
        type: "error",
        text: "Error al procesar la evidencia. Inténtalo de nuevo.",
      });
    }
  };

  // En móvil abre la captura nativa; en desktop abre modal con preview en vivo.
  const fotoDePunto = (punto, estancia) => abrirCaptura({ id: punto.id, estancia });

  // Desde «Otras fotos»: sin punto
  const openCamera = () => abrirCaptura(null);

  // El destino de la foto viaja hasta processFile (la cámara se abre fuera de este render)
  const abrirCaptura = (destino) => {
    puntoFoto.current = destino;
    if (isMobileDevice && fileInputRef.current) {
      fileInputRef.current.click();
      return;
    }

    setCameraOpen(true);
  };

  // Recibe la imagen tomada por la cámara nativa y reinicia el input.
  const handleNativeCapture = (e) => {
    const file = e.target.files?.[0];
    if (file) {
      processFile(file);
    }

    e.target.value = "";
  };

  // Solo permite finalizar si no quedan evidencias offline pendientes de sincronizar.
  const handleComplete = async () => {
    if (cargaChecklist === "error") {
      setStatus({ type: "error", text: "No se ha podido cargar el checklist: pulsa «Reintentar» antes de finalizar." });
      return;
    }
    let pending;
    let marcas;
    try {
      [pending, marcas] = await Promise.all([
        getPendingEvidencesByInspection(inspectionId),
        getMarcasPendientes(inspectionId),
      ]);
    } catch (e) {
      console.error("No se pudo leer lo guardado en el móvil", e);
      setStatus({ type: "error", text: "No se ha podido comprobar lo guardado en el móvil. Inténtalo de nuevo." });
      return;
    }
    if (pending.length > 0 || marcas.length > 0) {
      setStatus({
        type: "error",
        text: "Tienes fotos o marcas del checklist pendientes de sincronizar en este móvil. No finalices la inspección hasta que se suban para que aparezcan en el PDF.",
      });
      return;
    }

    // El servidor decide (409), pero así el operario lo sabe sin esperar a la red
    const faltas = faltasLocales(checklist, fotosPendientes);
    if (faltas.length > 0) {
      setStatus({ type: "error", text: `Faltan puntos del checklist: ${textoFaltas(faltas)}.` });
      return;
    }

    setStatus({ type: "loading", text: "Finalizando inspección…" });
    try {
      await api.patch(`/inspecciones/${inspectionId}/`, {
        estado: "COMPLETADA",
      });
      window.location.replace("/operario");
    } catch (error) {
      const faltasServidor = error?.response?.status === 409 ? error.response.data?.faltas : null;
      setStatus({
        type: "error",
        text: Array.isArray(faltasServidor)
          ? `Faltan puntos del checklist: ${textoFaltas(faltasServidor)}.`
          : esRechazoDefinitivo(error)
            ? `No se ha podido finalizar: ${motivoDelRechazo(error, "la petición")}`
            : "Error al finalizar la inspección. Inténtalo de nuevo.",
      });
    }
  };

  return (
    <>
      {/* Input oculto usado para invocar la cámara nativa del dispositivo. */}
      <input
        ref={fileInputRef}
        type="file"
        accept="image/*"
        capture="environment"
        className="hidden"
        onChange={handleNativeCapture}
      />

      {/* Modal de cámara web (solo desktop/no-móvil). */}
      {cameraOpen && (
        <CameraModal
          onCapture={processFile}
          onClose={() => {
            puntoFoto.current = null;
            setCameraOpen(false);
          }}
        />
      )}

      {cargaChecklist === "error" && (
        <div role="alert" className="mb-6 px-4 py-3 rounded-lg bg-red-50 text-red-800 border border-red-200 text-sm">
          <p>
            No se ha podido cargar el checklist de esta inspección. Si tiene checklist, no podrás finalizarla hasta
            cargarlo.
          </p>
          <button
            type="button"
            onClick={() => setReintentos((n) => n + 1)}
            className="mt-2 min-h-11 px-4 rounded-lg border-2 border-red-700 font-semibold text-red-800 hover:bg-red-100"
          >
            Reintentar
          </button>
        </div>
      )}
      {checklist?.tiene_checklist && (
        <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-6 mb-6">
          {cargaChecklist === "movil" && (
            <p className="mb-3 text-xs text-slate-600">Sin conexión: es el checklist guardado en este móvil.</p>
          )}
          <ChecklistFlow
            checklist={checklist}
            fotosPendientes={fotosPendientes}
            onMarcar={marcar}
            onFoto={fotoDePunto}
          />
          {/* Los avisos van aquí, junto a lo que se acaba de marcar */}
          <AvisoEstado status={status} />
        </div>
      )}

      <section aria-labelledby={checklist?.tiene_checklist ? "otras-fotos-titulo" : undefined}>
      {checklist?.tiene_checklist && (
        <h3 id="otras-fotos-titulo" className="text-xs font-semibold text-slate-600 uppercase tracking-wide mb-2">
          Otras fotos
        </h3>
      )}

      {/* Tarjeta principal del flujo de inspección. */}
      <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
        {/* Barra de progreso */}
        <div className="flex border-b border-slate-100">
          {[1, 2].map((s) => (
            <div
              key={s}
              className={`flex-1 h-1 transition-all ${step >= s ? "bg-blue-600" : "bg-slate-100"}`}
            />
          ))}
        </div>

        <div className="p-6">
          {/* Paso 1: seleccionar estancia y/o finalizar inspección. */}
          {step === 1 && (
            <div className="flex flex-col gap-5">
              <div>
                <label className="block text-xs font-semibold text-slate-500 uppercase tracking-wide mb-2">
                  Estancia a inspeccionar
                </label>
                <select
                  value={estanciaElegida}
                  onChange={(e) => setRoom(e.target.value)}
                  className="w-full px-3 py-2.5 rounded-lg border border-slate-300 text-sm text-slate-900 bg-white focus:outline-none focus:ring-2 focus:ring-blue-500 transition"
                >
                  <option value="">Selecciona una estancia…</option>
                  {ROOMS.map((r) => (
                    <option key={r} value={r}>
                      {r}
                    </option>
                  ))}
                </select>
              </div>

              <button
                onClick={() => setStep(2)}
                disabled={!estanciaElegida}
                className="py-2.5 rounded-lg bg-blue-600 text-white text-sm font-semibold hover:bg-blue-700 disabled:opacity-40 disabled:cursor-not-allowed transition"
              >
                Registrar daño en {estanciaElegida || "esta sala"}
              </button>

              <div className="border-t border-slate-100 pt-4">
                <button
                  onClick={handleComplete}
                  className="w-full py-2.5 rounded-lg border border-emerald-300 text-emerald-700 text-sm font-semibold hover:bg-emerald-50 transition"
                >
                  Finalizar inspección
                </button>
              </div>
            </div>
          )}

          {/* Paso 2: describir daño y capturar/subir evidencia fotográfica. */}
          {step === 2 && (
            <div className="flex flex-col gap-5">
              <div>
                <label className="block text-xs font-semibold text-slate-500 uppercase tracking-wide mb-2">
                  Descripción del daño en{" "}
                  <span className="text-slate-800">{estanciaElegida}</span>
                </label>
                <textarea
                  value={description}
                  maxLength={DESCRIPCION_MAX}
                  aria-describedby="contador-descripcion"
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder="Ej: Arañazo profundo en la puerta del armario…"
                  className="w-full px-3 py-2.5 rounded-lg border border-slate-300 text-sm text-slate-900 bg-white resize-none h-24 focus:outline-none focus:ring-2 focus:ring-blue-500 transition"
                />
                <p
                  id="contador-descripcion"
                  className="mt-1 text-xs text-slate-400 text-right"
                >
                  {description.length}/{DESCRIPCION_MAX} caracteres
                </p>
              </div>

              {/* Botón cámara principal */}
              <button
                onClick={openCamera}
                className="flex flex-col items-center gap-3 p-6 rounded-xl border-2 border-dashed border-slate-300 hover:border-blue-400 hover:bg-blue-50 transition w-full"
              >
                <svg
                  className="w-8 h-8 text-slate-400"
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={1.5}
                    d="M3 9a2 2 0 012-2h.93a2 2 0 001.664-.89l.812-1.22A2 2 0 0110.07 4h3.86a2 2 0 011.664.89l.812 1.22A2 2 0 0018.07 7H19a2 2 0 012 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2V9z"
                  />
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={1.5}
                    d="M15 13a3 3 0 11-6 0 3 3 0 016 0z"
                  />
                </svg>
                <span className="text-sm font-semibold text-slate-700">
                  {isMobileDevice ? "Abrir cámara del móvil" : "Abrir cámara"}
                </span>
                {isMobileDevice && (
                  <span className="text-xs text-slate-500 text-center">
                    Se abrirá la app de cámara del dispositivo para que la foto
                    se tome con vista previa nativa.
                  </span>
                )}
                <span className="text-xs text-slate-400">
                  La foto se sellará con SHA-256 localmente
                </span>
              </button>

              <button
                onClick={() => setStep(1)}
                className="text-sm text-slate-400 hover:text-slate-600 transition"
              >
                ← Volver
              </button>
            </div>
          )}

          {/* Mensajes de estado del flujo (con checklist, van junto a él) */}
          {!checklist?.tiene_checklist && <AvisoEstado status={status} />}
        </div>
      </div>
      </section>
    </>
  );
}
