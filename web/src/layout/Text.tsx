/**
 * A server text in the DOM (D312, D428): `Text`, a text from data, isolated in a `<bdi>` of its own
 * so that its direction cannot reorder the page around it, cut at 2,000 code points and marked; and
 * `Ref`, a pointer, a statistic reference or a data token, isolated alike and not cut (the server's
 * page cuts neither). Each renders the visible form through `showText`, the one door from a server
 * text to the DOM, **in a form of its own** (`form.ts`): each sets it around the door, so that what
 * it shows never depends on an ancestor, and no module outside the layout and the door can set one;
 * the styling is `visible.css`, a stylesheet the build emits (D411).
 */
import "./visible.css";

import type { ReactElement } from "react";

import { type ServerText, showText } from "../api";
import { FORM } from "./form";

/** A text from data: isolated, cut at 2,000 code points and marked, each escape in its span. */
export function Text({ value }: { readonly value: ServerText }): ReactElement {
  return (
    <bdi className="data">
      <FORM value="cut">{showText(value)}</FORM>
    </bdi>
  );
}

/** A pointer, a statistic reference or a data token: isolated, never cut; a data token of a message
 * (`token`) is also marked as data, so that it is told from the server's words around it, and the
 * server's own cut mark (`truncated`: the server cut it, never the page) stands inside the isolate,
 * where the page's own mark of `Text` does: one placement. `truncated` is `Segments`' to set, from the
 * server's own flag, and a public prop for it (round 2 of #117, m7: a screen could put the server's
 * mark on a text the server never cut; the mark is a word, never a text from data). */
export function Ref({ value, token = false, truncated = false }: { readonly value: ServerText; readonly token?: boolean; readonly truncated?: boolean }): ReactElement {
  return (
    <bdi className={token ? "data token" : "data"}>
      <FORM value="whole">{showText(value)}</FORM>
      {truncated ? (
        <span className="cut" title="cut">
          …
        </span>
      ) : null}
    </bdi>
  );
}
