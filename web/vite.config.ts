import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { fileURLToPath, URL } from "node:url";

// The build lands inside the Python package, so `pip install .` ships the UI and the
// gateway serves it without Node. `npm run dev` proxies the API to a local gateway.
const api = process.env.RATCHET_API || "http://localhost:8000";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  build: { outDir: "../ratchet/web/dist", emptyOutDir: true, sourcemap: false, chunkSizeWarningLimit: 900 },
  server: {
    port: 5173,
    proxy: {
      "/ratchet": api,
      "/scout/": api,
      "/v1": api,
    },
  },
});
