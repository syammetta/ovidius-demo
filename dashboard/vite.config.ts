import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      "/qa": "http://localhost:8000",
      "/health": "http://localhost:8000",
      "/traces": "http://localhost:8000",
      "/metrics": "http://localhost:8000",
      "/query-logs": "http://localhost:8000",
      "/eval": "http://localhost:8000",
      "/agent": "http://localhost:8000",
      "/ws": {
        target: "ws://localhost:8000",
        ws: true,
      },
    },
  },
  build: {
    outDir: "../static",
    emptyOutDir: true,
  },
});
