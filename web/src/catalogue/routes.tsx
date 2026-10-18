import type { ComponentType } from "react";
import type { RouteObject } from "react-router";

import { DatasetAddress } from "../router/address";
import { Failure } from "./Failure";
import { Home } from "./Home";

/** The not-found screen's module, loaded on first use. */
export type NotFoundModule = { NotFound: ComponentType };

export function loadNotFound(): Promise<NotFoundModule> {
  return import("./NotFound");
}

/** The catalogue's routes (hash routing: the server serves no path below the entry's own,
 * D410): `/`; `/datasets/:id`, whose parameter the router module reads through the address door
 * (D428: an id renders the dataset's screen, until M5.2 the not-found screen, and anything else
 * fixed words); and an explicit not-found route for every other hash, its screen lazy. Every route
 * shows the error element's fixed words if it fails (`Failure`: never the error's message). `load`
 * is the not-found module's loader, a parameter so that a test can make it fail. */
export function catalogueRoutes(load: () => Promise<NotFoundModule> = loadNotFound): RouteObject[] {
  return [
    {
      errorElement: <Failure />,
      children: [
        { index: true, element: <Home /> },
        {
          path: "datasets/:id",
          lazy: async () => {
            const { NotFound } = await load();
            // Until M5.2 builds the dataset's screen, an id lands on the not-found screen.
            return { element: <DatasetAddress screen={() => <NotFound />} /> };
          },
        },
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
