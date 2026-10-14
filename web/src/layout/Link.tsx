/**
 * `Link` (D428): the app's one link to a dataset, made from a `DatasetId` alone (`idOf`, from a
 * server text, or `idOfAddress`, from the address: the generated grammar bounds its characters), so
 * that its target is `#/datasets/<id>` composed here, a fragment of the page's own document: never a
 * text from data, never an address another site wrote, never `javascript:` or another origin.
 * Anything else is refused by type.
 */
import type { ReactElement, ReactNode } from "react";

import type { DatasetId } from "../api";

/** A link to the dataset `to`'s screen. */
export function Link({ to, children }: { readonly to: DatasetId; readonly children: ReactNode }): ReactElement {
  return <a href={`#/datasets/${to}`}>{children}</a>;
}
