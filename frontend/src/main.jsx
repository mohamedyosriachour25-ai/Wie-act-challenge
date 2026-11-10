import React from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import "@fontsource/manrope/latin-400.css";
import "@fontsource/manrope/latin-ext-400.css";
import "@fontsource/manrope/latin-500.css";
import "@fontsource/manrope/latin-ext-500.css";
import "@fontsource/manrope/latin-600.css";
import "@fontsource/manrope/latin-ext-600.css";
import "@fontsource/manrope/latin-700.css";
import "@fontsource/manrope/latin-ext-700.css";
import "@fontsource/manrope/latin-800.css";
import "@fontsource/manrope/latin-ext-800.css";
import "@fontsource/jetbrains-mono/latin-500.css";
import "@fontsource/jetbrains-mono/latin-700.css";
import "@fontsource/noto-naskh-arabic/arabic-400.css";
import "@fontsource/noto-naskh-arabic/arabic-700.css";
import "@fontsource/noto-kufi-arabic/arabic-500.css";
import "@fontsource/noto-kufi-arabic/arabic-700.css";
import "./styles.css";
import App from "./App.jsx";
import { AuthProvider } from "./lib/auth.jsx";
import { LangProvider } from "./lib/i18n.jsx";

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <BrowserRouter>
      <LangProvider>
        <AuthProvider>
          <App />
        </AuthProvider>
      </LangProvider>
    </BrowserRouter>
  </React.StrictMode>
);
