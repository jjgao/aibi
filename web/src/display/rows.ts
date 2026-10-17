/**
 * The display table (D427): every obligation the spec puts on what the web shows, one row each,
 * frozen and reviewed. SPEC.md's Appendix C is generated from this file (`scripts/display-table.mjs`,
 * `npm run generate`; `generate:check` fails while the two differ), so the table has one source.
 *
 * **The table is data alone.** It imports nothing (the reader evaluates it in a context with no
 * `require` and no `import`) and no entry's graph reaches it. `scripts/display-ids.json`, a file of
 * its own, lists every id the table ever held: a row that leaves the table, or an id added, is an
 * edit of that file too, and the generator fails until the two agree (a row leaves only into
 * `RETIRED`, with a reason, and its id stays listed there).
 *
 * **A row** names what must be shown (`must`), the `basis` of the duty (`spec MUST`: a MUST or
 * MUST NOT of the spec states this duty of what is shown; `spec statement`: the cited section or
 * decision states it, in no MUST of its own; `derived`: the spec states it for another reader or
 * implies it, or D427 decides it, the derivation said in `must`; the spec has no SHOULD about
 * display), the screens it binds, the spec it cites, the pull request that owns it (`owner`), the
 * module that holds it (`module`, relative to `web/`) and the test file that holds its test
 * (`test`). A row is **built** when its module exists; from then on the gate requires that the
 * module has a value export statement of its own and a passed test titled `<id>: …`, a literal `it`/`test` call in `test`,
 * which imports `module`. A module named here and not yet written is the owner's plan, and the
 * owner's pull request may move it (an edit of this table, reviewed like any other).
 *
 * **Owners are pull requests, in landing order** (`OWNERS`): a built row of an owner fails the gate
 * while any owner before it has a row not built, so 2b-1L's lint lands before M5.2 renders data,
 * and 2b-1L2's (R90's flow, R90f) after M5.4 and before M5.5; M5.2-rest's (the descriptors' members
 * and the paging controls, D431) after M5.5 and before M5.6.
 * 2b-0, which writes this table and its gate, builds no screen and owns no row. `server` rows are
 * the server's (or a gate's that has landed): they name no module and never fall due here.
 *
 * **Which modules ship** is the build's gate's to say, from the bundler's own module graph
 * (`plugins/gate.ts` (f)): every module of a production or end-to-end bundle under `src/`, outside
 * `src/api/` (the seam, whose every export `tests/gates/api-graph.test.ts` pins), is a row's
 * module, an exemption or a helper, or the build fails. The two lists below are the only places a
 * module that is no row's is named, and they are **reviewed, not checked**: nothing reads what an
 * exemption or a helper does, imports or renders, and the reason is the reviewer's to doubt. A
 * pull request that adds or widens one changes this file and Appendix C, which is where review
 * sees it; a module that shows data is a row's.
 *
 * **Not checked, by design** (D427's residuals): that a lint rule of 2b-1L is wired into
 * `eslint.config.mjs` and refuses what it should (the gate sees its module and its tests alone; 2b-1L's
 * own tests are the canary: each row's corpus linted through `eslint.config.mjs` with
 * `new ESLint({ cwd })`, `tests/unit/lint-display.test.ts`, D429); that a built row's module is
 * reached by an entry; what a module does with a name it imports.
 */

/** The owners, in the order they land; `server` last, outside the order. */
export const OWNERS = ["2b-1a", "2b-1L", "2b-1b", "M5.2-min", "M5.3-min", "M5.4-min", "2b-1L2", "M5.5", "M5.2-rest", "M5.6", "server"] as const;

/** The screens a row may bind. */
export const SCREENS = ["all", "catalogue", "dataset", "column", "builder", "result", "refusal", "operator"] as const;

/** Why a row is a duty: see the header. */
export const BASES = ["spec MUST", "spec statement", "derived"] as const;

export type Owner = (typeof OWNERS)[number];
export type Screen = (typeof SCREENS)[number];
export type Basis = (typeof BASES)[number];

export type Row = {
  readonly id: string;
  readonly basis: Basis;
  readonly must: string;
  readonly screens: readonly Screen[];
  readonly spec: readonly string[];
  readonly owner: Owner;
  readonly module: string;
  readonly test: string;
};

/** A module of a bundle that is no row's: reviewed, not checked (see the header). */
export type Exemption = { readonly module: string; readonly reason: string };

/** A module of a bundle that is no row's and no screen: reviewed, not checked (see the header). */
export type Helper = { readonly module: string; readonly reason: string };

/** A row that left the table, with its reason (its id is never used again). */
export type Retired = { readonly id: string; readonly reason: string };

/** What the server alone holds: no row, listed so that it is not forgotten. */
export type NotRow = { readonly what: string; readonly spec: readonly string[] };

