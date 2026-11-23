// Opciones de vite-plugin-pwa (#15), aparte para poder probarlas sin arrancar Vite.
// El service worker solo precarga la aplicación (HTML, JS, CSS, iconos): nunca respuestas
// de la API ni tokens. No hay runtimeCaching, así que cualquier otra petición va a la red.

const AZUL = "#2563eb";

export const opcionesPWA = {
  // Nunca recarga sola: el operario puede estar a mitad de una inspección
  registerType: "prompt",
  // El <link rel="manifest"> lo inyecta el plugin. Los iconos de public/ ya entran por
  // globPatterns: includeAssets o includeManifestIcons los repetirían en la precarga
  includeManifestIcons: false,
  manifest: {
    name: "CheckIt",
    short_name: "CheckIt",
    description: "Inspecciones de alojamientos con evidencias selladas.",
    lang: "es",
    display: "standalone",
    start_url: "/",
    theme_color: AZUL,
    background_color: "#ffffff",
    icons: [
      { src: "pwa-64x64.png", sizes: "64x64", type: "image/png" },
      { src: "pwa-192x192.png", sizes: "192x192", type: "image/png" },
      { src: "pwa-512x512.png", sizes: "512x512", type: "image/png" },
      { src: "maskable-icon-512x512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
  },
  workbox: {
    globPatterns: ["**/*.{js,css,html,svg,png,ico,woff2}"],
    navigateFallback: "index.html",
    // Una navegación a /api (p. ej. /api/v1/docs/ en local) va siempre a la red
    navigateFallbackDenylist: [/^\/api(\/|$)/],
    cleanupOutdatedCaches: true,
    // El primer service worker controla ya la pestaña abierta: si no, «Actualizar» no
    // recargaría hasta la siguiente navegación. Las versiones nuevas siguen esperando al
    // botón (sin skipWaiting)
    clientsClaim: true,
  },
  devOptions: { enabled: false },
};
