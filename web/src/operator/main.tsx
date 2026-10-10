import { lazy, StrictMode, Suspense } from "react";
import { createRoot } from "react-dom/client";

import { REFUSALS, start } from "../api";
import { rootElement } from "../root";
import { Operator } from "./Operator";
import { forgetAddress } from "./url";

forgetAddress();

// The harness exists in the end-to-end build alone: `__AIBI_E2E__` is the constant `false` in
// production, so this import is folded away and the build's gate refuses any module
// of `src/harness/` it still finds (`plugins/gate.ts`).
const Harness = __AIBI_E2E__ ? lazy(() => import("../harness/Harness")) : null;

// The client starts before anything renders: it seals every body reader but its own (D419), or,
// on an engine that cannot read a server number exactly, the page shows why and nothing else.
if (start()) {
  createRoot(rootElement()).render(
    <StrictMode>
      <Operator />
      {Harness === null ? null : (
        <Suspense fallback={null}>
          <Harness />
        </Suspense>
      )}
    </StrictMode>,
  );
} else {
  rootElement().textContent = REFUSALS.unsupported;
}
