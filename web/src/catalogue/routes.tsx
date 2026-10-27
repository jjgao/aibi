import { Fragment, type ReactElement } from "react";
import type { RouteObject } from "react-router";

import { type Answered, type DatasetId, describeColumn, type Outcome } from "../api";
import { ColumnAddress, DatasetAddress } from "../router/address";
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

/** The column's screen's module, loaded on first use, typed alike: handed its dataset's id and the
 * call that describes the column, never the column's id (D443). */
export type ColumnModule = {
  Column: (props: { readonly dataset: DatasetId; readonly describe: () => Promise<Outcome<Answered<"describe_column">>> }) => ReactElement;
};

export function loadColumnModule(): Promise<ColumnModule> {
  return import("../dataset/Column");
}

/** The catalogue's routes (hash routing: the server serves no path below the entry's own,
 * D410): `/`, the catalogue; `/datasets/:id`, whose parameter the router module reads through the
 * address door (D428: an id renders the dataset's screen, lazy, keyed by the id so that another id
 * mounts it anew, and anything else fixed words); `/datasets/:id/columns/:column`, a sibling (so the
 * dataset's screen and its builder unmount), whose two parameters the router module reads through
 * the two doors (D443: two ids render the column's screen, lazy, handed the dataset's id and the
 * call that describes the column, and keyed by both ids, nested, so that another dataset's column of
 * the same id, or another column of the same dataset, mounts it anew); and an explicit not-found
 * route for every other hash, its screen lazy. Every element is JSX written in place (D431: no
 * component called or passed as a value, no `Component`; `tests/gates/router-surface.test.ts` walks
 * them). Every route shows the error element's fixed words if it fails (`Failure`: never the error's
 * message). `load`, `loadDataset` and `loadColumn` are the lazy modules' loaders, parameters so that
 * a test can make one fail. */
export function catalogueRoutes(load: () => Promise<NotFoundModule> = loadNotFound, loadDataset: () => Promise<DatasetModule> = loadDatasetModule, loadColumn: () => Promise<ColumnModule> = loadColumnModule): RouteObject[] {
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
          path: "datasets/:id/columns/:column",
          lazy: async () => {
            const { Column } = await loadColumn();
            return {
              element: (
                <ColumnAddress
                  screen={(found, named) => (
                    <Fragment key={found}>
                      <Column key={named} dataset={found} describe={() => describeColumn({ dataset: found, column: named })} />
                    </Fragment>
                  )}
                />
              ),
            };
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
