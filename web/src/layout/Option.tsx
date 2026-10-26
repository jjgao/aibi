/**
 * An `<option>` of a server text (D428). A native list shows an option's text alone (`option.text`
 * flattens any element), so the option holds the visible form's `flat` form: no span, each escape
 * literal text (`\u202e`, as the same six characters in the data would read: the residual D428
 * names), cut at 2,000 code points and marked with `…`; no raw unsafe character reaches it. Its DOM
 * value is its index (`Key`), which the list's `selectedIndex` gives back: never the text, and never a
 * `Choice` (an object, no `<select>` value: TS2322 by design). `NoOption` is a list's first option,
 * "(choose)" with the value `""`: a list's nothing chosen, fixed words (D433).
 *
 * An option's text is its `label` (the id), then, when it has a `name` (the descriptor's label, D441),
 * ` — ` and the name: both through the same flat form, each cut at 2,000 code points of data, the id
 * first so that no name displaces it. The name is shown as sent (an empty one leaves `id — `), never
 * isolated (an option holds no `<bdi>`, and the page writes no raw isolate: the id begins
 * with a strong left-to-right letter and comes first, so a right-to-left name can reorder only itself), and a native
 * list collapses its tabs and line feeds to spaces: D441's residuals.
 */
import type { ReactElement } from "react";

import { type ServerText, showText } from "../api";
import { FORM } from "./form";
import type { Key } from "./key";

/** The words of a list's option that chooses nothing: fixed. */
export const NO_OPTION = "(choose)";

/** A list's first option: nothing chosen (its value `""`, never an index). */
export function NoOption(): ReactElement {
  return <option value="">{NO_OPTION}</option>;
}

/** The words between an option's id and its name: fixed. */
const NAME_SEPARATOR = " \u2014 ";

/**
 * The option of `label`, with its `name` when it has one, at index `at`. `label` is the id's box and
 * `name` the descriptor's label (or `null`): the swap-prone pair of `Option<R>` (`src/api/document.ts`).
 */
export function Option({ label, name, at }: { readonly label: ServerText; readonly name: ServerText | null; readonly at: Key }): ReactElement {
  return (
    <option value={at}>
      <FORM value="flat">
        {showText(label)}
        {name === null ? null : NAME_SEPARATOR}
        {name === null ? null : showText(name)}
      </FORM>
    </option>
  );
}
