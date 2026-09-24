// main.tsx — entry point of the React app.
//
// Vite loads this file (see index.html). Here we:
//   1) grab the #root div,
//   2) wrap the App with the AuthProvider (for the login state),
//   3) enable React Router (for the various pages),
//   4) have React render the app.

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