export const ROWS: readonly Row[] = [
  // A. Every screen.
  {
    id: "R01",
    basis: "spec statement",
    must: "Text from data is plain text, never markup: its control and format characters (bidi among them), lone surrogates and the line and paragraph separators are shown as visible escapes, one escape span per run, a carriage return before a line feed dropped; each text is isolated in a `<bdi>`, cut at 2,000 code points before escaping and marked cut; pointers, `stat:` references and ids are never cut.",
    screens: ["all"],
    spec: ["A6", "§14", "D312", "D426"],
    owner: "2b-1a",
    module: "src/layout/Text.tsx",
    test: "tests/unit/layout.test.tsx",
  },
  {
    id: "R02",
    basis: "spec statement",
    must: "Server words (`text` segments) and data tokens stay distinct: a data token is isolated and marked as data, also where it spells server words, a token the server cut (`truncated`) shows its cut mark, and segments are shown in their order, never rewritten.",
    screens: ["result", "builder", "refusal", "catalogue", "dataset"],
    spec: ["§8.1", "§7.7", "D399"],
    owner: "2b-1a",
    module: "src/layout/Segments.tsx",
    test: "tests/unit/layout.test.tsx",
  },
  {
    id: "R03",
    basis: "spec statement",
    must: "A list from data shows at most 64 members, then how many more: the page counts the rest only for D312's lists (domain tags, facet values), never for a list a count of §8.4 governs, and a cut the server made (`tables_left_out`) is shown as the server's number.",
    screens: ["catalogue", "dataset"],
    spec: ["D311", "D312", "§7.7", "§8.4"],
    owner: "2b-1b",
    module: "src/layout/List.tsx",
    test: "tests/unit/list.test.tsx",
  },
  {
    id: "R04",
    basis: "spec statement",
    must: "Every number shown is a server number shown by its exact source text (`1e-07`, `-0.0`, `1.10`); the page never computes, sums, differences or rounds a shown number, compares one only through the sanctioned predicates (Q4), and never rebuilds a count §8.4 withheld.",
    screens: ["all"],
    spec: ["A1", "§8.4", "D419", "D426"],
    owner: "2b-1b",
    module: "src/api/format.tsx",
    test: "tests/unit/format.test.tsx",
  },
  {
    id: "R05",
    basis: "spec statement",
    must: "No remote resource and no markup or script sink: the server writes both documents and their policy (images and connections to the server only, no inline code, Trusted Types with no policy), and the build's gate keeps each entry's graph to the package's own modules.",
    screens: ["all"],
    spec: ["§14", "D411", "D418"],
    owner: "server",
    module: "",
    test: "",
  },
  {
    id: "R06",
    basis: "spec statement",
    must: "A number with no value is never shown as 0 or blank: a null is shown as suppressed or as not estimable with its reason (R14, R20).",
    screens: ["all"],
    spec: ["P2", "§8.2", "§8.4"],
    owner: "2b-1b",
    module: "src/layout/Figure.tsx",
    test: "tests/unit/figure.test.tsx",
  },
  {
    id: "R07",
    basis: "derived",
    must: "An option's label shows the visible form's escapes as literal text (an `<option>` holds text alone), with no raw unsafe character, and its value is the option's index, never a string from data (derived: A6 and D312 bind every position text from data reaches).",
    screens: ["builder"],
    spec: ["A6", "D312"],
    owner: "2b-1a",
    module: "src/layout/Option.tsx",
    test: "tests/unit/layout.test.tsx",
  },
  {
    id: "R08",
    basis: "spec statement",
    must: "A dataset id from the address (its path or hash, a link from another site) is checked by the dataset grammar before any use, never decoded before the check and never echoed: a refused id gives fixed words.",
    screens: ["catalogue", "dataset"],
    spec: ["D311", "D314", "A6", "§14"],
    owner: "2b-1a",
    module: "src/router/address.tsx",
    test: "tests/unit/address-row.test.tsx",
  },
  {
    id: "R09",
    basis: "spec statement",
    must: "A shareable URL reads and writes the same Analysis document: a document read from the address is untrusted input, read through the router module's own door and checked by `validate_document` before anything is shown or run, and shown only through the visible form.",
    screens: ["builder", "result"],
    spec: ["A2", "A6", "D314", "§14"],
    owner: "M5.3-min",
    module: "src/router/document.tsx",
    test: "tests/unit/document-address.test.tsx",
  },
  // B. The result screen (`run_analysis`, one envelope per view).
  {
    id: "R10",
    basis: "spec statement",
    must: "The readback is shown next to every figure: each position's beside that position's figures and the view's beside the view's (Q3), so that a person confirms the document by reading it.",
    screens: ["result"],
    spec: ["A2", "§7.7", "D49"],
    owner: "M5.4-min",
    module: "src/result/Figures.tsx",
    test: "tests/unit/result-figures.test.tsx",
  },
  {
    id: "R11",
    basis: "spec statement",
    must: "Readbacks and messages are rendered from their segments in order, never rewritten, paraphrased or written by a model; pack summaries follow the readback, labelled as the pack's.",
    screens: ["result", "builder"],
    spec: ["§7.3", "§7.7", "A2", "D296"],
    owner: "M5.3-min",
    module: "src/layout/Readback.tsx",
    test: "tests/unit/readback.test.tsx",
  },
  {
    id: "R12",
    basis: "derived",
    must: "Every proportion is shown as its numerator, its denominator and what the denominator counts (`denominator_text`), never as a bare estimate (derived: §8.2 and P1 give the server's proportion those three members and say none is a bare number, and the page shows what the envelope says).",
    screens: ["result", "builder"],
    spec: ["P1", "A1", "§8.2"],
    owner: "M5.4-min",
    module: "src/layout/Proportion.tsx",
    test: "tests/unit/proportion.test.tsx",
  },
  {
    id: "R13",
    basis: "derived",
    must: "A proportion's `excluded` is shown by reason, every reason and its zeros included, a suppressed one as suppressed: no unit is dropped uncounted (derived: §8.2's `excluded` carries every reason, P2 and §6.6 say no unit is dropped uncounted, and the page shows what the envelope says).",
    screens: ["result"],
    spec: ["P2", "§6.6", "§8.2"],
    owner: "M5.4-min",
    module: "src/layout/Proportion.tsx",
    test: "tests/unit/proportion.test.tsx",
  },
  {
    id: "R14",
    basis: "spec statement",
    must: "A null number says why: a null count is suppressed (a count is null only when suppressed, `StatCount`'s count included) and is shown as suppressed; any other null shows its reason from the enclosing `not_estimable` (not estimable, with the reason's words, or suppressed); a null other than a count with no reason there fails closed as not shown, never as suppressed by default.",
    screens: ["result", "builder", "catalogue", "dataset"],
    spec: ["§8.2", "§8.4", "D311", "D426"],
    owner: "2b-1b",
    module: "src/layout/Figure.tsx",
    test: "tests/unit/figure.test.tsx",
  },
  {
    id: "R15",
    basis: "spec statement",
    must: "An effect size names its measure, its position and its reference position, and reads as the server defines it: a difference is the position minus the reference, a ratio the position over the reference.",
    screens: ["result"],
    spec: ["§8.2", "D355"],
    owner: "M5.4-min",
    module: "src/result/Effects.tsx",
    test: "tests/unit/effects.test.tsx",
  },
  {
    id: "R16",
    basis: "derived",
    must: "An interval shows its level and its method with its bounds (Q7; derived: §8.2 gives every interval both, and a bare pair of bounds says neither).",
    screens: ["result"],
    spec: ["§8.2"],
    owner: "M5.4-min",
    module: "src/layout/Interval.tsx",
    test: "tests/unit/interval.test.tsx",
  },
  {
    id: "R17",
    basis: "derived",
    must: "A test shows the positions it used, and a multiple-testing family how many tests it holds and how many were not computed or suppressed (derived: §9.5 says tests that cannot be computed leave the family and are counted, and §8.4 suppresses some; showing those counts is the page's part).",
    screens: ["result"],
    spec: ["§9.5", "§8.4"],
    owner: "M5.4-min",
    module: "src/result/Tests.tsx",
    test: "tests/unit/result-tests.test.tsx",
  },
  {
    id: "R18",
    basis: "spec MUST",
    must: "When `n_unknown` is above zero (the sanctioned `isPositive`, Q4) it is shown next to the cohort size with its largest reason (`largestOf`, ties in the vocabulary's order); a suppressed `n_unknown` or a null map shows no reason and says suppressed.",
    screens: ["result", "builder"],
    spec: ["§6.6", "§8.4", "§15"],
    owner: "M5.4-min",
    module: "src/layout/Population.tsx",
    test: "tests/unit/population.test.tsx",
  },
  {
    id: "R19",
    basis: "derived",
    must: "Per position, the units analysed and those excluded per reason (and per variable, in parameter order) are shown, a null count as suppressed (derived: §6.6 and §8.1 say every result states them, and the page shows what the envelope says).",
    screens: ["result"],
    spec: ["§6.6", "§8.1"],
    owner: "M5.4-min",
    module: "src/result/Analysed.tsx",
    test: "tests/unit/analysed.test.tsx",
  },
  {
    id: "R20",
    basis: "spec statement",
    must: "Population counts (`n_true`, `n_false`, `n_unknown`, by reason, by leaf, `lift_differs`) are each shown as a value or as suppressed by the entry's `suppressed` pointers; `lift_differs` is suppressed under any *k*, a 0 included, and never shown as 0 under *k*.",
    screens: ["result", "builder"],
    spec: ["§8.4", "D297"],
    owner: "M5.4-min",
    module: "src/layout/Population.tsx",
    test: "tests/unit/population.test.tsx",
  },
  {
    id: "R21",
    basis: "spec statement",
    must: "Every `warn` and `block` caveat is shown, core and pack codes alike, with its code, severity and message (§8.3 says these caveats have to be shown to the user; its MUST binds the tool descriptions, which tell every client so, and A5's caveat on a result that depends on a proposal is visible only if shown).",
    screens: ["result", "builder", "catalogue", "dataset"],
    spec: ["§8.3", "§11.1", "A5"],
    owner: "2b-1b",
    module: "src/layout/Caveat.tsx",
    test: "tests/unit/caveat.test.tsx",
  },
  {
    id: "R22",
    basis: "spec MUST",
    must: "An output with a `block` caveat is shown for inspection, under a banner, never presented as an answer.",
    screens: ["result", "builder"],
    spec: ["§8.3"],
    owner: "2b-1b",
    module: "src/layout/Caveat.tsx",
    test: "tests/unit/caveat.test.tsx",
  },
  {
    id: "R23",
    basis: "derived",
    must: "A severity the page does not know is read as `block` (derived: every code declares its severity, and the page's vocabulary is closed and fails closed, `kindOf(Severity)`).",
    screens: ["result", "builder"],
    spec: ["§8.3", "D426"],
    owner: "2b-1b",
    module: "src/layout/Caveat.tsx",
    test: "tests/unit/caveat.test.tsx",
  },
  {
    id: "R24",
    basis: "derived",
    must: "`info` caveats (`SUPPRESSED`, `NOT_ESTIMABLE`, `LIFT_DIFFERS`, `POOLED_ACROSS_DATASETS`) are shown too, secondary (Q6; derived: §8.3 lists these four info codes, and D427's decision Q6 shows them, secondary).",
    screens: ["result", "builder", "catalogue", "dataset"],
    spec: ["§8.3"],
    owner: "2b-1b",
    module: "src/layout/Caveat.tsx",
    test: "tests/unit/caveat.test.tsx",
  },
  {
    id: "R25",
    basis: "derived",
    must: "The effective disclosure setting (*k*, or off) is stated on every result, cohort count and catalogue answer (derived: the cited text has the server state the setting in what it returns, and the page shows what the envelope says).",
    screens: ["result", "builder", "catalogue", "dataset"],
    spec: ["§8.4", "§8.1"],
    owner: "2b-1b",
    module: "src/layout/Disclosure.tsx",
    test: "tests/unit/disclosure.test.tsx",
  },
  {
    id: "R26",
    basis: "derived",
    must: "The provenance panel shows the derivation with every result and count: the derivation id, the analysis and its version, the releases (dataset, label, manifest, status), pack versions, the semantics version, the engine, the digest, the cohort ids and the document as written (Q9), every id whole and citable (derived: P1 and §8.1 say every result carries these, and the page shows what the envelope says).",
    screens: ["result", "builder"],
    spec: ["P1", "§1", "§8.1", "§15"],
    owner: "M5.4-min",
    module: "src/result/Provenance.tsx",
    test: "tests/unit/provenance-panel.test.tsx",
  },
  {
    id: "R27",
    basis: "spec statement",
    must: "A draft release is shown as `@draft` (its label is `draft` exactly when its status is draft).",
    screens: ["result", "builder", "dataset"],
    spec: ["§8.1", "§8.3", "§12.3"],
    owner: "2b-1b",
    module: "src/layout/ReleaseRef.tsx",
    test: "tests/unit/release-ref.test.tsx",
  },
  {
    id: "R28",
    basis: "derived",
    must: "A cache hit says so and names the issuance whose values it serves; the parameters used are echoed and the unused ones reported (derived: §8.1's issuance carries `cache_hit` and `values_from`, and the page shows what the envelope says).",
    screens: ["result"],
    spec: ["§8.1", "§7.1"],
    owner: "M5.4-min",
    module: "src/result/Provenance.tsx",
    test: "tests/unit/provenance-panel.test.tsx",
  },
  {
    id: "R29",
    basis: "spec statement",
    must: "A variable that counts rows is labelled as counting rows, and memberships carry a fixed-word note that a unit counts in each category it holds wherever the data says `multi_membership: true` (Q8).",
    screens: ["result"],
    spec: ["§9.2", "D382"],
    owner: "M5.4-min",
    module: "src/result/Values.tsx",
    test: "tests/unit/values.test.tsx",
  },
  {
    id: "R30",
    basis: "spec statement",
    must: "A chart draws only its inline `data.values`, with a CSP-safe interpreter and no loader or request; a chart with a URL, a `$schema` or a transform is not drawn.",
    screens: ["result"],
    spec: ["§8.5", "D322", "§14"],
    owner: "M5.4-min",
    module: "src/result/Charts.tsx",
    test: "tests/unit/charts.test.tsx",
  },
  {
    id: "R31",
    basis: "derived",
    must: "A chart the client refuses to draw (R30) is never dropped in silence: the result states how many of its charts the client refused to draw, and a result with no chart says it has none (derived: §8.5's charts are the server's, drawn by a CSP-safe interpreter that refuses what it cannot draw, and A3 has a refusal named, never silent).",
    screens: ["result"],
    spec: ["§8.5", "A3"],
    owner: "M5.4-min",
    module: "src/result/Charts.tsx",
    test: "tests/unit/charts.test.tsx",
  },
  {
    id: "R32",
    basis: "derived",
    must: "Survival: a separated term shows its direction, and the hazard-ratio chart's labels are D369's (derived: D364 and D369 fix the term's direction and the chart's labels, which the page shows as the server wrote them).",
    screens: ["result"],
    spec: ["D364", "D369"],
    owner: "M5.4-min",
    module: "src/result/Survival.tsx",
    test: "tests/unit/survival.test.tsx",
  },
  {
    id: "R33",
    basis: "spec statement",
    must: "Cohort names are data labels (`labels`), and the reference position is marked.",
    screens: ["result"],
    spec: ["§8.1", "§7.6"],
    owner: "M5.4-min",
    module: "src/result/Figures.tsx",
    test: "tests/unit/result-figures.test.tsx",
  },
  {
    id: "R34",
    basis: "spec statement",
    must: "`values` is read by name through fail-closed readers, as the entry's `returns` declares it (Q1): a member the page cannot read is shown as not shown (the page and the server disagree), never guessed.",
    screens: ["result"],
    spec: ["§8.1", "§9.1", "D419", "D426"],
    owner: "M5.4-min",
    module: "src/result/Values.tsx",
    test: "tests/unit/values.test.tsx",
  },
  {
    id: "R35",
    basis: "spec statement",
    must: "An integer beyond ±(2^53 − 1) inside `values` is a decimal string: it is shown as the server's text where a number is expected (the by-name readers accept it), never parsed or computed with.",
    screens: ["result"],
    spec: ["§8.2", "§5.1"],
    owner: "M5.4-min",
    module: "src/result/Values.tsx",
    test: "tests/unit/values.test.tsx",
  },
  {
    id: "R36",
    basis: "spec statement",
    must: "Caveats are shown in the server's order (§8.1 sorts them and drops exact duplicates), never sorted again, merged or dropped by the page.",
    screens: ["result", "builder", "catalogue", "dataset"],
    spec: ["§8.1"],
    owner: "2b-1b",
    module: "src/layout/Caveat.tsx",
    test: "tests/unit/caveat.test.tsx",
  },
  {
    id: "R37",
    basis: "derived",
    must: "A caveat's `affects` pointers attach it to the figures they name (Q6), and a pointer that names no figure shown is shown as data (derived: §8.3's pointers say which parts of the output a caveat concerns).",
    screens: ["result"],
    spec: ["§8.3", "§8.1"],
    owner: "M5.4-min",
    module: "src/result/Figures.tsx",
    test: "tests/unit/result-figures.test.tsx",
  },
  {
    id: "R38",
    basis: "spec statement",
    must: "Pooled values carry a fixed-word note that they are pooled across datasets wherever the server's data says so (Q8).",
    screens: ["result"],
    spec: ["§7.5", "§8.3"],
    owner: "M5.4-min",
    module: "src/result/Values.tsx",
    test: "tests/unit/values.test.tsx",
  },
  // C. The builder (the document, live counts, forms).
  {
    id: "R39",
    basis: "spec statement",
    must: "A translated document's notes (a pack translator's, and the core's at every negation) are shown beside the translation, each at its pointer, server words and data kept apart.",
    screens: ["builder"],
    spec: ["D303", "§10.1"],
    owner: "M5.3-min",
    module: "src/builder/Notes.tsx",
    test: "tests/unit/notes.test.tsx",
  },
  {
    id: "R40",
    basis: "spec MUST",
    must: "Live counts include unknowns: the size as a proportion of the unit table, and `n_unknown` by reason (R18, R20); the `n_unknown` part is §6.6's MUST, as R18's, and the proportion of the unit table is derived from §15's builder.",
    screens: ["builder"],
    spec: ["§15", "§8.1", "§6.6"],
    owner: "M5.4-min",
    module: "src/layout/Population.tsx",
    test: "tests/unit/population.test.tsx",
  },
  {
    id: "R41",
    basis: "spec statement",
    must: "The readback is shown beside each cohort and view, so that a person confirms the document.",
    screens: ["builder"],
    spec: ["A2", "§7.7"],
    owner: "M5.3-min",
    module: "src/builder/Builder.tsx",
    test: "tests/unit/builder.test.tsx",
  },
  {
    id: "R42a",
    basis: "spec statement",
    must: "A refusal shows its code, its message from its segments, its alternatives (what is available) listed and never applied, and its `path` as data, or the document level for a null path.",
    screens: ["refusal", "builder"],
    spec: ["A3", "§8.6"],
    owner: "2b-1b",
    module: "src/layout/Refusal.tsx",
    test: "tests/unit/refusal.test.tsx",
  },
  {
    id: "R42b",
    basis: "spec statement",
    must: "A refusal's `path` anchors it at the place it points to in the document as written, and a null path at the document's top.",
    screens: ["builder"],
    spec: ["A3", "§8.6", "D299"],
    owner: "M5.3-min",
    module: "src/builder/Refusals.tsx",
    test: "tests/unit/builder-refusals.test.tsx",
  },
  {
    id: "R43",
    basis: "spec statement",
    must: "Ids not yet issued are shown as such, and each id status (`issued`, `not_issued`, `unknown`, `withdrawn`, `discarded`, `unknown_release`, `erased`) has words of its own.",
    screens: ["builder"],
    spec: ["§7.6", "§12.2"],
    owner: "M5.3-min",
    module: "src/builder/Ids.tsx",
    test: "tests/unit/ids-status.test.tsx",
  },
  {
    id: "R44",
    basis: "spec statement",
    must: "Parameter forms show JSON Schema's annotations, `format` included, and never apply them; a schema that holds a refused or unknown keyword is refused, never drawn in part.",
    screens: ["builder"],
    spec: ["D422", "A3"],
    owner: "M5.3-min",
    module: "src/builder/Params.tsx",
    test: "tests/unit/params.test.tsx",
  },
  {
    id: "R45",
    basis: "spec statement",
    must: "The analysis menu comes from the registry, each analysis shown available, unavailable (naming what is missing, `min_cell_count` and `allow_row_ids` included) or available with caveats (naming the unconfirmed fields).",
    screens: ["builder"],
    spec: ["P5", "§9.4", "§8.4"],
    owner: "M5.3-min",
    module: "src/builder/Analyses.tsx",
    test: "tests/unit/analyses.test.tsx",
  },
  {
    id: "R46",
    basis: "spec statement",
    must: "Registry, pack and leaf-kind text (labels, definitions, schemas, methods) is data, shown only through the visible form.",
    screens: ["builder"],
    spec: ["A6", "§7.3", "D417", "D421"],
    owner: "M5.3-min",
    module: "src/builder/Analyses.tsx",
    test: "tests/unit/analyses.test.tsx",
  },
  {
    id: "R47",
    basis: "derived",
    must: "A numeric constant's units are shown where the person enters it, and a numeric column whose units are undeclared is flagged (derived: D49 has units always in readbacks, and a constant typed without them is read in the column's).",
    screens: ["builder"],
    spec: ["D49", "§6.4", "§5.4"],
    owner: "M5.3-min",
    module: "src/builder/Constant.tsx",
    test: "tests/unit/constant.test.tsx",
  },
  {
    id: "R48",
    basis: "derived",
    must: "The clause that leaves units unknown is shown: `unknown_by_leaf`'s keys are mapped to the clauses as written (derived: §6.6 gives `unknown_by_leaf` per top-level clause and a map from the leaves as written; showing the clause that leaves units unknown follows).",
    screens: ["builder"],
    spec: ["§6.6", "§8.1"],
    owner: "M5.3-min",
    module: "src/builder/Unknowns.tsx",
    test: "tests/unit/unknowns.test.tsx",
  },
  {
    id: "R49",
    basis: "spec statement",
    must: "Overlap and cross-dataset caveats and descriptive-only results are shown (`COHORTS_OVERLAP`, `CONFOUNDED_WITH_DATASET`, `UNMAPPED_COMPARISON`, pooled values).",
    screens: ["builder", "result"],
    spec: ["§7.4", "§7.5"],
    owner: "M5.3-min",
    module: "src/builder/Builder.tsx",
    test: "tests/unit/builder.test.tsx",
  },
  // D. The catalogue (`search_catalog`).
  {
    id: "R50",
    basis: "spec statement",
    must: "Each hit shows its label, name, description, domain tags, data-use terms, packs, concepts, pack facets and tables (grain, role, rows).",
    screens: ["catalogue"],
    spec: ["§11.1", "D311", "P4"],
    owner: "M5.2-min",
    module: "src/catalogue/Catalogue.tsx",
    test: "tests/unit/catalogue-hits.test.tsx",
  },
  {
    id: "R51",
    basis: "spec statement",
    must: "Every row count carries its `stat:` reference; a suppressed one is shown as suppressed, and a not estimable one with its reasons.",
    screens: ["catalogue", "dataset"],
    spec: ["A1", "§8.1", "D311"],
    owner: "M5.2-min",
    module: "src/layout/StatCount.tsx",
    test: "tests/unit/stat-count.test.tsx",
  },
  {
    id: "R52",
    basis: "spec statement",
    must: "Each hit's release label, status and effective disclosure are shown.",
    screens: ["catalogue"],
    spec: ["§8.4", "D311"],
    owner: "M5.2-min",
    module: "src/catalogue/Catalogue.tsx",
    test: "tests/unit/catalogue-hits.test.tsx",
  },
  {
    id: "R53a",
    basis: "spec statement",
    must: "The hits' caveats (R21), the total and where the next page starts are shown.",
    screens: ["catalogue"],
    spec: ["§8.3", "D311"],
    owner: "M5.2-min",
    module: "src/catalogue/Catalogue.tsx",
    test: "tests/unit/catalogue-hits.test.tsx",
  },
  {
    id: "R53b",
    basis: "spec statement",
    must: "A control pages the catalogue from `next_offset` (ADAPTER_FED), one page an action, never on load.",
    screens: ["catalogue"],
    spec: ["§8.3", "D311"],
    owner: "M5.2-rest",
    module: "src/catalogue/Paging.tsx",
    test: "tests/unit/paging.test.tsx",
  },
  {
    id: "R54",
    basis: "derived",
    must: "Only the latest published release of each dataset is listed, and the page says so (derived: §11.1 has `search_catalog` answer over the latest published release of each dataset, and a page that lists one release says which).",
    screens: ["catalogue"],
    spec: ["§11.1", "D311"],
    owner: "M5.2-min",
    module: "src/catalogue/Catalogue.tsx",
    test: "tests/unit/catalogue-hits.test.tsx",
  },
  {
    id: "R55",
    basis: "spec statement",
    must: "A link to a dataset's screen is `#/datasets/<id>`, composed by `Link` from a `DatasetId` alone, never from text, the address or another origin.",
    screens: ["catalogue"],
    spec: ["D312", "D428", "D429"],
    owner: "M5.2-min",
    module: "src/layout/Link.tsx",
    test: "tests/unit/link.test.tsx",
  },
  // E. The dataset page and its descriptors (`describe_dataset`, `describe_column`).
  {
    id: "R60",
    basis: "spec statement",
    must: "Every descriptor member is shown with its curation status and who set it.",
    screens: ["dataset"],
    spec: ["A5", "§5.1", "D311"],
    owner: "M5.2-rest",
    module: "src/dataset/Descriptor.tsx",
    test: "tests/unit/descriptor.test.tsx",
  },
  {
    id: "R61",
    basis: "derived",
    must: "`proposed`, `imported_default` and `undeclared` are told from `asserted` and `imported` in words, not by colour alone: a proposal is visible until confirmed (derived: A5 says a proposal is visible until confirmed and §5.1 gives the statuses; \"in words, not by colour alone\" is D427's own decision, with no spec text).",
    screens: ["dataset"],
    spec: ["A5", "§5.1"],
    owner: "M5.2-rest",
    module: "src/dataset/Descriptor.tsx",
    test: "tests/unit/descriptor.test.tsx",
  },
  {
    id: "R62",
    basis: "derived",
    must: "A field the engine reads that the descriptor lacks is shown as undeclared, for the fields §5.1 names alone (a numeric column's units, a coverage's `parents`, an endpoint's `entry`), and a relationship without coverage as its coverage undeclared (Q5; derived: an absent member is undeclared, and these are the ones the engine reads).",
    screens: ["dataset"],
    spec: ["§5.1", "§5.6", "§5.8", "D195"],
    owner: "M5.2-rest",
    module: "src/dataset/Descriptor.tsx",
    test: "tests/unit/descriptor.test.tsx",
  },
  {
    id: "R63",
    basis: "spec statement",
    must: "The dataset's standing caveats (unconfirmed fields, proposed coverage) are shown (R21).",
    screens: ["dataset"],
    spec: ["§11.1", "D311"],
    owner: "M5.2-min",
    module: "src/dataset/Dataset.tsx",
    test: "tests/unit/dataset.test.tsx",
  },
  {
    id: "R64",
    basis: "spec statement",
    must: "The dataset page shows the applicable analyses (Q2: §9.4 governs where D311 left them out), worded as R45 words them.",
    screens: ["dataset"],
    spec: ["§9.4", "§15"],
    owner: "M5.2-min",
    module: "src/dataset/Dataset.tsx",
    test: "tests/unit/dataset.test.tsx",
  },
  {
    id: "R65a",
    basis: "spec statement",
    must: "The table graph is shown as a list: the tables but coverage tables, and each relationship with its child and parent tables and its cardinality.",
    screens: ["dataset"],
    spec: ["§6.1", "D311", "§15"],
    owner: "M5.2-min",
    module: "src/dataset/Graph.tsx",
    test: "tests/unit/graph.test.tsx",
  },
  {
    id: "R65b",
    basis: "spec statement",
    must: "Each relationship's child and parent columns are shown (from its descriptor).",
    screens: ["dataset"],
    spec: ["§6.1", "D311", "§15"],
    owner: "M5.2-rest",
    module: "src/dataset/Relationships.tsx",
    test: "tests/unit/relationships.test.tsx",
  },
  {
    id: "R66a",
    basis: "spec statement",
    must: "Each table is shown with its row count and its reference, which names the table; its columns with id, label, datatype and identifier flag.",
    screens: ["dataset"],
    spec: ["§5.3", "§5.4", "§5.6", "§5.8", "D311"],
    owner: "M5.2-min",
    module: "src/dataset/Tables.tsx",
    test: "tests/unit/tables.test.tsx",
  },
  {
    id: "R66b",
    basis: "spec statement",
    must: "Each table's grain and role, and the coverage and endpoint descriptors, are shown.",
    screens: ["dataset"],
    spec: ["§5.3", "§5.4", "§5.6", "§5.8", "D311"],
    owner: "M5.2-rest",
    module: "src/dataset/TableDescriptors.tsx",
    test: "tests/unit/table-descriptors.test.tsx",
  },
  {
    id: "R67",
    basis: "spec statement",
    must: "The column page shows observation-state counts and the distribution, each count with its `stat:` reference, suppressed ones as suppressed, the pooled suppressed-categories row, histogram bins with their open and inclusive ends, no minimum or maximum under *k*, and the reason a distribution is not given.",
    screens: ["column"],
    spec: ["§8.4", "§11.1", "§5.4"],
    owner: "M5.2-rest",
    module: "src/dataset/Column.tsx",
    test: "tests/unit/column.test.tsx",
  },
  {
    id: "R68a",
    basis: "spec statement",
    must: "The release, the effective disclosure, `columns_total` and where the next columns start are shown.",
    screens: ["dataset"],
    spec: ["D311", "D422"],
    owner: "M5.2-min",
    module: "src/dataset/Dataset.tsx",
    test: "tests/unit/dataset.test.tsx",
  },
  {
    id: "R68b",
    basis: "spec statement",
    must: "A control pages the columns from `columns_next`, at most D422's three `describe_dataset` pages a load.",
    screens: ["dataset"],
    spec: ["D311", "D422"],
    owner: "M5.2-rest",
    module: "src/dataset/Paging.tsx",
    test: "tests/unit/dataset-paging.test.tsx",
  },
  // F. Refusals and errors.
  {
    id: "R70",
    basis: "spec statement",
    must: "A refusal names the limit it hit (`limit`: its name and its maximum, shown as server numbers).",
    screens: ["refusal"],
    spec: ["A3", "§8.6", "§14"],
    owner: "2b-1b",
    module: "src/layout/Refusal.tsx",
    test: "tests/unit/refusal.test.tsx",
  },
  {
    id: "R71",
    basis: "spec statement",
    must: "A refusal's `counts` (cohort counts with their ids, such as an overlap's) are shown as cohort counts are (R18, R20, R25, R26).",
    screens: ["refusal", "builder"],
    spec: ["§8.6", "§7.4"],
    owner: "M5.4-min",
    module: "src/layout/Population.tsx",
    test: "tests/unit/population.test.tsx",
  },
  {
    id: "R72a",
    basis: "derived",
    must: "A well-formed `{refusals}` body is shown as refusals at any status of 400 or more, with `Retry-After` stated where it is given (derived: D426's typed answer gives the refusals and `retryAfter` at any status of 400 or more, and A3 has a refusal shown, never silent).",
    screens: ["refusal"],
    spec: ["D265", "D311", "D426"],
    owner: "2b-1b",
    module: "src/layout/Refusal.tsx",
    test: "tests/unit/refusal.test.tsx",
  },
  {
    id: "R72b",
    basis: "derived",
    must: "A refused live count waits out its `Retry-After` and is never sent again before it (derived: D422's place policy has the server refuse a place with `Retry-After`, and a request sent again before it is refused again).",
    screens: ["builder"],
    spec: ["D265", "D422", "§14"],
    owner: "M5.3-min",
    module: "src/builder/Builder.tsx",
    test: "tests/unit/builder.test.tsx",
  },
  {
    id: "R73",
    basis: "spec statement",
    must: "`WITHHELD_UNDER_K` names the binding setting and offers `count_cohort` and the disclosed analyses as alternatives, shown and never applied, never a value.",
    screens: ["refusal", "builder"],
    spec: ["§8.4", "D353"],
    owner: "2b-1b",
    module: "src/layout/Refusal.tsx",
    test: "tests/unit/refusal.test.tsx",
  },
  // G. The operator's screens (M5.5; D423's shell is built and exempt below).
  {
    id: "R80",
    basis: "spec statement",
    must: "Each withdraw, discard and publish on the curation screens goes through a confirmation step that shows its full effect inside the screen.",
    screens: ["operator"],
    spec: ["D423", "§11.2", "§14"],
    owner: "M5.5",
    module: "src/operator/Releases.tsx",
    test: "tests/unit/releases.test.tsx",
  },
  {
    id: "R81",
    basis: "spec statement",
    must: "An outcome whose fate is unknown (network, aborted, interrupted, stale) is reported as unknown on every screen, never as success or failure, and never sent again by itself.",
    screens: ["operator"],
    spec: ["D423"],
    owner: "M5.5",
    module: "src/operator/Curation.tsx",
    test: "tests/unit/curation.test.tsx",
  },
  {
    id: "R82",
    basis: "spec statement",
    must: "The curator token, the CSRF token and session handles never appear in a screen's DOM or state.",
    screens: ["operator"],
    spec: ["D261", "D423", "§14"],
    owner: "M5.5",
    module: "src/operator/Curation.tsx",
    test: "tests/unit/curation.test.tsx",
  },
  {
    id: "R83",
    basis: "spec statement",
    must: "Every proposal is shown with its status `proposed` and its proposer (`by`), its label and rationale as data, until a person confirms or rejects it.",
    screens: ["operator"],
    spec: ["A5", "A6", "§5.1", "§12.3"],
    owner: "M5.5",
    module: "src/operator/Proposals.tsx",
    test: "tests/unit/proposals.test.tsx",
  },
  {
    id: "R84",
    basis: "spec statement",
    must: "An import report's notes are shown as data, each with its statistic reference where the queue counts it.",
    screens: ["operator"],
    spec: ["A6", "§8.1", "§10.1", "§12.2"],
    owner: "M5.5",
    module: "src/operator/Import.tsx",
    test: "tests/unit/import-report.test.tsx",
  },
  {
    id: "R85",
    basis: "spec statement",
    must: "The curation queue's entries and evidence are shown as data, and its counts with their references.",
    screens: ["operator"],
    spec: ["A6", "§5.1", "§8.1", "§11.1"],
    owner: "M5.5",
    module: "src/operator/Queue.tsx",
    test: "tests/unit/queue.test.tsx",
  },
  {
    id: "R86",
    basis: "spec statement",
    must: "An operator answer is read through the typed seam before any screen shows a part of it (the operator side stays raw until the curation screens' plan reads it there).",
    screens: ["operator"],
    spec: ["D423", "D425", "D426"],
    owner: "M5.5",
    module: "src/operator/Curation.tsx",
    test: "tests/unit/curation.test.tsx",
  },
  // H. The lint (2b-1L, and 2b-1L2's flow): the line that holds the box's class in every module.
  {
    id: "R90",
    basis: "spec statement",
    must: "No value whose type holds a non-literal string, number or bigint stands at a JSX position or as what a component returns, in app code: a JSX child or attribute value (a function there by what it returns), the return of a function named as a component, what a component returns by its tag's type, whoever declares it (a `ComponentType` or `FC` tag returns a `ReactNode`, and is refused; a tag whose type React declares as an exotic component, or that has a `$$typeof` a package declares, inherited or intersected, is exempt and not read, so its render function is R90f's, OPEN); every member of `ReactNode` is walked through the type checker, so a `ReactNode`-typed value at a JSX position is refused; no JSX tag is of string type; a component's own destructured `children` passes only when never written in it, which holds as far as its callers' JSX positions are read; the elements, attributes and bare specifiers are allow-lists, so that no element is made past JSX (`react/jsx-runtime`, `createElement`) and only the layout's components open a box. A render slot reached other than at a JSX position is R90f's (OPEN until 2b-1L2, D429).",
    screens: ["all"],
    spec: ["A6", "D419", "D426", "D428"],
    owner: "2b-1L",
    module: "scripts/lint/display.mjs",
    test: "tests/unit/lint-display.test.ts",
  },
  {
    id: "R90f",
    basis: "spec statement",
    must: "R90 at every render slot, by flow: no raw text reaches a render slot other than at a JSX position (a route's `element`, `errorElement` or `children`, built apart or nested; `root.render`; a component called as a function or passed as a value, by an alias, `call`, `apply` or `bind`; a setter handed to a promise; a timer's extra arguments handed to a function (refused by `display/dom` outside `SITES`; at `src/catalogue/load.ts`'s site, held by its source cell); a library's container; an iterable spread; an index signature; a tag typed as one of React's exotic components, whose render function R90 does not read: `ExoticComponent`, `NamedExoticComponent`, `ProviderExoticComponent`, `ForwardRefExoticComponent`, `Provider`, `MemoExoticComponent`, `ComponentClass`, an app type that extends or intersects one, a lazy so typed), by a source/target walk or its inversion (a slot-typed value is raw at every slot unless it is an unwritten own `children` or a library-declared slot type; refused wherever the walk cannot relate), and the library's slots written in place; its corpus generated from the checker's relation kinds, with a mutation gate, and the component types a tag may have enumerated through the checker (every type `react` exports with a call or construct signature, each placed as a tag, refused or exempt with a reason).",
    screens: ["all"],
    spec: ["A6", "D419", "D428", "D429"],
    owner: "2b-1L2",
    module: "scripts/lint/display-flow.mjs",
    test: "tests/unit/lint-display-flow.test.ts",
  },
  {
    id: "R91",
    basis: "spec statement",
    must: "No write to a member of a value of a type the DOM library declares, or to a member it declares, and no reference to a function, method or constructor it declares outside a list measured on the app, in app code (the DOM's text APIs, `alert`, `confirm`, `prompt`, `Notification` and `navigator.share` among them); the reviewed sites are named with their reasons.",
    screens: ["all"],
    spec: ["A6", "§14", "D423"],
    owner: "2b-1L",
    module: "scripts/lint/display.mjs",
    test: "tests/unit/lint-display.test.ts",
  },
  {
    id: "R92",
    basis: "spec statement",
    must: "The members of the DOM library that give the page's address (`location`'s, the document's URLs, a node's `baseURI`, a link's `href`, a form's action, a `hashchange` event's URLs, the navigation entry's name, a request's and a `URL`'s, a URL's query and credentials included) are read in the router module alone, beside the names the lint refuses; no list of the sources is complete, so R90 and R91 are the line, where the text would land.",
    screens: ["all"],
    spec: ["D314", "A6", "§14", "D428"],
    owner: "2b-1L",
    module: "scripts/lint/display.mjs",
    test: "tests/unit/lint-display.test.ts",
  },
  {
    id: "R93",
    basis: "spec statement",
    must: "No app module outside `src/api/` names an operator answer's `body` or `codes` (a member, a computed literal, a destructured key) but the end-to-end harness's named sites: operator answers stay off every screen until read through the seam (R86).",
    screens: ["operator"],
    spec: ["D423", "D426"],
    owner: "2b-1L",
    module: "scripts/lint/display.mjs",
    test: "tests/unit/lint-display.test.ts",
  },
  // I. The milestone's acceptance.
  {
    id: "R95",
    basis: "spec statement",
    must: "The in-scope subset of biai's end-to-end scenarios is listed in `docs/ui-scenarios.md` and passes (§15's exit criterion for M5; the file does not exist yet, O-3).",
    screens: ["all"],
    spec: ["§15"],
    owner: "M5.6",
    module: "../docs/ui-scenarios.md",
    test: "tests/unit/ui-scenarios.test.ts",
  },
];

