/**
 * The declared table of what the result screen does not show (D435, B7.2): each entry a pattern over
 * a leaf's path, its owner and its row, and why. A leaf whose change the walk refuses (a vocabulary the
 * envelope's schema checks) or the values model's walk refuses (the result then reads as of another
 * shape) is not swept here: the sweep pins those paths apart. The sweep (`result-sweep.test.tsx`) computes, over
 * every body, the paths whose leaf the page does not show, and holds that set **equal** to the paths
 * this table matches: a member deferred without an entry, an entry matching a shown path, and an
 * entry that matches no path of any body each fail. The numbers oracle subtracts the same table.
 *
 * **Paths.** A leaf's path is its raw position, normalised: a list's index is `[]`, a map's key
 * `{key}` (the key itself is the leaf) and a map's member its name when it is spelled as a member name
 * (`^[a-z][a-z_]*$`: a result's `values`, documents and charts are maps of the walk's, `BoxedJson`),
 * `{}` otherwise (a data key); it ends in the leaf's kind (`@text`, `@number`).
 * Four segments carry what the test's own code reads of the body (never the page's): a result's
 * `values` is `values(unread)` when its analysis and version are not `summary.distribution@1.0.0` or
 * its labels and columns disagree in count; a column of a position is `[numbers]`, `[categories]`,
 * `[other]` or `[numbers deferred]` (a numbers column of a result under a disclosure setting, or with
 * any of `q1_bin`, `median_bin`, `q3_bin` present: B2's condition, restated here); a category row is
 * `[unnamed]` when it names no value and has no `other_values`; a label of a result the page reads no
 * values of is `column(unread)`; a result's `issuance` is
 * `issuance(hit)` or `issuance(computed)` by its `cache_hit`, and its `population` is
 * `population(k)` under a disclosure setting.
 */

/** An entry: the paths it matches, the owner and the row that will show them, and why. */
export interface Deferred {
  readonly path: RegExp;
  readonly owner: "M5.4-min" | "M5.4-rest" | "M5.C" | "M5.3-min";
  readonly row: string;
  readonly why: string;
}

/** The label path: a column of the canonical document's view (read by the seam, shown as labels).
 * The keys that lead to it are read by name (a key changed reads as another shape). */
const LABEL = String.raw`results/\[\]/derivation/document/view/params/columns/\[\]/column@text`;

/** A column of a position of a read result. */
const COLUMN = String.raw`^results/\[\]/values/positions/\[\]/columns`;

