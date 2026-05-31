import js from "@eslint/js";
import globals from "globals";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import { defineConfig, globalIgnores } from "eslint/config";

export default defineConfig([
  // Ignora artefactos de build generados por Vite.
  globalIgnores(["dist"]),
  {
    // Aplica esta configuración a JS/JSX del proyecto.
    files: ["**/*.{js,jsx}"],
    extends: [
      // Reglas base recomendadas de ESLint para JavaScript.
      js.configs.recommended,
      // Reglas para uso correcto de hooks de React.
      reactHooks.configs.flat.recommended,
      // Reglas compatibles con Fast Refresh en entorno Vite.
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      // Variables globales del navegador (window, document, etc.).
      globals: globals.browser,
      // Habilita parsing de sintaxis JSX.
      parserOptions: { ecmaFeatures: { jsx: true } },
    },
  },
]);
