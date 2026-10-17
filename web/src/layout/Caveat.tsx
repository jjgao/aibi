/**
 * Caveats (D430; D427's R21–R24 and R36, §8.3). `Caveats` shows every caveat of an output, `warn`,
 * `block` and `info` alike, core and pack codes alike, each with its code, its severity and its
 * message (from its segments), and its `affects` pointers as data; **in the server's order** (§8.1
 * sorts them and drops exact duplicates; the page never sorts, merges or drops one); `info` caveats
 * (`SUPPRESSED`, `NOT_ESTIMABLE`, `LIFT_DIFFERS`, `POOLED_ACROSS_DATASETS`) are shown too, secondary
 * (a class of their own, in place, never moved). A severity the page does not know is read as
 * `block` (`kindOf("Severity")` fails closed, D426). `Inspection` puts an output with a `block`
 * caveat under a banner, **for inspection, not an answer**, and shows the caveats with it (R22);
 * an output without one is shown as it is.
 */
import { Fragment, type ReactElement, type ReactNode } from "react";

import { type Boxed, type components, type Kind, kindOf } from "../api";
import { keyOf } from "./key";
import { Segments } from "./Segments";
import { Ref } from "./Text";

/** A caveat, as the typed answers give it. */
export type BoxedCaveat = Boxed<components["schemas"]["Caveat"]>;

/** The words of each severity. */
export const SEVERITY_WORDS = { info: "note", warn: "warning", block: "block" } as const satisfies Record<Kind<"Severity">, string>;

/** The class of a caveat of each severity: `info` is secondary. */
const SEVERITY_CLASS = { info: "caveat info", warn: "caveat warn", block: "caveat block" } as const satisfies Record<Kind<"Severity">, string>;

/** The banner's words over an output with a `block` caveat: fixed. */
export const FOR_INSPECTION = "For inspection, not an answer: a blocking caveat applies to this output (§8.3).";

/** Whether any caveat blocks (an unknown severity read as `block`). */
export function blocks(caveats: readonly BoxedCaveat[]): boolean {
  return caveats.some((caveat) => kindOf(caveat.severity, "Severity") === "block");
}

/** One caveat: its code, its severity, its message and what it affects. */
function One({ caveat }: { readonly caveat: BoxedCaveat }): ReactElement {
  const severity = kindOf(caveat.severity, "Severity");
  return (
    <div role="listitem" className={SEVERITY_CLASS[severity]}>
      <Ref value={caveat.code} /> ({SEVERITY_WORDS[severity]}): <Segments value={caveat.message} />
      {caveat.affects.length === 0 ? null : (
        <span className="affects">
          {" "}
          affects:{" "}
          {caveat.affects.map((pointer, index) => (
            <Fragment key={keyOf(index)}>
              {index === 0 ? null : ", "}
              <Ref value={pointer} />
            </Fragment>
          ))}
        </span>
      )}
    </div>
  );
}

/** The caveats of an output, in the server's order; nothing for none. */
export function Caveats({ caveats }: { readonly caveats: readonly BoxedCaveat[] }): ReactElement | null {
  return caveats.length === 0 ? null : (
    <div role="list" className="caveats" aria-label="caveats">
      {caveats.map((caveat, index) => (
        <One key={keyOf(index)} caveat={caveat} />
      ))}
    </div>
  );
}

/** An output with its caveats: under the banner when one blocks, shown for inspection alone. */
export function Inspection({ caveats, children }: { readonly caveats: readonly BoxedCaveat[]; readonly children: ReactNode }): ReactElement {
  return blocks(caveats) ? (
    <section className="inspection" aria-label="for inspection, not an answer">
      <p role="alert" className="banner">
        {FOR_INSPECTION}
      </p>
      <Caveats caveats={caveats} />
      {children}
    </section>
  ) : (
    <section className="output">
      <Caveats caveats={caveats} />
      {children}
    </section>
  );
}