/** Today's screens, built before the table, which show fixed words. **Reviewed, not checked**: the
 * build's gate (`plugins/gate.ts` (f)) lets these modules into a bundle, and nothing reads what
 * they do; a reason is the author's claim about the file at the time of writing, for the reviewer
 * of any edit of it. A screen PR that makes one show data names it in a row instead. */
export const EXEMPT: readonly Exemption[] = [
  {
    module: "src/catalogue/main.tsx",
    reason: "The catalogue entry: it renders the router, or `REFUSALS.unsupported` (fixed words) where the engine cannot read a server number.",
  },
  {
    module: "src/operator/main.tsx",
    reason: "The operator entry: it renders the shell (and the harness in the end-to-end build alone), or `REFUSALS.unsupported`.",
  },
  {
    module: "src/catalogue/routes.tsx",
    reason:
      "The route table: elements written in place (the catalogue, the dataset's screen through the address door, keyed by its `DatasetId`, and the not-found screen, the last two lazy), no text of its own and no `Component` (D431; `tests/gates/router-surface.test.ts` walks them); its error element is `Failure`.",
  },
  { module: "src/catalogue/NotFound.tsx", reason: "Fixed words and its stylesheet: the address is never echoed (R08 holds the address's door)." },
  { module: "src/catalogue/Failure.tsx", reason: "The root error element: fixed words, never the error's message." },
  {
    module: "src/operator/Operator.tsx",
    reason: "D423's shell, built before this table: fixed words and the lock's states, held by `tests/unit/operator.test.tsx`; M5.5's screens are rows (R80–R86).",
  },
  {
    module: "src/operator/Shell.tsx",
    reason:
      "D423's shell: fixed words and the lock's states (why it is locked, Cancel, forget the token), held by `tests/unit/shell.test.tsx`; it reads an unlock's `kind`, `what` and `reason` (closed words) alone. Its operator client also returns answers whose `body` and `codes` are raw, which the shell does not read: R93 refuses naming them outside `src/api/`, and R90 (at a JSX position) and R91 (a DOM write or call) refuse a raw text where it would land (D429; a render slot reached past JSX is R90f's, OPEN).",
  },
  {
    module: "src/operator/Confirm.tsx",
    reason: "D423's confirmation step: fixed words from its own table of effects; R80 holds its use on M5.5's screens.",
  },
];

