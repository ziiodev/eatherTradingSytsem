import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "node:path";

/**
 * Vitest config for the dashboard.
 *
 * - `environment: "happy-dom"` mirrors a browser DOM without the cost of
 *   jsdom and is the project standard for JS tests.
 * - `@vitejs/plugin-react` lets Vitest transpile TSX without a separate
 *   Next/Babel toolchain.
 * - `@/` alias matches `tsconfig.json`'s `paths` entry so test imports look
 *   identical to runtime imports.
 */
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  test: {
    environment: "happy-dom",
    setupFiles: ["./vitest.setup.ts"],
    globals: true,
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
    css: false,
  },
});
