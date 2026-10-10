/** The unit tests (`tests/unit`, the skeleton in jsdom) and the build's tests (`tests/gates`:
 * the gate on real Vite builds, `check-bundle`, `check-licenses`), in Node. */
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  define: { __AIBI_E2E__: "false" },
  test: {
    projects: [
      {
        extends: true,
        test: { name: "unit", environment: "jsdom", include: ["tests/unit/**/*.test.{ts,tsx}"] },
      },
      {
        extends: true,
        test: {
          name: "gates",
          environment: "node",
          include: ["tests/gates/**/*.test.ts"],
          testTimeout: 120_000,
          hookTimeout: 120_000,
        },
      },
    ],
  },
});
