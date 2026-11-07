import React from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import "@fontsource/familjen-grotesk/500.css";
import "@fontsource/familjen-grotesk/700.css";
import "@fontsource/atkinson-hyperlegible/400.css";
import "@fontsource/atkinson-hyperlegible/700.css";
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
