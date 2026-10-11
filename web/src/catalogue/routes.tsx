import type { ComponentType } from "react";
import type { RouteObject } from "react-router";

import { Failure } from "./Failure";
import { Home } from "./Home";

/** The not-found screen's module, loaded on first use. */
export type NotFoundModule = { NotFound: ComponentType };

export function loadNotFound(): Promise<NotFoundModule> {
  return import("./NotFound");
}

/** The catalogue's routes (hash routing: the server serves no path below the entry's own,
 * D410): `/`, and an explicit not-found route for every other hash, its screen lazy. Every route
 * shows the error element's fixed words if it fails. `load` is the not-found module's loader, a
 * parameter so that a test can make it fail. */
export function catalogueRoutes(load: () => Promise<NotFoundModule> = loadNotFound): RouteObject[] {
  return [
    {
      errorElement: <Failure />,
      children: [
        { index: true, element: <Home /> },
        {
          path: "*",
          lazy: async () => {
            const found = await load();
            return { Component: found.NotFound };
          },
        },
      ],
    },
  ];
}