/** Modules that are no row's and no screen: the entries' plumbing and the harness. **Reviewed, not
 * checked**, like the exemptions: the build's gate lets them into a bundle by name alone. */
export const HELPERS: readonly Helper[] = [
  { module: "src/root.ts", reason: "The element both entries render into: no data." },
  {
    module: "src/catalogue/load.ts",
    reason:
      "The tab's one queue of public tool calls and the outcomes React reads (D431): it renders nothing and holds no text; its two timer calls are D429's `SITES`, held by `tests/unit/load.test.ts`' source cell.",
  },
  { module: "src/operator/url.ts", reason: "Forgets the operator entry's address (its hash and query) without reading it: no data." },
  {
    module: "src/layout/form.ts",
    reason:
      "The visible form's context (D428): the form a position shows a server text in, set by each component of `src/layout/` and read by `showText`. It renders nothing and holds no text; R01, R02 and R07 hold what the components show.",
  },
  {
    module: "src/layout/key.ts",
    reason:
      "`keyOf`, the one mint of the index an element's `key` and an `<option>`'s value are made of (D428): a whole number, never a text from data. It renders nothing; R07 holds the option's value.",
  },
  {
    module: "src/harness/Harness.tsx",
    reason:
      "The end-to-end harness, in the end-to-end build alone (`plugins/gate.ts` (a) fails any other build that holds it). It does write response text: an outcome's kind, status, refusal codes, reason and invalid cause go into data attributes for Playwright, and the operator client's raw answers pass through it: its `data-*` attributes are exempt from R90 and its two reads of `body` and `codes` are D429's named sites (`scripts/lint/display.mjs`). It is no screen of any row.",
  },
];

