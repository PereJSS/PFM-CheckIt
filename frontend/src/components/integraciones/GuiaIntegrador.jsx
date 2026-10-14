import { apiBaseUrl } from "../../services/api";
import { CARD } from "./estilos";

// URL absoluta del webhook de entrada (integraciones/urls.py: webhooks/pms/).
// apiBaseUrl puede ser relativa ("/api/v1") o absoluta (VITE_API_URL).
const webhookUrl = () =>
  new URL(
    `${apiBaseUrl.replace(/\/$/, "")}/webhooks/pms/`,
    window.location.origin,
  ).href;

// Ejemplo fiel a PmsWebhookSerializer (integraciones/serializers.py).
const PAYLOAD_EJEMPLO = {
  event_id: "evt_123",
  event_type: "reservation.checkout",
  provider: "guesty",
  data: { property_id: "LISTING-42" },
};

const PRE =
  "mt-2 overflow-x-auto rounded-lg bg-slate-900 p-4 font-mono text-xs leading-relaxed text-slate-100";
const CODE = "font-mono text-xs bg-slate-100 text-slate-700 px-1.5 py-0.5 rounded";

export default function GuiaIntegrador() {
  const url = webhookUrl();
  const curl = `curl -X POST "${url}" \\
  -H "Authorization: Api-Key <tu_api_key>" \\
  -H "Content-Type: application/json" \\
  -d '${JSON.stringify(PAYLOAD_EJEMPLO)}'`;

  return (
    <section aria-labelledby="titulo-guia" className="mb-12">
      <h2 id="titulo-guia" className="text-lg font-semibold text-slate-900">
        Guía para el integrador
      </h2>
      <p className="mt-1 mb-4 text-sm text-slate-500">
        Cómo debe llamar tu PMS a CheckIt para crear inspecciones al terminar
        una reserva.
      </p>

      <div className={`${CARD} p-6 space-y-5 text-sm text-slate-700`}>
        <div>
          <h3 className="font-semibold text-slate-800">1. Endpoint</h3>
          <p className="mt-1">
            <span className={CODE}>POST</span>{" "}
            <span className={`${CODE} break-all`}>{url}</span>
          </p>
        </div>

        <div>
          <h3 className="font-semibold text-slate-800">2. Autenticación</h3>
          <p className="mt-1">
            Envía la API key en la cabecera{" "}
            <span className={CODE}>Authorization: Api-Key &lt;clave&gt;</span>{" "}
            y el cuerpo como{" "}
            <span className={CODE}>Content-Type: application/json</span>. Sin
            clave, o con una clave revocada, la respuesta es{" "}
            <span className={CODE}>401</span>.
          </p>
        </div>

        <div>
          <h3 className="font-semibold text-slate-800">3. Cuerpo del evento</h3>
          <pre className={PRE}>
            <code>{JSON.stringify(PAYLOAD_EJEMPLO, null, 2)}</code>
          </pre>
          <ul className="mt-3 list-disc pl-5 space-y-1 text-slate-600">
            <li>
              <span className={CODE}>event_id</span> (obligatorio, máx. 100):
              identificador único del evento en tu PMS. Si reenvías el mismo,
              no se duplica la inspección.
            </li>
            <li>
              <span className={CODE}>event_type</span> (obligatorio):{" "}
              <span className={CODE}>reservation.checkout</span> crea una
              inspección pendiente. Otros tipos se registran como «Ignorado».
            </li>
            <li>
              <span className={CODE}>provider</span> (opcional, por defecto{" "}
              <span className={CODE}>generico</span>): nombre de tu PMS.
            </li>
            <li>
              <span className={CODE}>data.property_id</span> (obligatorio en{" "}
              <span className={CODE}>reservation.checkout</span>): debe
              coincidir con el <strong>ID externo</strong> de una de tus
              propiedades (pestaña Propiedades).
            </li>
          </ul>
        </div>

        <div>
          <h3 className="font-semibold text-slate-800">4. Ejemplo con curl</h3>
          <pre className={PRE}>
            <code>{curl}</code>
          </pre>
          <p className="mt-2 text-slate-600">
            Respuesta <span className={CODE}>202</span> si el evento se acepta
            y <span className={CODE}>200</span> con{" "}
            <span className={CODE}>"duplicado": true</span> si ya estaba
            procesado. Si queda en «Error» (por ejemplo, ID externo
            inexistente), corrige la propiedad y reenvía el mismo{" "}
            <span className={CODE}>event_id</span>: se reprocesa.
          </p>
        </div>
      </div>
    </section>
  );
}
