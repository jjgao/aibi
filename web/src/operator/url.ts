/** The operator entry's one address. */
export const CURATE_PATH = "/curate";

/** Forget whatever the operator entry's URL holds besides its path, without reading it: the
 * entry takes nothing from its URL (D412), so a hash such as `#/withdraw?x`, or a query, is
 * ignored, and replaced (never pushed, so no history entry is added and none is left holding it)
 * by the fixed path. `location.hash = ""` would not do: it keeps a `#` and adds an entry. */
export function forgetAddress(history: Pick<History, "replaceState">): void {
  history.replaceState(null, "", CURATE_PATH);
}
