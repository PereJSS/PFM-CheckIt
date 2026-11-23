import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { VitePWA } from "vite-plugin-pwa";
import { opcionesPWA } from "./pwa.config.js";

export default defineConfig({
  plugins: [react(), tailwindcss(), VitePWA(opcionesPWA)],
  server: {
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
