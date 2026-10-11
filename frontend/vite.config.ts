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

// Windows/WSL bind mounts into a Linux container do not forward inotify events,
// so HMR needs filesystem polling. Opt-in via env (set on the containerized ui
// service) to keep native `npm run dev` fast. See compose.override.yaml.
const usePolling = process.env.VITE_USE_POLLING === "true";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": { target: apiProxyTarget, changeOrigin: true },
      "/health": { target: apiProxyTarget, changeOrigin: true },
    },
    watch: usePolling ? { usePolling: true, interval: 300 } : undefined,
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    // Unit/component tests only; the Playwright E2E specs under e2e/ run separately.
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
  },
});
