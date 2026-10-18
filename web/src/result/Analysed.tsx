/**
 * One position's analysed counts (D435; D427's R19; §6.6, §8.1): the units analysed (`n`), the units
 * excluded (`excluded_units`) and those excluded by reason, every reason and its zero, each a count
 * shown through `Count` (a `null` count as suppressed, R14), a suppressed map as suppressed; then, per
 * variable, the same three, in the view's column order: each under its label when the values seam
 * read the labels and the variables line up with them (`labels`), and otherwise in order under fixed
 * words, never shifted (D434's `variables`).
 */
import { Fragment, type ReactElement } from "react";

import { type Boxed, type components, entriesOf, type ServerText } from "../api";
import { Count, SUPPRESSED } from "../layout/Figure";
import { keyOf } from "../layout/key";
import { Ref, Text } from "../layout/Text";

type Counts = Boxed<components["schemas"]["Analysed"]> | Boxed<components["schemas"]["AnalysedVariable"]>;

/** The fixed words of the analysed counts. */
export const ANALYSED_WORDS = {
  unlabelled: "Per variable, in the view's column order (labels not shown: the page and the server disagree about them):",
} as const;

/** The three counts of an analysed entry or of one of its variables. */
function Three({ value }: { readonly value: Counts }): ReactElement {
  const excluded = value.excluded;
  return (
    <>
      n <Count value={value.n} />; excluded_units <Count value={value.excluded_units} />; excluded by reason{" "}
      {excluded === null ? (
        <span className="suppressed">{SUPPRESSED}</span>
      ) : (
        <span role="list">
          {entriesOf(excluded).map(([reason, count], index) => (
            <Fragment key={keyOf(index)}>
              {index === 0 ? null : ", "}
              <span role="listitem">
                <Ref value={reason} /> <Count value={count} />
              </span>
            </Fragment>
          ))}
        </span>
      )}
    </>
  );
}

/** One position's analysed counts (the module's docstring); `labels` is `null` when the variables
 * are not labelled. */
export function Analysed({ value, labels }: { readonly value: Boxed<components["schemas"]["Analysed"]>; readonly labels: readonly ServerText[] | null }): ReactElement {
  const variables = value.variables ?? [];
  return (
    <div className="analysed" aria-label="analysed">
      <p>
        Analysed: <Three value={value} />
      </p>
      {variables.length > 0 && labels === null ? <p className="note">{ANALYSED_WORDS.unlabelled}</p> : null}
      {variables.length === 0 ? null : (
        <div role="list">
          {variables.map((variable, index) => {
            const label = labels?.[index];
            return (
              <p key={keyOf(index)} role="listitem">
                {label === undefined ? null : (
                  <>
                    <Text value={label} />:{" "}
                  </>
                )}
                <Three value={variable} />
              </p>
            );
          })}
        </div>
      )}
    </div>
  );
}