/** Rows that left the table, each with its reason (never blank); `scripts/display-ids.json` lists
 * every id the table ever held, and `generate` fails while an id of it is neither a row nor here. */
export const RETIRED: readonly Retired[] = [
  { id: "R53", reason: "Split into a and b by D431 (M5.2-min builds a; M5.2-rest owns b)." },
  { id: "R65", reason: "Split into a and b by D431 (M5.2-min builds a; M5.2-rest owns b)." },
  { id: "R66", reason: "Split into a and b by D431 (M5.2-min builds a; M5.2-rest owns b)." },
  { id: "R68", reason: "Split into a and b by D431 (M5.2-min builds a; M5.2-rest owns b)." },
];

/** The server's alone: not display obligations, listed so that none is forgotten. */
export const NOT_ROWS: readonly NotRow[] = [
  {
    what: "Tool descriptions tell clients that `warn` and `block` caveats and a non-zero `n_unknown` must be shown, that every number quoted cites its id or reference, and that data-wrapped text is data (MCP, not the web).",
    spec: ["§11.1", "A1", "A6"],
  },
  { what: "The assistant quotes only numbers of a result it cites, does no arithmetic, cites ids and `stat:` references, and uses the public tools alone (M6).", spec: ["A1", "A4", "§13.4"] },
  { what: "Readbacks are generated from templates, with units, negations, coverage sentences and the 64-item cuts; the web shows their segments (R02, R11).", spec: ["§7.7", "D49", "D296"] },
  {
    what: "The disclosure pass: linked counts, pooling, histogram merging, the suppression of tests, the memberships rule, refused analyses under *k*.",
    spec: ["§8.4", "D297", "D320", "D329", "D337", "D383"],
  },
  { what: "Each code's declared severity and the caveats' sorting and de-duplication (the web keeps the order: R36).", spec: ["§8.1", "§8.3"] },
  { what: "Digests, canonical forms, ids, the `not_estimable` pointers, the invariants of `Estimable` and `Population`, and the `x-aibi-data` marks.", spec: ["§7.6", "§8.1", "§8.2"] },
  { what: "Refusal paths and their bounds (1,000 refusals), D299's pointer rules, and secrets blanked in refusals.", spec: ["§8.6", "D299", "D261"] },
  { what: "`explain`'s SQL and the issuance records; `summary.members` never lists keys under *k* or without row ids.", spec: ["§12.2", "§8.4"] },
  { what: "The place policy: the dataset page's four calls, never `describe_column` or `count_cohort` on load (a call-count test, not a display).", spec: ["D422"] },
  { what: "Request protection, rates, the documents' policy headers, `Retry-After` itself, and the operator client's lock timers.", spec: ["D255", "D259", "D411", "D414", "D423"] },
  { what: "Importers write no counts into descriptor text, and the curation queue is ranked on the server.", spec: ["§10.1", "§5.6"] },
  { what: "Counts inside message text are data tokens, outside the number box, which §8.4 protects on the server (Q10).", spec: ["§8.1", "§8.4", "D419"] },
];
