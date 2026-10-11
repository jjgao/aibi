import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createHashRouter } from "react-router";
import { RouterProvider } from "react-router/dom";

import { REFUSALS, start } from "../api";
import { rootElement } from "../root";
import { catalogueRoutes } from "./routes";

// The client starts before anything renders: it seals every body reader but its own (D419), or,
// on an engine that cannot read a server number exactly, the page shows why and nothing else.
if (start()) {
  const router = createHashRouter(catalogueRoutes());
  createRoot(rootElement()).render(
    <StrictMode>
      <RouterProvider router={router} />
    </StrictMode>,
  );
} else {
  rootElement().textContent = REFUSALS.unsupported;
}
