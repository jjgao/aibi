import { lazy, StrictMode, Suspense } from "react";
import { createRoot } from "react-dom/client";

import { rootElement } from "../root";
import { Operator } from "./Operator";
import { forgetAddress } from "./url";

forgetAddress(window.history);

// The harness exists in the end-to-end build alone: `__AIBI_E2E__` is the constant `false` in
// production, so this import is folded away and the build's gate refuses any module
// of `src/harness/` it still finds (`plugins/gate.ts`).
const Harness = __AIBI_E2E__ ? lazy(() => import("../harness/Harness")) : null;

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
