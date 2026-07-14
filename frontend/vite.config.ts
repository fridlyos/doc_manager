import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Local-only dev server. The client calls relative /api and /health paths and
// this dev server proxies them to the backend, so the browser stays same-origin
// (no CORS). Production serves the built ./dist from the API.
//
// Proxy target is env-driven so the same config works in both contexts:
//   - host `npm run dev`      -> default http://127.0.0.1:8000 (published API)
//   - containerized ui service -> API_PROXY_TARGET=http://api:8000 (compose net)
const apiProxyTarget = process.env.API_PROXY_TARGET ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": { target: apiProxyTarget, changeOrigin: true },
      "/health": { target: apiProxyTarget, changeOrigin: true },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
  },
});
