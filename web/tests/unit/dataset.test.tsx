/**
 * The dataset's screen (`#/datasets/<id>`; D431; D427's R63, R64, R68a): `Description`, the view of
 * an answer of `describe_dataset` (its loader's states are `dataset-states.test.tsx`'s: a row's own
 * test file names no hook, D427). The view shows the
 * dataset's id, release, disclosure and standing caveats, the applicable analyses worded as R45
 * words them (`missing` and `unconfirmed` whatever the status; an unknown status as its text), the
 * columns' total and where the next ones start, and, in fixed words, what it does not show yet
 * (T8, n6).
 */
import { describe, expect, it } from "vitest";

import { boxText } from "../../src/api/box";
import { Description, MEMBERS_NOT_SHOWN, UNKNOWN_STATUS } from "../../src/dataset/Dataset";
import { sentinelled } from "./answers";
import { render } from "./render";
import { answersOf, describedOf, orchardText } from "./screens";

type Described = ReturnType<typeof describedOf>;

const ORCHARD_TEXT = orchardText("describe_dataset");

const answerOf = (body: Record<string, unknown> = {}): Described => describedOf(JSON.stringify({ ...JSON.parse(ORCHARD_TEXT), ...body }));

async function textOf(answer: Described): Promise<string> {
  const shown = await render(<Description answer={answer} />);
  const text = shown.container.textContent;
  shown.unmount();
  return text;
}

async function analysesOf(answer: Described): Promise<string[]> {
  const shown = await render(<Description answer={answer} />);
  const items = [...shown.container.querySelectorAll("[aria-label='applicable analyses'] > [role=listitem]")].map((item) => item.textContent);
  shown.unmount();
  return items;
}

