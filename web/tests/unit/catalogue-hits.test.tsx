/**
 * The catalogue (`#/`; D431; D427's R50, R52, R53a, R54): `Hits`, the view of an answer of
 * `search_catalog`. Each row's cell renders the view over answers walked as the seam walks them (the
 * orchard's own, and bodies of the test's that reach every member). The loader's states are
 * `catalogue-states.test.tsx`'s (a row's own test file names no hook, D427).
 */
import { describe, expect, it } from "vitest";

import { boxText } from "../../src/api/box";
import { EMPTY, Hits, LATEST, NOT_LINKED, UNDECLARED, UNKNOWN } from "../../src/catalogue/Catalogue";
import { render, type Rendered } from "./render";
import { hitsOf, orchardText } from "./screens";

const MANIFEST = `sha256:${"ab".repeat(32)}`;

/** A hit with every member, of the test's own. */
const RICH_HIT = {
  dataset: "lending",
  release: { label: 3, manifest: MANIFEST, status: "published" },
  disclosure: { min_cell_count: 5 },
  label: { data: "The lending library" },
  name: { data: "Lending" },
  description: { data: "Books, copies and loans." },
  domain_tags: [{ data: "books" }, { data: "loans" }],
  data_use: [{ system: { data: "DUO" }, code: { data: "0000042" }, label: { data: "general research use" } }],
  packs: ["onco", "demo"],
  concepts: ["concept:a", "concept:b"],
  facets: { "demo.genre": [{ data: "fiction" }, { data: "poetry" }] },
  tables: [
    { id: "books", label: { data: "Books" }, grain: { data: "One row per book" }, role: "entity", rows: { count: 12, reference: `stat:${MANIFEST}/books/n_rows` } },
    { id: "loans", label: { data: "Loans" }, rows: { count: null, reference: `stat:${MANIFEST}/loans/n_rows` } },
  ],
  tables_left_out: 3,
};

const answerOf = (body: Record<string, unknown>): ReturnType<typeof hitsOf> => hitsOf(JSON.stringify({ hits: [RICH_HIT], total: 7, caveats: [], ...body }));

async function view(answer: ReturnType<typeof hitsOf>): Promise<Rendered> {
  return render(<Hits answer={answer} />);
}

