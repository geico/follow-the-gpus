import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";
import { boardApi } from "./server/plugin.ts";

// base './' makes dist/ work under any path (spec 0006 section 3).
export default defineConfig({
  base: "./",
  plugins: [react(), tailwindcss(), boardApi()],
  build: {
    // index.html is the board entry.
    rollupOptions: {
      input: { index: "index.html" },
    },
  },
  test: {
    include: ["test/**/*.test.{ts,tsx}"],
    environment: "node",
  },
});
