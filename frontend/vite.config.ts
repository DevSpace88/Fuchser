// ============================================================================
// vite.config.ts — Configuration for the Vite dev server + build
// ============================================================================
// Vite is our build tool: in development it starts a dev server with
// hot module replacement (code changes show up in the browser immediately),
// in production it produces optimized static files in the dist/ folder.

import path from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],

  // ---- DEV SERVER ----
  // In development Vite runs on port 5173. Our backend runs on 8000.
  // Strictly speaking those are two different "origins" -> the browser would
  // block API calls (CORS). There ARE two ways to solve this:
  //
  //   (a) Allow CORS in the backend (see backend/app/main.py).
  //   (b) Set up a Vite PROXY: the browser calls /api/* on the
  //       VITE server (same origin!), and Vite forwards internally to the
  //       backend. NO CORS needed.
  //
  // We use BOTH (CORS as a fallback, the proxy as the main path). The proxy
  // is more convenient because you don't have to worry about
  // tokens/credentials etc.
  server: {
    host: true, // needed so the container can reach the host
    port: 5173,
    proxy: {
      // Forward all API calls and the OAuth login to the backend.
      "/api": {
        target: process.env.VITE_DEV_BACKEND_URL || "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },

  // ---- PATH ALIAS ----
  // Allows `import { x } from "@/lib/..."` instead of relative paths
  // like `../../lib/...`. "src" becomes "@".
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },

  // ---- BUILD OUTPUT ----
  // Vite writes the built files to "dist". In the production setup
  // we copy this directory to the backend (see Dockerfile + main.py).
  build: {
    outDir: "dist",
  },
});
