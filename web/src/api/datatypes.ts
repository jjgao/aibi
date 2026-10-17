/**
 * The datatypes the document seam offers (D432), by role: the server's rules, written here and held
 * to the server by a test (`server/tests/core/analyses/test_web_datatypes.py`, which reads
 * `web/tests/fixtures/datatypes.json`, itself held equal to this module by
 * `tests/unit/datatypes.test.ts`; a JSON module is not imported in `src/`, the build's gate
 * refusing it).
 *
 * - **`leaf`**: a value leaf's column takes a typed string only where the server's
 *   `typed_constant` accepts every string (`string`, `category`, `list<category>`), less
 *   `list<category>` (`LEFT_OUT`); a category column with permissible values still refuses another
 *   value (`NOT_PERMISSIBLE`), which the page shows as the server's refusal.
 * - **`view`**: `summary.distribution` summarises a column of these (its `SUMMARISED`); a string,
 *   date or datetime column is `NOT_SUPPORTED` under every *k*, and `list<category>` needs an
 *   aggregate.
 * - **`view_under_k`**: under any effective *k* (D329's edges rule) a numeric column needs bin
 *   edges, which the seam cannot send, so only `_CATEGORIES` run.
 *
 * Each word is a member of the generated `ColumnBrief.datatype` vocabulary (by type, and by the
 * test).
 */
import type { VOCABULARIES } from "./generated/shapes";

/** A column's datatype, as `describe_dataset` names it. */
type Datatype = (typeof VOCABULARIES)["ColumnBrief.datatype"][number];

/** The datatypes the seam offers, by role. */
export const DATATYPES = Object.freeze({
  leaf: Object.freeze(["category", "string"] as const),
  view: Object.freeze(["boolean", "category", "integer", "number", "time_offset"] as const),
  view_under_k: Object.freeze(["boolean", "category"] as const),
}) satisfies { readonly [role: string]: readonly Datatype[] };

/** The datatypes the server would take at a role and the seam leaves out, each with why. */
export const LEFT_OUT = Object.freeze({
  "list<category>": "a leaf's list<category> column takes a typed string, under the server's chosen quantifier (some): offering it, with its quantifier, is M5.3-rest's choice",
}) satisfies { readonly [datatype in Datatype]?: string };
