/**
 * `Link` (D428): the app's one link to a dataset, made from a `DatasetId` alone (`idOf`, from a
 * server text, or `idOfAddress`, from the address: the generated grammar bounds its characters), so
 * that its target is `#/datasets/<id>` composed here, a fragment of the page's own document: never a
 * text from data, never an address another site wrote, never `javascript:` or another origin.
 * Anything else is refused by type.
 */
import type { ReactElement, ReactNode } from "react";

import type { ColumnId, DatasetId } from "../api";

/** A link to the dataset `to`'s screen. */
export function Link({ to, children }: { readonly to: DatasetId; readonly children: ReactNode }): ReactElement {
  // A template of a literal and a `DatasetId`, by type (D429: an `href` is of a literal type).
  const target: `#/datasets/${DatasetId}` = `#/datasets/${to}`;
  return <a href={target}>{children}</a>;
}

/** A link to the column `column` of the dataset `dataset`. */
export function ColumnLink({ dataset, column, children }: { readonly dataset: DatasetId; readonly column: ColumnId; readonly children: ReactNode }): ReactElement {
  const target: `#/datasets/${DatasetId}/columns/${ColumnId}` = `#/datasets/${dataset}/columns/${column}`;
  return <a href={target}>{children}</a>;
}
