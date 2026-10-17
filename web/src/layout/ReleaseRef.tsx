/**
 * A release an output drew on (D430; D427's R27, §8.1, §12.3): `@` and its label, shown as
 * `@<label>` only for a published status with a numbered label and as `@draft` otherwise (a draft is
 * never citable, so it is never shown as a published label), with its dataset where the reference names one and its manifest. The label is a server number
 * (shown through `Num`) or the word `draft`; its status is read through `kindOf`. Where the label
 * and the status disagree (the schema's `if`/`then`, which the walk does not check, D426), or either
 * is a word the page does not know, the release is shown as a draft, with the disagreement said in
 * fixed words: never as a published release.
 */
import type { ReactElement } from "react";

import { isServerNumber, kindOf, Num, type ServerNumber, type ServerText } from "../api";
import { Ref } from "./Text";

/** A release reference (`ReleaseRef`) or a release (`ReleaseOut`), as the typed answers give it. */
export interface Release {
  readonly dataset?: ServerText;
  readonly label: ServerNumber | ServerText;
  readonly manifest: ServerText;
  readonly status: ServerText;
}

/** The words of a release whose label and status disagree: fixed. */
export const DISAGREE = "(its label and its status disagree: shown as a draft)";

/** A release: its dataset, `@` and its label (`@draft` for a draft), and its manifest. */
export function ReleaseRef({ value }: { readonly value: Release }): ReactElement {
  const status = kindOf(value.status, "ReleaseRef.status");
  const label = value.label;
  const numbered = isServerNumber(label) ? label : null;
  const draftLabel = !isServerNumber(label) && kindOf(label, "ReleaseRef.label") === "draft";
  const published = status === "published" && numbered !== null;
  const draft = status === "draft" && draftLabel;
  return (
    <span className="release">
      {value.dataset === undefined ? null : <Ref value={value.dataset} />}
      {published ? (
        <span className="label">
          @<Num value={numbered} />
        </span>
      ) : (
        <span className="label draft">@draft</span>
      )}
      {published || draft ? null : <span className="disagree"> {DISAGREE}</span>} · manifest <Ref value={value.manifest} />
    </span>
  );
}