export const DEFERRED: readonly Deferred[] = [
  {
    path: /^results\/\[\]\/derivation\/document\/view\/params\/columns\/\[\]\/column\(unread\)@text$/u,
    owner: "M5.4-min",
    row: "R34a",
    why: "A label of a result whose values the page does not read (another analysis, or another shape): the unread words stand for its values.",
  },
  {
    path: new RegExp(String.raw`^(?!${LABEL}$)results/\[\]/derivation/document/`, "u"),
    owner: "M5.4-rest",
    row: "R26b",
    why: "The canonical document, but its view's column labels (shown, through the seam's read): R26a shows the derivation's ids; the document is R26b's.",
  },
  { path: /^results\/\[\]\/source\/document\//u, owner: "M5.4-rest", row: "R26b", why: "The document as written: fixed words say it is not shown yet." },
  { path: /^results\/\[\]\/source\/params\//u, owner: "M5.4-rest", row: "R28b", why: "The parameters as written: fixed words say they are not shown yet." },
  { path: /^params\/used\//u, owner: "M5.4-rest", row: "R28b", why: "The answer's used parameters: fixed words (none, or not shown yet)." },
  { path: /^results\/\[\]\/charts\//u, owner: "M5.C", row: "R30, R31b", why: "A chart's members: words alone say whether the result carries charts (R31a)." },
  { path: /^results\/\[\]\/population\(k\)\/\[\]\/lift_differs@number$/u, owner: "M5.3-min", row: "R20", why: "lift_differs under any k is shown as suppressed, whatever value came (§8.4)." },
  { path: /^results\/\[\]\/values\(unread\)\//u, owner: "M5.4-min", row: "R34a", why: "The values of an analysis or version the page does not read, or of another shape: fixed words say so." },
  { path: new RegExp(String.raw`${COLUMN}/\[numbers deferred\]/`, "u"), owner: "M5.4-rest", row: "R34b", why: "A number column under the disclosure setting or with a bin index: fixed words alone." },
  {
    path: new RegExp(String.raw`${COLUMN}/\[other\]/(?!kind@text$)`, "u"),
    owner: "M5.4-rest",
    row: "R29",
    why: "The members of another kind of column (category_rows, number_rows, memberships): its kind alone is shown, in fixed words.",
  },
  {
    path: new RegExp(String.raw`${COLUMN}/\[categories\]/categories/\[unnamed\]/`, "u"),
    owner: "M5.4-min",
    row: "R34a",
    why: "A row that names no category and is no other_values row: fail-closed words, nothing of its proportion.",
  },
  { path: /\/proportion\/ci\//u, owner: "M5.4-rest", row: "R16", why: "A proportion's interval: fixed words that it has one." },
  { path: /^results\/\[\]\/caveats\/\[\]\/severity@text$/u, owner: "M5.4-min", row: "R21", why: "A caveat's severity: shown in the page's words (an unknown one as block)." },
  { path: /^results\/\[\]\/issuance\(computed\)\/values_from@text$/u, owner: "M5.4-min", row: "R28a", why: "A computed result's values_from (its own issuance): the page says it was computed for this request." },
];

/** The entry that matches a path, if any. */
export function deferredAt(path: string): Deferred | undefined {
  return DEFERRED.find((entry) => entry.path.test(path));
}

/** The paths whose change the envelope's walk refuses (a vocabulary or a pattern the schema checks),
 * each with the cell that holds what the page shows of it (`file::title`). */
export const REFUSED: Readonly<Record<string, string>> = {
  "results/[]/analysed/[]/excluded/{key}@text": "tests/unit/analysed.test.tsx::R19:",
  "results/[]/analysed/[]/not_estimable/{key}@text": "tests/unit/analysed.test.tsx::R19:",
  "results/[]/analysed/[]/not_estimable/{}@text": "tests/unit/analysed.test.tsx::R19:",
  "results/[]/analysed/[]/variables/[]/excluded/{key}@text": "tests/unit/analysed.test.tsx::R19:",
  "results/[]/analysed/[]/variables/[]/not_estimable/{key}@text": "tests/unit/analysed.test.tsx::R19:",
  "results/[]/analysed/[]/variables/[]/not_estimable/{}@text": "tests/unit/analysed.test.tsx::R19:",
  "results/[]/derivation/releases/[]/label@text": "tests/unit/release-ref.test.tsx::R27:",
  "results/[]/derivation/releases/[]/status@text": "tests/unit/release-ref.test.tsx::R27:",
  "results/[]/population(k)/[]/suppressed/[]@text": "tests/unit/population.test.tsx::R20:",
  "results/[]/population(k)/[]/unknown_by_leaf/{key}@text": "tests/unit/population.test.tsx::R20:",
  "results/[]/population(k)/[]/unknown_by_reason/{key}@text": "tests/unit/population.test.tsx::R18:",
  "results/[]/population/[]/unknown_by_leaf/{key}@text": "tests/unit/population.test.tsx::R20:",
  "results/[]/population/[]/unknown_by_reason/{key}@text": "tests/unit/population.test.tsx::R18:",
  "results/[]/source/leaves/{key}@text": "tests/unit/provenance-panel.test.tsx::R26a:",
};

/** The paths whose change the values model's walk refuses (the result then reads as another shape),
 * each with the cell that holds what the page shows of it. */
export const UNREAD: Readonly<Record<string, string>> = {
  "results/[]/derivation/document/view/params/columns/[]/{key}@text": "tests/unit/values.test.tsx::the labels are the canonical document's",
  "results/[]/derivation/document/view/params/{key}@text": "tests/unit/values.test.tsx::the labels are the canonical document's",
  "results/[]/derivation/document/view/{key}@text": "tests/unit/values.test.tsx::the labels are the canonical document's",
  "results/[]/derivation/document/{key}@text": "tests/unit/values.test.tsx::the labels are the canonical document's",
  "results/[]/values/positions/[]/columns/[categories]/categories/[]/proportion/ci/{key}@text": "tests/unit/proportion.test.tsx::R13:",
  "results/[]/values/positions/[]/columns/[categories]/categories/[]/proportion/denominator_definition/counts@text": "tests/unit/proportion.test.tsx::R12:",
  "results/[]/values/positions/[]/columns/[categories]/categories/[]/proportion/denominator_definition/{key}@text": "tests/unit/values.test.tsx::a member renamed",
  "results/[]/values/positions/[]/columns/[categories]/categories/[]/proportion/denominator_text/[]/{key}@text": "tests/unit/proportion.test.tsx::R12:",
  "results/[]/values/positions/[]/columns/[categories]/categories/[]/proportion/excluded/{key}@text": "tests/unit/proportion.test.tsx::R13:",
  "results/[]/values/positions/[]/columns/[categories]/categories/[]/proportion/not_estimable/{key}@text": "tests/unit/result-values.test.tsx::R34a:",
  "results/[]/values/positions/[]/columns/[categories]/categories/[]/proportion/not_estimable/{}@text": "tests/unit/result-values.test.tsx::R34a:",
  "results/[]/values/positions/[]/columns/[categories]/categories/[]/proportion/{key}@text": "tests/unit/values.test.tsx::a member renamed",
  "results/[]/values/positions/[]/columns/[categories]/categories/[]/values/[]/{key}@text": "tests/unit/values.test.tsx::a member renamed",
  "results/[]/values/positions/[]/columns/[categories]/categories/[]/{key}@text": "tests/unit/values.test.tsx::a member renamed",
  "results/[]/values/positions/[]/columns/[categories]/categories/[unnamed]/proportion/denominator_definition/counts@text": "tests/unit/result-values.test.tsx::a row that names no category",
  "results/[]/values/positions/[]/columns/[categories]/categories/[unnamed]/proportion/denominator_definition/{key}@text": "tests/unit/result-values.test.tsx::a row that names no category",
  "results/[]/values/positions/[]/columns/[categories]/categories/[unnamed]/proportion/not_estimable/{key}@text": "tests/unit/result-values.test.tsx::a row that names no category",
  "results/[]/values/positions/[]/columns/[categories]/categories/[unnamed]/proportion/not_estimable/{}@text": "tests/unit/result-values.test.tsx::a row that names no category",
  "results/[]/values/positions/[]/columns/[categories]/categories/[unnamed]/proportion/{key}@text": "tests/unit/result-values.test.tsx::a row that names no category",
  "results/[]/values/positions/[]/columns/[categories]/categories/[unnamed]/{key}@text": "tests/unit/result-values.test.tsx::a row that names no category",
  "results/[]/values/positions/[]/columns/[categories]/kind@text": "tests/unit/values.test.tsx::a kind changed",
  "results/[]/values/positions/[]/columns/[categories]/not_estimable/{key}@text": "tests/unit/result-values.test.tsx::R34a:",
  "results/[]/values/positions/[]/columns/[categories]/not_estimable/{}@text": "tests/unit/result-values.test.tsx::R34a:",
  "results/[]/values/positions/[]/columns/[categories]/{key}@text": "tests/unit/values.test.tsx::a member renamed",
  "results/[]/values/positions/[]/columns/[numbers deferred]/histogram/bins/[]/{key}@text": "tests/unit/values.test.tsx::a member renamed",
  "results/[]/values/positions/[]/columns/[numbers deferred]/histogram/edges_from@text": "tests/unit/result-values.test.tsx::R34a:",
  "results/[]/values/positions/[]/columns/[numbers deferred]/histogram/{key}@text": "tests/unit/values.test.tsx::a member renamed",
  "results/[]/values/positions/[]/columns/[numbers deferred]/kind@text": "tests/unit/values.test.tsx::a kind changed",
  "results/[]/values/positions/[]/columns/[numbers deferred]/not_estimable/{key}@text": "tests/unit/result-values.test.tsx::R34a:",
  "results/[]/values/positions/[]/columns/[numbers deferred]/not_estimable/{}@text": "tests/unit/result-values.test.tsx::R34a:",
  "results/[]/values/positions/[]/columns/[numbers deferred]/{key}@text": "tests/unit/values.test.tsx::a member renamed",
  "results/[]/values/positions/[]/columns/[numbers]/histogram/bins/[]/{key}@text": "tests/unit/values.test.tsx::a member renamed",
  "results/[]/values/positions/[]/columns/[numbers]/histogram/edges_from@text": "tests/unit/result-values.test.tsx::R34a:",
  "results/[]/values/positions/[]/columns/[numbers]/histogram/{key}@text": "tests/unit/values.test.tsx::a member renamed",
  "results/[]/values/positions/[]/columns/[numbers]/kind@text": "tests/unit/values.test.tsx::a kind changed",
  "results/[]/values/positions/[]/columns/[numbers]/not_estimable/{key}@text": "tests/unit/result-values.test.tsx::R34a:",
  "results/[]/values/positions/[]/columns/[numbers]/not_estimable/{}@text": "tests/unit/result-values.test.tsx::R34a:",
  "results/[]/values/positions/[]/columns/[numbers]/{key}@text": "tests/unit/values.test.tsx::a member renamed",
  "results/[]/values/positions/[]/columns/[other]/categories/[]/proportion/ci/{key}@text": "tests/unit/proportion.test.tsx::R13:",
  "results/[]/values/positions/[]/columns/[other]/categories/[]/proportion/denominator_definition/counts@text": "tests/unit/proportion.test.tsx::R12:",
  "results/[]/values/positions/[]/columns/[other]/categories/[]/proportion/denominator_definition/{key}@text": "tests/unit/values.test.tsx::a member renamed",
  "results/[]/values/positions/[]/columns/[other]/categories/[]/proportion/denominator_text/[]/{key}@text": "tests/unit/proportion.test.tsx::R12:",
  "results/[]/values/positions/[]/columns/[other]/categories/[]/proportion/excluded/{key}@text": "tests/unit/proportion.test.tsx::R13:",
  "results/[]/values/positions/[]/columns/[other]/categories/[]/proportion/not_estimable/{key}@text": "tests/unit/result-values.test.tsx::R34a:",
  "results/[]/values/positions/[]/columns/[other]/categories/[]/proportion/not_estimable/{}@text": "tests/unit/result-values.test.tsx::R34a:",
  "results/[]/values/positions/[]/columns/[other]/categories/[]/proportion/{key}@text": "tests/unit/values.test.tsx::a member renamed",
  "results/[]/values/positions/[]/columns/[other]/categories/[]/values/[]/{key}@text": "tests/unit/values.test.tsx::a member renamed",
  "results/[]/values/positions/[]/columns/[other]/categories/[]/{key}@text": "tests/unit/values.test.tsx::a member renamed",
  "results/[]/values/positions/[]/columns/[other]/excluded_rows/{key}@text": "tests/unit/values.test.tsx::a member renamed",
  "results/[]/values/positions/[]/columns/[other]/histogram/bins/[]/{key}@text": "tests/unit/values.test.tsx::a member renamed",
  "results/[]/values/positions/[]/columns/[other]/histogram/edges_from@text": "tests/unit/result-values.test.tsx::R34a:",
  "results/[]/values/positions/[]/columns/[other]/histogram/{key}@text": "tests/unit/values.test.tsx::a member renamed",
  "results/[]/values/positions/[]/columns/[other]/kind@text": "tests/unit/result-values.test.tsx::R34a:",
  "results/[]/values/positions/[]/columns/[other]/not_estimable/{key}@text": "tests/unit/result-values.test.tsx::R34a:",
  "results/[]/values/positions/[]/columns/[other]/not_estimable/{}@text": "tests/unit/result-values.test.tsx::R34a:",
  "results/[]/values/positions/[]/columns/[other]/{key}@text": "tests/unit/values.test.tsx::a member renamed",
  "results/[]/values/positions/[]/{key}@text": "tests/unit/values.test.tsx::a member renamed",
};
