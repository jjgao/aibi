/** Forget whatever the operator entry's URL holds besides its path, without reading it: the
 * entry takes nothing from its URL (D412), so a hash such as `#/withdraw?x`, or a query, is
 * ignored, and replaced (never pushed, so no history entry is added and none is left holding it)
 * by the fixed path. `location.hash = ""` would not do: it keeps a `#` and adds an entry.
 *
 * The lint refuses `history.replaceState` everywhere in `src/` but this one call (D423): this file
 * is nothing but this function, the path is written out here, a literal and no binding (a name
 * could be given any value), and the call is on the global `history`, which is used by member
 * access alone. */
export function forgetAddress(): void {
  history.replaceState(null, "", "/curate");
}
