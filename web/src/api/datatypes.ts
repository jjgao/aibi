/**
 * The datatypes the document seam offers (D432), by role. The server's rules they follow are rows of
 * D432's table (`web/tests/fixtures/seam-rules.json`), each held by its live cells, and none is stated
 * here: `leaf` is the `leaf-list` row (with `not-permissible`), `view` is the `view-lists` row (with
 * `view-codes` and `view-ancestors`: `optionsOf` lists a unit's own table's columns alone), and
 * `view_under_k` is the `view-lists` row's list under a floor. The module is code (a JSON module is not
 * imported in `src/`, the build's gate refusing it), held equal to `web/tests/fixtures/datatypes.json`
 * by `tests/unit/datatypes.test.ts`, which the server's test
 * (`server/tests/core/analyses/test_web_datatypes.py`) reads and holds to the server.
 *
 * Each word is a member of the generated `ColumnBrief.datatype` vocabulary (by type, and by the
 * test: the `datatype-vocabulary` row).
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
  "list<category>": "left out of the leaves by this seam: offering it, with a quantifier, is M5.3-rest's choice",
}) satisfies { readonly [datatype in Datatype]?: string };
