/**
 * An `<option>` of a server text (D428). A native list shows an option's text alone (`option.text`
 * flattens any element), so the option holds the visible form's `flat` form: no span, each escape
 * literal text (`\u202e`, as the same six characters in the data would read: the residual D428
 * names), cut at 2,000 code points and marked with `…`; no raw unsafe character reaches it. Its DOM
 * value is its index (`Key`), which the list's `selectedIndex` gives back: never the text, and never a
 * `Choice` (an object, no `<select>` value: TS2322 by design).
 */
import type { ReactElement } from "react";

import { type ServerText, showText } from "../api";
import { FORM } from "./form";
import type { Key } from "./key";

/** The option of `label`, at index `at`. */
export function Option({ label, at }: { readonly label: ServerText; readonly at: Key }): ReactElement {
  return (
    <option value={at}>
      <FORM value="flat">{showText(label)}</FORM>
    </option>
  );
}
