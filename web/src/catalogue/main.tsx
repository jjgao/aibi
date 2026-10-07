import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createHashRouter } from "react-router";
import { RouterProvider } from "react-router/dom";

import { rootElement } from "../root";
import { catalogueRoutes } from "./routes";

const router = createHashRouter(catalogueRoutes());

createRoot(rootElement()).render(
  <StrictMode>
    <RouterProvider router={router} />
  </StrictMode>,
);
