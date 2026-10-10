/** The operator entry's one address. */
export const CURATE_PATH = "/curate";

/** Forget whatever the operator entry's URL holds besides its path, without reading it: the
 * entry takes nothing from its URL (D412), so a hash such as `#/withdraw?x`, or a query, is
 * ignored, and replaced (never pushed, so no history entry is added and none is left holding it)
 * by the fixed path. `location.hash = ""` would not do: it keeps a `#` and adds an entry. The lint
 * refuses `history.replaceState` in all of `src/` but this one call (D423): the page's objects are
 * used by direct member access alone, so the history is not passed in. */
export function forgetAddress(): void {
  history.replaceState(null, "", CURATE_PATH);
}
