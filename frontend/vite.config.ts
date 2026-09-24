// ============================================================================
// vite.config.ts — Konfiguration für den Vite-Dev-Server + Build
// ============================================================================
// Vite ist unser Build-Tool: in Entwicklung startet es einen Dev-Server mit
// Hot-Module-Replacement (Code-Änderungen erscheinen sofort im Browser),
// in Produktion erzeugt es optimierte statische Dateien im dist/-Ordner.

import path from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],

  // ---- DEV-SERVER ----
  // In Entwicklung läuft Vite auf Port 5173. Unser Backend läuft auf 8000.
  // Eigentlich wären das zwei verschiedene "Origins" -> der Browser würde
  // API-Calls blockieren (CORS). Es GIBT zwei Wege, das zu lösen:
  //
  //   (a) CORS im Backend erlauben (siehe backend/app/main.py).
  //   (b) Einen Vite-PROXY einrichten: der Browser ruft /api/* auf dem
  //       VITE-Server auf (gleiche Origin!), und Vite leitet intern an den
  //       Backend weiter. KEIN CORS nötig.
  //
  // Wir nutzen BEIDES (CORS als Fallback, Proxy als Hauptweg). Der Proxy
  // ist komfortabler, weil man sich um Tokens-Credentials etc. keine Gedanken
  // machen muss.
  server: {
    host: true, // nötig, damit man aus dem Docker-Container den Host erreicht
    port: 5173,
    proxy: {
      // Alle API-Aufrufe und den OAuth-Login ans Backend weiterleiten.
      "/api": {
        target: process.env.VITE_DEV_BACKEND_URL || "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },

  // ---- Pfad-Alias ----
  // Erlaubt `import { x } from "@/lib/..."` statt relativen Pfaden
  // wie `../../lib/...`. "src" wird zu "@".
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },

  // ---- BUILD-OUTPUT ----
  // Vite schreibt die gebauten Dateien nach "dist". Im Produktions-Setup
  // kopieren wir dieses Verzeichnis ans Backend (siehe Dockerfile + main.py).
  build: {
    outDir: "dist",
  },
});