describe("Description (the view of an answer)", () => {
  it("R63: the dataset's standing caveats (unconfirmed fields, proposed coverage) are shown (R21)", async () => {
    expect.hasAssertions();
    const shown = await render(<Description answer={answerOf()} />);
    const caveats = shown.container.querySelector("[aria-label=caveats]")?.textContent ?? "";
    expect(caveats).toMatch(/^UNCONFIRMED_SEMANTICS \(warning\): 17 descriptor fields that queries read are not confirmed/u);
    expect(caveats).toContain("harvests/fields/primary_key (proposed)");
    expect(caveats).toContain("affects: /relationships/0, /tables/0, /tables/1");
    shown.unmount();
    const covered = answerOf({ caveats: [{ code: "COVERAGE_PROPOSED", severity: "warn", message: [{ text: "Coverage of " }, { data: "rel:a.b" }, { text: " is proposed" }], affects: ["/coverage/0"] }] });
    expect(await textOf(covered)).toContain("COVERAGE_PROPOSED (warning): Coverage of rel:a.b is proposed affects: /coverage/0");
  });

  it("R64: the dataset page shows the applicable analyses, worded as R45 words them", async () => {
    expect.hasAssertions();
    expect(await analysesOf(answerOf())).toEqual([
      "compare.columns version 1.0.0: available with caveats; unconfirmed: columns",
      "compare.existence version 1.1.0: available",
      "summary.distribution version 1.0.0: available with caveats; unconfirmed: columns",
      "summary.members version 1.0.0: available",
      "survival.cox version 1.0.0: unavailable; missing: endpoint",
      "survival.km version 1.1.0: unavailable; missing: endpoint",
    ]);
    // `missing` and `unconfirmed` whenever not empty, whatever the status; an unknown status as its text.
    const odd = answerOf({
      applicable_analyses: [
        { analysis: "a.x", version: "1.0.0", status: "available", missing: ["endpoint", "unit"], unconfirmed: ["columns"] },
        { analysis: "a.y", version: "2.0.0", status: "unavailable", missing: [], unconfirmed: [] },
      ],
    });
    expect(await analysesOf(odd)).toEqual(["a.x version 1.0.0: available; missing: endpoint, unit; unconfirmed: columns", "a.y version 2.0.0: unavailable"]);
    const first = odd.applicable_analyses[0];
    if (first === undefined) {
      throw new Error("an analysis");
    }
    const unknown = { ...odd, applicable_analyses: [{ ...first, status: boxText("retired") }] } as Described;
    expect(await analysesOf(unknown)).toEqual([`a.x version 1.0.0: ${UNKNOWN_STATUS} retired; missing: endpoint, unit; unconfirmed: columns`]);
  });

  it("R68a: the release, the effective disclosure, columns_total and where the next columns start are shown", async () => {
    expect.hasAssertions();
    const text = await textOf(answerOf());
    expect(text).toContain("Release @1 · manifest sha256:af9437cdad3f3621ce3afc862cf8eb6dbd78996ef4a7c693c0801ed839612900");
    expect(text).toContain("Disclosure: none (no minimum cell count applies).");
    expect(text).toContain("Columns: 9");
    expect(text).not.toContain("on are not on this page");
    const paged = await textOf(describedOf(orchardText("describe_dataset", 1)));
    expect(paged).toContain("Columns: 9; the columns from position 3 on are not on this page.");
    const floored = await textOf(describedOf(orchardText("describe_dataset", 2)));
    expect(floored).toContain("Disclosure: minimum cell count k = 50: counts of units from 1 to k − 1, and counts that would reveal them, are suppressed (§8.4).");
    expect(floored).toMatch(/Table trees \(trees\)Rows: suppressed stat:sha256:[0-9a-f]{64}\/trees\/n_rows\?floor=50/u);
  });

  it("says in fixed words that descriptor members are not shown, where they are (a column's at its own screen, D443), and names the dataset by its id (T8, n6)", async () => {
    const shown = await render(<Description answer={answerOf()} />);
    const legend = shown.container.querySelector(".legend")?.textContent;
    expect(legend).toBe(`${MEMBERS_NOT_SHOWN[0]}orchard${MEMBERS_NOT_SHOWN[1]}`);
    expect(legend).toContain("are not shown on this page yet. aibi's server-rendered page at /datasets/orchard shows them when a server runs without a web bundle (its default until M5.2-rest, D410), and the describe_dataset tool gives them whole, or, under a disclosure setting, without their curation evidence.");
    expect(legend?.startsWith("Other descriptor members (beyond the tables' ids and labels, the ids, labels, datatypes and identifier flags of the columns and the graph below), among them ")).toBe(true);
    expect(legend).not.toContain("every descriptor's id");
    // The caveats above the notice name a member and its curation status (`harvests/fields/primary_key (proposed)`),
    // so the notice does not say that a member's status is not shown, but of the members they do not name.
    expect(legend).not.toContain("each member's curation status");
    for (const member of ["the dataset's label", "definitions and provenance", "name, packs and source", "grains, roles, primary keys and sources", "each descriptor's kind and version", "who set each member and its curation evidence", "the curation status of each member the caveats above do not name", "extensions", "relationships' child and parent columns, and the coverage and endpoint descriptors"]) {
      expect(legend, member).toContain(member);
    }
    // A column's descriptor is neither D311's page's nor describe_dataset's (D443): no column member is
    // claimed of them, and the words send it to the column's screen and describe_column.
    expect(legend).not.toContain("units");
    expect(legend?.endsWith(" Neither gives a column's descriptor: each column's id below links to the column's own screen, and the describe_column tool gives it.")).toBe(true);
    expect(shown.container.querySelector("h1")?.textContent).toBe("Dataset orchard");
    shown.unmount();
  });

  it("states what the recorded answers hold: curation evidence is in a describe_dataset answer exactly when it has no floor, and no member of a descriptor is shown, as the notice says", async () => {
    expect.hasAssertions();
    const holdsEvidence = (value: unknown): boolean => (Array.isArray(value) ? value.some(holdsEvidence) : typeof value === "object" && value !== null ? Object.entries(value).some(([key, one]) => key === "evidence" || holdsEvidence(one)) : false);
    const recorded = answersOf("describe_dataset").map((found) => JSON.parse(found.body) as Record<string, unknown> & { disclosure: { min_cell_count: number | null }; tables: { descriptor: unknown }[] });
    expect(recorded.length).toBeGreaterThanOrEqual(3);
    expect(recorded.some((raw) => raw.disclosure.min_cell_count !== null)).toBe(true);
    for (const [at, raw] of recorded.entries()) {
      expect(holdsEvidence(raw), String(at)).toBe(raw.disclosure.min_cell_count === null);
      // The members of a descriptor the screen shows, derived by marking each string leaf of the
      // dataset's, the tables', the relationships', the coverage's and the endpoints' descriptors:
      // none appears (the screen reads the answer's own ids, labels and graph, not a descriptor).
      const mark = "§MEMBER§";
      const marked = {
        ...raw,
        descriptor: sentinelled(raw["descriptor"], mark),
        relationships: sentinelled(raw["relationships"], mark),
        coverage: sentinelled(raw["coverage"], mark),
        endpoints: sentinelled(raw["endpoints"], mark),
        tables: raw.tables.map((table) => ({ ...table, descriptor: sentinelled(table.descriptor, mark) })),
      };
      expect(await textOf(describedOf(JSON.stringify(marked))), String(at)).not.toContain(mark);
    }
    expect(MEMBERS_NOT_SHOWN[1]).toContain("gives them whole, or, under a disclosure setting, without their curation evidence.");
  });
});
