import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Local-only dev server. The API base URL is injected via VITE_API_BASE_URL so
// no host is hard-coded. Production serves the built ./dist from the API.
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
  },
});
