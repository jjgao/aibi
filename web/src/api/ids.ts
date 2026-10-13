/**
 * The four request ids (D425): a proposal's id (`path:proposal`, `Accept.proposal`), a release's
 * label (`query:release`) and the open session's id (`TakeOver.session`), the request positions
 * that take back a number a response gave. Each is an id the server counts its own events by (a
 * label is `max(label) + 1` per dataset, a session the app database's row id, a proposal a global
 * `AUTOINCREMENT`), never a count of data, so the box guards their integrity here (a count is
 * never sent where an id goes), not disclosure (§8.4, D419).
 *
 * **`RequestId`** is the generated types' type at those four positions (`generate-types.mjs`
 * reads them from `classes.json`'s `from`): a number branded by a symbol no other module can
 * name, so that no plain number, no box and no other value written in the ordinary way compiles
 * there, through `exchange` or `routes`; the way to make one is `requestId`, the grammar, which
 * `provenance.ts`'s `idAt` alone imports (the lint's table of the internals of `src/api/`, the
 * import-graph test); `curator.ts` imports nothing from this module: the `RequestId` it sends is the one
 * `idAt` gives it.
 *
 * **What the brand is not:** a proof. It is static (at run time a `RequestId` is a plain number),
 * and ways of minting one without a cast that the checker and the lint allow exist and are not
 * exhaustively closed: TypeScript's unsound variance and overload implementations (examples: an
 * overload whose implementation returns `unknown`, method-parameter bivariance, array and
 * mutable-property covariance, `Map.set`'s bivariance; `request-half.test.ts` pins them as
 * examples, not as a list). D419 draws its boundary at accident or a lazy hand, so no lint rule is
 * added for them (`any` is the lint's). The guarantee that holds is structural: the operator
 * client's four methods take decoded nodes, never routes or ids, and build the URL and the body
 * inside `src/api/` from the grammar's reading of the node's own box (D425), and every such mint
 * reaches only `exchange` and `routes`, which carry no credentials.
 * (The same variance reaches a *box* to a primitive place of the operator client; that is harmless
 * because no member's body can read a box's text: `idAt` alone does, from a stamped node, D425.)
 *
 * **The grammar** is the canonical decimal spelling of a whole number from 1 to 2^53 - 1:
 * `^[1-9][0-9]*$` and at most `9007199254740991`, compared as text (no rounding: `9007199254740993`
 * is refused, not read as `9007199254740992`). The server is laxer than this (a path or a query
 * takes `01`, `+1`, ` 1`, `1.0`; a body `1e0`, `1.0`), so the client is the only guard of the
 * spelling: a box's text that is not canonical is refused, never repaired.
 */

/** The brand: a symbol held by this module alone (its value is never used; its type is the brand). */
const REQUEST_ID: unique symbol = Symbol("aibi.request-id");

/** A request id: a number only `requestId` makes. */
export type RequestId = number & { readonly [REQUEST_ID]: true };

/** The canonical spelling of a whole number from 1 on. */
const CANONICAL = /^[1-9][0-9]*$/u;

/** The largest request id, 2^53 - 1, as text. */
const LARGEST = "9007199254740991";

/** Whether a text is a request id's spelling: canonical, and at most 2^53 - 1 (compared as text of
 * equal length, so no rounding decides it). */
export function isRequestIdText(text: string): boolean {
  return CANONICAL.test(text) && (text.length < LARGEST.length || (text.length === LARGEST.length && text <= LARGEST));
}

/** The request id a text spells, or `null` (`isRequestIdText`). */
export function requestId(text: string): RequestId | null {
  return isRequestIdText(text) ? (Number(text) as RequestId) : null;
}
