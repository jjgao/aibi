/**
 * An `<option>` of a server text (D428). A native list shows an option's text alone (`option.text`
 * flattens any element), so the option holds the visible form's `flat` form: no span, each escape
 * literal text (`\u202e`, as the same six characters in the data would read: the residual D428
 * names), cut at 2,000 code points and marked with `…`; no raw unsafe character reaches it. Its DOM
 * value is its index (`Key`), which the list's `selectedIndex` gives back: never the text, and never a
 * `Choice` (an object, no `<select>` value: TS2322 by design). `NoOption` is a list's first option,
 * "(choose)" with the value `""`: a list's nothing chosen, fixed words (D433).
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

/** The option of `label`, at index `at`. */
export function Option({ label, at }: { readonly label: ServerText; readonly at: Key }): ReactElement {
  return (
    <option value={at}>
      <FORM value="flat">{showText(label)}</FORM>
    </option>
  );
}
