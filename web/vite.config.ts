/**
 * The build of the web bundle (D411, D418). Two modes, and only two: `production` (`dist/`, the
 * bundle the server ships) and `e2e` (`dist-e2e/`, the same source with the harness, which the
 * Playwright job serves). The mode is the one value that decides both whether the harness is
 * compiled in (`__AIBI_E2E__`) and whether the gate admits it, so that no
 * environment variable can give a production build the one without the other.
 *
 * Build under `umask 022` (the npm scripts do): the loader refuses a bundle whose files group or
 * others can write (D410).
 */
import react from "@vitejs/plugin-react";
import { defineConfig, type UserConfig } from "vite";

import { noWorker, operatorGate } from "./plugins/gate.ts";
import {
  ASSET_FILE_NAMES,
  CHUNK_FILE_NAMES,
  ENTRY_FILE_NAMES,
  sanitizeFileName,
} from "./plugins/names.ts";

export const MODES = { production: "dist", e2e: "dist-e2e" } as const;

export function buildConfig(mode: string): UserConfig {
  if (mode !== "production" && mode !== "e2e") {
    throw new Error(`vite.config.ts builds the modes production and e2e alone, not ${mode}`);
  }
  const e2e = mode === "e2e";
  return {
    base: "/",
    publicDir: false,
    define: { __AIBI_E2E__: JSON.stringify(e2e) },
    plugins: [react(), operatorGate({ harness: e2e })],
    // Vite builds each worker in a build of its own, which `plugins` never sees: the gate refuses
    // any worker there (D418).
    worker: { plugins: () => [noWorker()] },
    build: {
      outDir: MODES[mode],
      emptyOutDir: true,
      manifest: true,
      assetsDir: "assets",
      assetsInlineLimit: 0,
      sourcemap: false,
      // Every browser the bundle supports preloads modules itself (Chromium 66 and later; the
      // bundle needs Chromium 114 for JSON source text), so Vite's polyfill would be dead code.
      modulePreload: { polyfill: false },
      rolldownOptions: {
        input: { index: "index.html", operator: "operator.html" },
        output: {
          entryFileNames: ENTRY_FILE_NAMES,
          chunkFileNames: CHUNK_FILE_NAMES,
          assetFileNames: ASSET_FILE_NAMES,
          sanitizeFileName,
        },
      },
    },
  };
}

export default defineConfig(({ mode }) => buildConfig(mode));
