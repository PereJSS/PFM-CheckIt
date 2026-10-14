import DestinosWebhook from "./DestinosWebhook";
import EntregasWebhook from "./EntregasWebhook";
import useListado from "./useListado";
import { CARD, CODE, PRE } from "./estilos";

// Verificación de firma en el receptor, según el contrato de webhooks salientes:
// X-CheckIt-Signature: sha256=<hex> = HMAC-SHA256(cuerpo crudo, secreto "whsec_...").
const EJEMPLO_VERIFICACION = `import hashlib, hmac

def firma_valida(cuerpo_crudo: bytes, cabecera: str, secreto: str) -> bool:
    esperada = "sha256=" + hmac.new(
        secreto.encode("utf-8"), cuerpo_crudo, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(esperada, cabecera or "")`;

// Webhooks salientes: CheckIt avisa a sistemas externos (destinos) y guarda
// el historial de entregas. Endpoints: /integraciones/destinos/ y /integraciones/entregas/.
export default function WebhooksSalientesSection() {
  // Los destinos se cargan aquí porque también los usa el filtro de entregas.
  const {
    datos: destinos,
    cargando,
    error,
    noDisponible,
    recargar,
  } = useListado(
    "/integraciones/destinos/",
    "",
    "No se pudieron cargar los destinos.",
  );

  return (
    <section aria-labelledby="titulo-salientes" className="mb-12">
      <h2
        id="titulo-salientes"
        className="text-lg font-semibold text-slate-900"
      >
        Webhooks salientes
      </h2>
      <p className="mt-1 mb-4 text-sm text-slate-500">
        CheckIt avisa a tus sistemas cuando una inspección se completa o su PDF
        está listo.
      </p>

      {/* 404: el backend desplegado aún no expone estos endpoints. */}
      {noDisponible ? (
        <div className={`${CARD} px-5 py-10 text-center text-slate-400 text-sm`}>
          Los webhooks salientes aún no están disponibles en este servidor.
        </div>
      ) : (
        <>
          <DestinosWebhook
            destinos={destinos}
            cargando={cargando}
            error={error}
            recargar={recargar}
          />
          {/* key: al crear o borrar un destino se recarga el historial de entregas */}
          <EntregasWebhook
            key={destinos.map((d) => d.id).join(",")}
            destinos={destinos}
          />

          <details className={`${CARD} mt-8 p-6 text-sm text-slate-700`}>
            <summary className="cursor-pointer font-semibold text-slate-800">
              Cómo verificar los webhooks que envía CheckIt
            </summary>
            <ul className="mt-4 list-disc pl-5 space-y-2 text-slate-600">
              <li>
                <span className={CODE}>POST</span> con{" "}
                <span className={CODE}>Content-Type: application/json</span>.
                Cuerpo:{" "}
                <span className={CODE}>
                  {
                    '{"event_id", "tipo", "fecha", "data": {inspeccion_id, propiedad_id, external_id, estado, pdf_estado}}'
                  }
                </span>
                .
              </li>
              <li>
                <span className={CODE}>
                  X-CheckIt-Signature: sha256=&lt;hex&gt;
                </span>
                : HMAC-SHA256 del cuerpo crudo (los bytes exactos recibidos)
                con el secreto completo <span className={CODE}>whsec_…</span>{" "}
                como clave. Compárala en tiempo constante. Si rotas el secreto,
                el anterior deja de valer al momento.
              </li>
              <li>
                <span className={CODE}>X-CheckIt-Event</span>: tipo de evento.{" "}
                <span className={CODE}>X-CheckIt-Delivery</span>: id de la
                entrega, estable entre reintentos. Usa{" "}
                <span className={CODE}>event_id</span> para descartar
                repeticiones.
              </li>
              <li>
                Responde 2xx. Ante 5xx o timeout (5 s) se reintenta con backoff
                exponencial hasta 5 veces; ante 4xx no se reintenta y no se
                siguen redirecciones.
              </li>
            </ul>
            <pre className={PRE}>
              <code>{EJEMPLO_VERIFICACION}</code>
            </pre>
          </details>
        </>
      )}
    </section>
  );
}
