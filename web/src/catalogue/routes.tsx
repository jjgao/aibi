import type { ReactElement } from "react";
import type { RouteObject } from "react-router";

import type { DatasetId } from "../api";
import { DatasetAddress } from "../router/address";
import { Catalogue } from "./Catalogue";
import { Failure } from "./Failure";

/** The not-found screen's module, loaded on first use: its component typed by what it returns (an
 * element), so that the display lint reads its tag (D429, R90: a `ComponentType` returns a `ReactNode`). */
export type NotFoundModule = { NotFound: () => ReactElement };

export function loadNotFound(): Promise<NotFoundModule> {
  return import("./NotFound");
}

/** The dataset's screen's module, loaded on first use, typed alike. */
export type DatasetModule = { Dataset: (props: { readonly id: DatasetId }) => ReactElement };

export function loadDatasetModule(): Promise<DatasetModule> {
  return import("../dataset/Dataset");
}

/** The catalogue's routes (hash routing: the server serves no path below the entry's own,
 * D410): `/`, the catalogue; `/datasets/:id`, whose parameter the router module reads through the
 * address door (D428: an id renders the dataset's screen, lazy, keyed by the id so that another id
 * mounts it anew, and anything else fixed words); and an explicit not-found route for every other
 * hash, its screen lazy. Every element is JSX written in place (D431: no component called or passed
 * as a value, no `Component`; `tests/gates/router-surface.test.ts` walks them). Every route shows
 * the error element's fixed words if it fails (`Failure`: never the error's message). `load` and
 * `loadDataset` are the lazy modules' loaders, parameters so that a test can make one fail. */
export function catalogueRoutes(load: () => Promise<NotFoundModule> = loadNotFound, loadDataset: () => Promise<DatasetModule> = loadDatasetModule): RouteObject[] {
  return [
    {
      errorElement: <Failure />,
      children: [
        { index: true, element: <Catalogue /> },
        {
          path: "datasets/:id",
          lazy: async () => {
            const { Dataset } = await loadDataset();
            return { element: <DatasetAddress screen={(found) => <Dataset key={found} id={found} />} /> };
          },
        },
        {
          path: "*",
          lazy: async () => {
            const found = await load();
            return { element: <found.NotFound /> };
          },
        },
      ],
    },
  ];
}
