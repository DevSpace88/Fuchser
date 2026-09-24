// main.tsx — Einstiegspunkt der React-App.
//
// Vite lädt diese Datei (siehe index.html). Hier:
//   1) holen wir uns das #root-div,
//   2) umhüllen die App mit dem AuthProvider (für den Login-State),
//   3) aktivieren React Router (für die verschiedenen Seiten),
//   4) lassen React die App rendern.

import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { AuthProvider } from "@/lib/auth";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <AuthProvider>
        <App />
      </AuthProvider>
    </BrowserRouter>
  </React.StrictMode>,
);
