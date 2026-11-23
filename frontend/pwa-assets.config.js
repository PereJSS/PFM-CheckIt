// Genera los iconos de la PWA a partir del favicon: `npm run generate-pwa-assets`.
// Icono provisional hasta la estética (#26); los PNG generados se versionan en public/.
import { defineConfig, minimal2023Preset } from "@vite-pwa/assets-generator/config";

// Android recorta el icono maskable y iOS no admite transparencia: fondo azul a sangre
const fondo = { resizeOptions: { background: "#2563eb" } };

export default defineConfig({
  headLinkOptions: { preset: "2023" },
  preset: {
    ...minimal2023Preset,
    maskable: { ...minimal2023Preset.maskable, ...fondo },
    apple: { ...minimal2023Preset.apple, ...fondo },
  },
  images: ["public/favicon.svg"],
});
