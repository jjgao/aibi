/**
 * A result's charts (D435; D427's R31a; §8.5, A3): until charts are drawn (R30, R31b: M5.C), fixed
 * words alone, chosen by whether the result's `charts` list is empty: a result with none says so, and
 * one that carries charts says none is drawn here, so that a chart not drawn never reads as a chart
 * with no data. No member of a chart is read, and no number is shown.
 */
import type { ReactElement } from "react";

/** The fixed words of the charts. */
export const CHART_WORDS = {
  none: "This result has no chart.",
  some: "This result carries charts; none is drawn on this page (charts come with M5.C). A chart not drawn is not a chart with no data.",
} as const;

/** The charts' words, from the list's emptiness alone. */
export function Charts({ charts }: { readonly charts: readonly unknown[] }): ReactElement {
  return <p className="note charts">{charts.length === 0 ? CHART_WORDS.none : CHART_WORDS.some}</p>;
}
