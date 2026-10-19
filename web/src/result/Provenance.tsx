/**
 * The provenance panel (D435; D427's R26a and R28a; §8.1, P1): a result's derivation (its id, its
 * analysis and version, its releases through `ReleaseRef`, its packs' versions, its semantics version
 * and its engine), its digest, its issuance (its id, and one sentence: computed for this request, or
 * served from the result cache with the issuance its values come from) and its `source.leaves` (each
 * pointer and its leaf ids, as data); fixed words for the document as written (R26b) and the
 * parameters as written (R28b), which are not shown yet; and the fixed words of where the result
 * lives (in place, until a reload). `Parameters` shows an answer's parameters, once per answer (the
 * builder renders it): the unused ones, each as data, and fixed words for the used ones (R28b).
 * Every id is whole, through `Ref`.
 */
import { Fragment, type ReactElement } from "react";

import { type Answered, entriesOf, Num } from "../api";
import { keyOf } from "../layout/key";
import { ReleaseRef } from "../layout/ReleaseRef";
import { Ref, Text } from "../layout/Text";

type BoxedResult = Answered<"run_analysis">["results"][number];

/** The fixed words of the panel. */
export const PROVENANCE_WORDS = {
  computed: "computed for this request.",
  cached: "served from the result cache: its values come from issuance",
  document: "The document as written is not shown on this page yet (R26b).",
  params: "The parameters as written are not shown on this page yet (R28b).",
  none: "none",
  used: "not shown here yet (R28b).",
  explain: "The derivation id names this result; an agent reads its derivation with explain.",
  kept: "This page keeps the result while it stays open: a reload clears it (the result is not fetched again). If the form changes, the result stays under the words that say so, until the next run.",
} as const;

/** A result's provenance (the module's docstring). */
export function Provenance({ result }: { readonly result: BoxedResult }): ReactElement {
  const derivation = result.derivation;
  const issuance = result.issuance;
  const packs = entriesOf(derivation.packs);
  const leaves = entriesOf(result.source.leaves);
  return (
    <section className="provenance" aria-label="provenance">
      <p>
        Derivation <Ref value={derivation.id} />: analysis <Ref value={derivation.analysis.id} /> version <Ref value={derivation.analysis.version} />
      </p>
      <p className="meta">
        Releases:{" "}
        {derivation.releases.map((release, index) => (
          <Fragment key={keyOf(index)}>
            {index === 0 ? null : ", "}
            <ReleaseRef value={release} />
          </Fragment>
        ))}
      </p>
      <p className="meta">
        Packs:{" "}
        {packs.length === 0
          ? PROVENANCE_WORDS.none
          : packs.map(([name, pack], index) => (
              <Fragment key={keyOf(index)}>
                {index === 0 ? null : ", "}
                <Ref value={name} /> version <Ref value={pack.version} /> results_version <Num value={pack.results_version} />
              </Fragment>
            ))}
      </p>
      <p className="meta">
        Semantics version <Num value={derivation.semantics_version} />; engine <Ref value={derivation.engine} />
      </p>
      <p className="meta">
        Digest <Ref value={result.digest} />
      </p>
      <p className="meta">
        Issuance <Ref value={issuance.id} />:{" "}
        {issuance.cache_hit ? (
          <>
            {PROVENANCE_WORDS.cached} <Ref value={issuance.values_from} />.
          </>
        ) : (
          PROVENANCE_WORDS.computed
        )}
      </p>
      <p className="meta">
        Leaves:{" "}
        {leaves.length === 0
          ? PROVENANCE_WORDS.none
          : leaves.map(([pointer, ids], index) => (
              <Fragment key={keyOf(index)}>
                {index === 0 ? null : "; "}
                <Ref value={pointer} />{" "}
                {ids.map((id, at) => (
                  <Fragment key={keyOf(at)}>
                    {at === 0 ? null : ", "}
                    <Ref value={id} />
                  </Fragment>
                ))}
              </Fragment>
            ))}
      </p>
      <p className="note">{PROVENANCE_WORDS.document}</p>
      <p className="note">{PROVENANCE_WORDS.params}</p>
      <p className="note">{PROVENANCE_WORDS.explain}</p>
      <p className="note">{PROVENANCE_WORDS.kept}</p>
    </section>
  );
}

/** An answer's parameters (the module's docstring), shown once per answer. */
export function Parameters({ params }: { readonly params: Answered<"run_analysis">["params"] }): ReactElement {
  return (
    <section className="parameters" aria-label="parameters">
      <p>
        Parameters used: {entriesOf(params.used).length === 0 ? PROVENANCE_WORDS.none : PROVENANCE_WORDS.used}
      </p>
      <p>
        Parameters not used:{" "}
        {params.unused.length === 0
          ? PROVENANCE_WORDS.none
          : params.unused.map((one, index) => (
              <Fragment key={keyOf(index)}>
                {index === 0 ? null : ", "}
                <Text value={one.data} />
              </Fragment>
            ))}
      </p>
    </section>
  );
}
