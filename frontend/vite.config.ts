import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The backend API base can be overridden for containerized dev via
// VITE_API_PROXY_TARGET; it defaults to the local uvicorn server.
const apiTarget = process.env.VITE_API_PROXY_TARGET ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    proxy: {
      "/api": {
        target: apiTarget,
        changeOrigin: true,
      },
    },
  },
});
