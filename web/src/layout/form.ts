/**
 * The form a server text is shown in, by its position (D428): the context `showText`'s element
 * (`src/api/text.tsx`, the one door from a server text to the DOM) reads as it renders. **Each
 * component of the layout sets its own form** around the door (`Text` `cut`, `Ref` and a text
 * segment of `Segments` `whole`, `Option` `flat`), so that no component's output depends on an
 * ancestor; and this module is imported by `src/layout/` and `src/api/text.tsx` alone (a lint row,
 * `tests/gates/layout-form.test.ts`'s scan of the imports), so that no screen can set a position's
 * form around a component. Every form escapes the same code points, and they differ in the cut and
 * the spans alone:
 *
 * - `cut` (the default): a text from data, cut at 2,000 code points and marked (D312: labels,
 *   names, descriptions, grains, tags, terms, values), each escaped run a `<span class="escape">`;
 * - `whole`: not cut, as the server's page does not cut them: pointers, statistic references, data
 *   tokens (which the server cuts and marks itself, `truncated`) and the server's own words;
 * - `flat`: an `<option>`'s text, which a native list shows as text alone: no span, so each escape
 *   is literal text (`\u202e`, six characters, as the same characters written in the data would
 *   read: the residual, named in D428), cut and marked with `…`.
 */
import { createContext } from "react";

/** The forms of a position. */
export type Form = "cut" | "whole" | "flat";

/** The form of the nearest position that sets one; `cut` where none does. */
export const FORM = createContext<Form>("cut");
