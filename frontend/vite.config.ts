import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Local-only dev server. The API base URL is injected via VITE_API_BASE_URL so
// no host is hard-coded. Production serves the built ./dist from the API.
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    // Dev-only: proxy API + health calls to the backend so the browser stays
    // same-origin (no CORS needed). Production serves the built UI from the API.
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/health": { target: "http://127.0.0.1:8000", changeOrigin: true },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
  },
});
