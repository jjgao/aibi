/**
 * The page's operator client (D423): the one `curator.ts` client of the operator entry, its
 * listeners installed on the page as this module loads (the idle lock's input events,
 * `visibilitychange`, `pagehide` and `pageshow`). Those listeners are why this module is no part
 * of `index.ts`: re-exported there, it would be in the catalogue entry's bundle, its listeners and
 * all. The operator entry's modules (`src/operator/`) and the end-to-end harness import it by its
 * own path, and nothing else may (the lint, the import-graph test and the build's gate, which
 * also refuses it in the catalogue entry's graph).
 */
import { createCurator } from "./curator";

export type { Edit, LockReason, Outcome, Unlocked, View } from "./curator";
export { IDLE_LIMIT_MS, NAME_LIMIT } from "./curator";

/** The page's operator client. */
export const operator = createCurator();

// The page's objects are used by direct member access alone (the lint, D423), so the client is
// given what it listens with, not the objects.
operator.install({
  window: {
    addEventListener: (type, listener, options) => {
      window.addEventListener(type, listener, options);
    },
  },
  document: {
    addEventListener: (type, listener, options) => {
      document.addEventListener(type, listener, options);
    },
  },
});
