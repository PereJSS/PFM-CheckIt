import ApiKeysSection from "./ApiKeysSection";
import EventosSection from "./EventosSection";
import GuiaIntegrador from "./GuiaIntegrador";
import WebhooksSalientesSection from "./WebhooksSalientesSection";

// Interruptor de la sección de webhooks salientes. Sus endpoints
// (/integraciones/destinos/ y /integraciones/entregas/) los añade la rama de
// webhooks salientes; si el backend aún no los tiene, la sección muestra
// "no disponible" (404). Pon false para ocultarla del todo.
const WEBHOOKS_SALIENTES_ACTIVOS = true;

// Pestaña "Integraciones" del panel de administración.
export default function IntegracionesPanel() {
  return (
    <>
      <div className="mb-8">
        <h1 className="text-2xl font-semibold text-slate-900">Integraciones</h1>
        <p className="mt-1 text-sm text-slate-500">
          Conecta tu PMS con CheckIt y revisa los eventos intercambiados
        </p>
      </div>

      <ApiKeysSection />
      <EventosSection />
      <GuiaIntegrador />
      {WEBHOOKS_SALIENTES_ACTIVOS && <WebhooksSalientesSection />}
    </>
  );
}