describe("Hits (the view of an answer)", () => {
  it("R50: each hit shows its label, name, description, domain tags, data-use terms, packs, concepts, pack facets and tables (grain, role, rows)", async () => {
    expect.hasAssertions();
    const shown = await view(answerOf({}));
    const hit = shown.container.querySelector("section.hit");
    const text = hit?.textContent ?? "";
    expect(hit?.querySelector("h2")?.textContent).toBe("The lending library");
    expect(hit?.querySelector("h2 a")?.getAttribute("href")).toBe("#/datasets/lending");
    for (const part of [
      "Lending",
      "Books, copies and loans.",
      "Domain tags: books, loans",
      "Data use: general research use (DUO:0000042)",
      "Packs: onco, demo",
      "Concepts: concept:a, concept:b",
      "Facet demo.genre: fiction, poetry",
      `books Books, grain One row per book, role entity, rows 12 stat:${MANIFEST}/books/n_rows`,
      `loans Loans, grain ${UNDECLARED}, role ${UNDECLARED}, rows suppressed stat:${MANIFEST}/loans/n_rows`,
      "and 3 more, left out by the server",
    ]) {
      expect(text, part).toContain(part);
    }
    shown.unmount();
  });

  it("R50: omits a name, a description and the lists a hit does not have, as D311 does; an unknown role is its text, marked", async () => {
    expect.hasAssertions();
    const bare = { ...RICH_HIT, domain_tags: [], data_use: [], packs: [], concepts: [], facets: {}, tables_left_out: 0 } as Record<string, unknown>;
    delete bare["name"];
    delete bare["description"];
    const shown = await view(answerOf({ hits: [bare] }));
    const text = shown.container.querySelector("section.hit")?.textContent ?? "";
    for (const absent of ["Lending", "Books, copies", "Domain tags", "Data use", "Packs", "Concepts", "Facet", "left out"]) {
      expect(text, absent).not.toContain(absent);
    }
    shown.unmount();
    const answer = answerOf({});
    const table = answer.hits[0]?.tables[0];
    if (table === undefined) {
      throw new Error("a table");
    }
    const odd = { ...answer, hits: [{ ...answer.hits[0], tables: [{ ...table, role: boxText("bridge") }] }] } as typeof answer;
    const unknown = await view(odd);
    expect(unknown.container.textContent).toContain(`role bridge ${UNKNOWN}, rows`);
    unknown.unmount();
  });

  it("R50: a hit whose dataset is no id is shown unlinked, with fixed words", async () => {
    expect.hasAssertions();
    const answer = answerOf({});
    const odd = { ...answer, hits: [{ ...answer.hits[0], dataset: boxText("javascript:alert(1)") }] } as typeof answer;
    const shown = await view(odd);
    expect(shown.container.querySelector("a")).toBeNull();
    expect(shown.container.querySelector("h2")?.textContent).toBe(`The lending library ${NOT_LINKED}`);
    shown.unmount();
  });

  it("R52: each hit's release label, status and effective disclosure are shown", async () => {
    expect.hasAssertions();
    const shown = await view(answerOf({}));
    const text = shown.container.textContent;
    expect(text).toContain(`Dataset lending, release @3 · manifest ${MANIFEST}, status published`);
    expect(text).toContain("Disclosure: minimum cell count k = 5: counts of units from 1 to k − 1, and counts that would reveal them, are suppressed (§8.4).");
    shown.unmount();
    const orchardHits = await view(hitsOf(orchardText("search_catalog")));
    expect(orchardHits.container.textContent).toContain("Disclosure: none (no minimum cell count applies).");
    orchardHits.unmount();
    const answer = answerOf({});
    const hit = answer.hits[0];
    if (hit === undefined) {
      throw new Error("a hit");
    }
    const odd = { ...answer, hits: [{ ...hit, release: { ...hit.release, status: boxText("withdrawn") } }] } as typeof answer;
    const unknown = await view(odd);
    expect(unknown.container.textContent).toContain(`status withdrawn ${UNKNOWN}`);
    expect(unknown.container.textContent).toContain("@draft");
    unknown.unmount();
  });

  it("R53a: the hits' caveats (R21), the total and where the next page starts are shown", async () => {
    expect.hasAssertions();
    const caveat = { code: "SUPPRESSED", severity: "info", message: [{ text: "Counts are suppressed" }], affects: ["/hits/0/tables/1/rows"] };
    const shown = await view(answerOf({ caveats: [caveat], next_offset: 50 }));
    const text = shown.container.textContent;
    expect(text).toContain("Datasets: 7");
    expect(shown.container.querySelector("[aria-label=caveats]")?.textContent).toBe("SUPPRESSED (note): Counts are suppressed affects: /hits/0/tables/1/rows");
    expect(text).toContain("More datasets follow from position 50; this page shows the first page.");
    shown.unmount();
    const last = await view(answerOf({}));
    expect(last.container.textContent).not.toContain("More datasets follow");
    last.unmount();
    const paged = await view(hitsOf(orchardText("search_catalog", 1)));
    expect(paged.container.textContent).toContain("Datasets: 2");
    expect(paged.container.textContent).toContain("More datasets follow from position 1; this page shows the first page.");
    paged.unmount();
  });

  it("R54: only the latest published release of each dataset is listed, and the page says so", async () => {
    expect.hasAssertions();
    const shown = await view(answerOf({}));
    expect(shown.container.querySelector("p")?.textContent).toBe(LATEST);
    expect(LATEST).toBe("The latest published release of each dataset is listed; text in the descriptors comes from the datasets and is shown as it is.");
    shown.unmount();
  });

  it("shows no hit in fixed words, with the total", async () => {
    const shown = await view(hitsOf(JSON.stringify({ hits: [], total: 0, caveats: [] })));
    expect(shown.words()).toBe(`${LATEST} Datasets: 0 ${EMPTY}`);
    shown.unmount();
  });
});
