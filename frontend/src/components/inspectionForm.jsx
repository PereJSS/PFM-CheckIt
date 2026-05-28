import { useState, useRef, useEffect, useCallback } from "react";
import { calculateSHA256 } from "../utils/hash";
import { saveEvidenceOffline } from "../services/offline";
import api from "../services/api";

const ROOMS = [
  "Salón",
  "Cocina",
  "Baño Principal",
  "Dormitorio 1",
  "Dormitorio 2",
  "Terraza",
];

// ── Visor de cámara en vivo ────────────────────────────────────────────────
function CameraModal({ onCapture, onClose }) {
  const videoRef = useRef(null);
  const canvasRef = useRef(null);
  const streamRef = useRef(null);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    let mounted = true;

    if (!navigator.mediaDevices?.getUserMedia) {
      setError("Tu navegador no soporta acceso a la cámara.");
      return;
    }

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

    const stopCurrentStream = () => {
      if (streamRef.current) {
        streamRef.current.getTracks().forEach((t) => t.stop());
        streamRef.current = null;
      }
      if (videoRef.current) {
        videoRef.current.srcObject = null;
      }
    };

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

    return () => {
      mounted = false;
      setReady(false);

      if (videoRef.current) {
        videoRef.current.srcObject = null;
      }

      if (streamRef.current) {
        streamRef.current.getTracks().forEach((t) => t.stop());
      }
    };
  }, []);

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
        });
        onCapture(file);
      },
      "image/jpeg",
      0.92,
    );
  }, [onCapture]);

  const handleFileChange = (e) => {
    const file = e.target.files[0];
    if (file) onCapture(file);
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

// ── Formulario principal ───────────────────────────────────────────────────
export default function InspectionForm({ inspectionId }) {
  const [step, setStep] = useState(1);
  const [room, setRoom] = useState("");
  const [description, setDescription] = useState("");
  const [status, setStatus] = useState(null);
  const [cameraOpen, setCameraOpen] = useState(false);

  const processFile = async (file) => {
    setCameraOpen(false);
    setStatus({ type: "loading", text: "Calculando huella SHA-256…" });
    try {
      const hash = await calculateSHA256(file);
      await saveEvidenceOffline({
        inspectionId,
        room,
        description,
        hash,
        file,
        timestamp: new Date().toISOString(),
        synced: false,
      });
      setStatus({
        type: "success",
        text: "Evidencia sellada y guardada correctamente.",
      });
      setStep(1);
      setRoom("");
      setDescription("");
    } catch {
      setStatus({
        type: "error",
        text: "Error al procesar la evidencia. Inténtalo de nuevo.",
      });
    }
  };

  const handleComplete = async () => {
    setStatus({ type: "loading", text: "Finalizando inspección…" });
    try {
      await api.patch(`/inspecciones/${inspectionId}/`, {
        estado: "COMPLETADA",
      });
      window.location.replace("/operario");
    } catch {
      setStatus({
        type: "error",
        text: "Error al finalizar la inspección. Inténtalo de nuevo.",
      });
    }
  };

  return (
    <>
      {cameraOpen && (
        <CameraModal
          onCapture={processFile}
          onClose={() => setCameraOpen(false)}
        />
      )}

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
          {step === 1 && (
            <div className="flex flex-col gap-5">
              <div>
                <label className="block text-xs font-semibold text-slate-500 uppercase tracking-wide mb-2">
                  Estancia a inspeccionar
                </label>
                <select
                  value={room}
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
                disabled={!room}
                className="py-2.5 rounded-lg bg-blue-600 text-white text-sm font-semibold hover:bg-blue-700 disabled:opacity-40 disabled:cursor-not-allowed transition"
              >
                Registrar daño en {room || "esta sala"}
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

          {step === 2 && (
            <div className="flex flex-col gap-5">
              <div>
                <label className="block text-xs font-semibold text-slate-500 uppercase tracking-wide mb-2">
                  Descripción del daño en{" "}
                  <span className="text-slate-800">{room}</span>
                </label>
                <textarea
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder="Ej: Arañazo profundo en la puerta del armario…"
                  className="w-full px-3 py-2.5 rounded-lg border border-slate-300 text-sm text-slate-900 bg-white resize-none h-24 focus:outline-none focus:ring-2 focus:ring-blue-500 transition"
                />
              </div>

              {/* Botón cámara principal */}
              <button
                onClick={() => setCameraOpen(true)}
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
                <span className="text-sm font-semibold text-slate-600">
                  Abrir cámara
                </span>
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

          {status && (
            <div
              className={`mt-5 px-4 py-3 rounded-lg text-sm font-medium ${
                status.type === "success"
                  ? "bg-emerald-50 text-emerald-700 border border-emerald-200"
                  : status.type === "error"
                    ? "bg-red-50 text-red-700 border border-red-200"
                    : "bg-blue-50 text-blue-700 border border-blue-200"
              }`}
            >
              {status.text}
            </div>
          )}
        </div>
      </div>
    </>
  );
}
