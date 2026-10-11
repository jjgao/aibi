/// <reference types="vite/client" />

/** Whether this is the end-to-end build, which holds the harness: a boolean literal the build
 * substitutes (`vite.config.ts` derives it from the build's mode, the one value the gate's
 * harness rule also reads; Vitest's configuration gives `false`). Not `import.meta.env`, whose
 * values Vitest reads from `process.env` as strings, where `"false"` is true. */
declare const __AIBI_E2E__: boolean;
