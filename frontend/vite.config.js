import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development the API runs on :8000; the proxy keeps the browser on a single origin.
export default defineConfig({
  plugins: [react()],
  // The on-device face model (opt-in camera attention check) is a separate, lazily loaded chunk; the warning is expected.
  build: { chunkSizeWarningLimit: 1700 },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/ws": { target: "ws://127.0.0.1:8000", ws: true },
    },
  },
});
