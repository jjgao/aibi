/**
 * `Tables` (D431, D440; D427's R66a): each table headed by its id and its label (the hand-made
 * tables' labels differ from their ids, so the two places are told apart), then its row count and
 * its `stat:` reference (never cut), and its columns, each with its id, label, datatype in words (absent:
 * undeclared; unknown: its text, marked) and identifier flag (both arms). Every text is swept.
 */
import { describe, expect, it } from "vitest";

import { boxText, readText } from "../../src/api/box";
import { idOfAddress } from "../../src/api/text";
import { IDENTIFIER, NO_COLUMN, NO_TABLES, Tables, type TablesOut, UNDECLARED, UNKNOWN_DATATYPE } from "../../src/dataset/Tables";
import { leafSweep } from "./injection";
import { render } from "./render";
import { describedOf, orchardText } from "./screens";

/** The dataset the tables are of (the links' first hole). */
const ORCHARD = idOfAddress("orchard");

const REFERENCE = (table: string): string => `stat:sha256:${"cd".repeat(32)}/${table}/n_rows`;

const tablesOf = (tables: unknown[]): TablesOut => describedOf(JSON.stringify({ ...(JSON.parse(orchardText("describe_dataset")) as Record<string, unknown>), tables })).tables;

const table = (name: string, columns: unknown[], count: number | null = 3): unknown => ({ id: name, label: { data: `Label of ${name}` }, descriptor: {}, rows: { count, reference: REFERENCE(name) }, columns });

async function shown(tables: TablesOut): Promise<{ readonly heads: string[]; readonly rows: string[]; readonly columns: string[][] }> {
  const found = await render(<Tables tables={tables} dataset={ORCHARD} />);
  const sections = [...found.container.querySelectorAll("section.table")];
  const result = {
    heads: sections.map((section) => section.querySelector("h3")?.textContent ?? ""),
    rows: sections.map((section) => section.querySelector("h3 + p")?.textContent ?? ""),
    columns: sections.map((section) => [...section.querySelectorAll("[role=listitem]")].map((item) => item.textContent)),
  };
  found.unmount();
  return result;
}

describe("Tables", () => {
  it("R66a: each table is shown with its id and label, then its row count and its reference; its columns with id, label, datatype and identifier flag", async () => {
    expect.hasAssertions();
    const orchard = describedOf(orchardText("describe_dataset")).tables;
    const { heads, rows, columns } = await shown(orchard);
    expect(heads).toEqual(["Table harvests (harvests)", "Table trees (trees)"]);
    expect(rows).toEqual([
      "Rows: 60 stat:sha256:af9437cdad3f3621ce3afc862cf8eb6dbd78996ef4a7c693c0801ed839612900/harvests/n_rows",
      "Rows: 40 stat:sha256:af9437cdad3f3621ce3afc862cf8eb6dbd78996ef4a7c693c0801ed839612900/trees/n_rows",
    ]);
    expect(columns[0]).toEqual([
      "harvests.grade grade, category",
      `harvests.harvest_id harvest_id, string, ${IDENTIFIER}`,
      "harvests.kg kg, integer",
      `harvests.tree_id tree_id, string, ${IDENTIFIER}`,
    ]);
    expect(columns[1]).toContain("trees.tags tags, list of categories");
  });

  it("R66a: `identifier` is said of an identifier and nothing of any other column (m7: the flag false may be undeclared, so no word says \"not an identifier\"); a datatype absent as undeclared, and one the page does not know as its text", async () => {
    expect.hasAssertions();
    const made = tablesOf([
      table("t", [
        { id: "t.a", label: { data: "A" }, identifier: true },
        { id: "t.b", label: { data: "B" }, identifier: false, datatype: "datetime" },
        { id: "t.c", label: { data: "C" }, identifier: false, datatype: "time_offset" },
      ]),
    ]);
    expect((await shown(made)).columns[0]).toEqual([`t.a A, ${UNDECLARED}, ${IDENTIFIER}`, "t.b B, date and time", "t.c C, time offset"]);
    const first = made[0];
    if (first === undefined) {
      throw new Error("one table");
    }
    const odd = [{ ...first, columns: [{ id: boxText("t.x"), label: { data: boxText("X") }, identifier: false, datatype: boxText("decimal128") }] }] as unknown as TablesOut;
    expect((await shown(odd)).columns[0]).toEqual([`t.x X, decimal128 ${UNKNOWN_DATATYPE}`]);
  });

  it("R66a: tells two tables apart by their ids and labels, a suppressed count as suppressed (never 0), with the reference whole", async () => {
    expect.hasAssertions();
    const made = tablesOf([table("one", []), table("two", [], null)]);
    const both = await shown(made);
    expect(both.heads).toEqual(["Table one (Label of one)", "Table two (Label of two)"]);
    expect(both.rows).toEqual([`Rows: 3 ${REFERENCE("one")}`, `Rows: suppressed ${REFERENCE("two")}`]);
    const found = await render(<Tables tables={tablesOf([])} dataset={ORCHARD} />);
    expect(found.container.textContent).toBe(`Tables${NO_TABLES}`);
    found.unmount();
  });

  it("R66a: a table with no column on this page says so in words, never blank (m6: its columns may be on another page)", async () => {
    expect.hasAssertions();
    const found = await render(<Tables tables={tablesOf([table("one", []), table("two", [{ id: "two.a", label: { data: "A" }, identifier: false }])])} dataset={ORCHARD} />);
    const sections = [...found.container.querySelectorAll("section.table")].map((section) => section.textContent);
    expect(sections).toEqual([`Table one (Label of one)Rows: 3 ${REFERENCE("one")}${NO_COLUMN}`, `Table two (Label of two)Rows: 3 ${REFERENCE("two")}two.a A, ${UNDECLARED}`]);
    expect(NO_COLUMN).toBe("No column on this page.");
    found.unmount();
  });

  it("R66a: the heading's parentheses are the page's own, around one isolate of the label as sent, whatever it holds (D440)", async () => {
    expect.hasAssertions();
    const labels = [`${"L".repeat(4095)}‮`, "<img src=x onerror=alert(1)></bdi><script>alert(1)</script>", "one", "   ", "a\r\nb", "x) Table evil (‮y", "javascript:alert(1)"];
    for (const label of labels) {
      const found = await render(<Tables tables={tablesOf([{ ...(table("one", []) as object), label: { data: label } }])} dataset={ORCHARD} />);
      const heading = found.container.querySelector("h3");
      const children = [...(heading?.childNodes ?? [])].map((node) => (node.nodeType === Node.TEXT_NODE ? `text ${node.textContent ?? ""}` : node.nodeName));
      expect(children, label.slice(0, 40)).toEqual(["text Table ", "BDI", "text  (", "BDI", "text )"]);
      expect(heading?.querySelectorAll("bdi")[0]?.textContent).toBe("one");
      expect(found.container.querySelectorAll("img, script, a, b").length).toBe(0);
      found.unmount();
    }
  });

  it("links each column's id to its screen (D443): the target is the dataset's and the answer's own column id, through the door; an id or a dataset not of the grammar gets no link, its id shown all the same", async () => {
    expect.hasAssertions();
    const orchard = describedOf(orchardText("describe_dataset")).tables;
    const linked = await render(<Tables tables={orchard} dataset={ORCHARD} />);
    const anchors = [...linked.container.querySelectorAll("a")];
    const ids = orchard.flatMap((one) => one.columns.map((column) => readText(column.id)));
    expect(ids.length).toBeGreaterThan(5);
    expect(anchors.map((anchor) => anchor.getAttribute("href"))).toEqual(ids.map((id) => `#/datasets/orchard/columns/${id}`));
    expect(anchors.map((anchor) => anchor.textContent)).toEqual(ids);
    expect(anchors.every((anchor) => anchor.closest("[role=listitem]")?.firstElementChild === anchor)).toBe(true);
    linked.unmount();
    const unlinked = await render(<Tables tables={orchard} dataset={null} />);
    expect(unlinked.container.querySelectorAll("a")).toHaveLength(0);
    expect(unlinked.container.textContent).toContain("trees.variety");
    unlinked.unmount();
    const first = orchard[0];
    if (first === undefined) {
      throw new Error("a table");
    }
    // A column id the walk passes but the grammar refuses (a label-like text at the id's position):
    // no link is minted of it, and the label is never the target.
    const odd = [{ ...first, columns: [{ id: boxText("Trees.Variety"), label: { data: boxText("t.ok") }, identifier: false }] }] as unknown as TablesOut;
    const refused = await render(<Tables tables={odd} dataset={ORCHARD} />);
    expect(refused.container.querySelectorAll("a")).toHaveLength(0);
    expect(refused.container.textContent).toContain("Trees.Variety");
    refused.unmount();
  });

  it("links every column of the grammar, whatever its datatype (none declared, one the page does not know, an identifier): exactly its own link, its own id as the target", async () => {
    expect.hasAssertions();
    const made = tablesOf([
      table("t", [
        { id: "t.undeclared", label: { data: "U" }, identifier: false },
        { id: "t.declared", label: { data: "D" }, identifier: false, datatype: "string" },
        { id: "t.key", label: { data: "K" }, identifier: true },
        { id: "t.keyed", label: { data: "KD" }, identifier: true, datatype: "string" },
      ]),
    ]);
    const first = made[0];
    if (first === undefined) {
      throw new Error("one table");
    }
    const odd = [{ ...first, columns: [...first.columns, { id: boxText("t.odd"), label: { data: boxText("O") }, identifier: false, datatype: boxText("decimal128") }] }] as unknown as TablesOut;
    const found = await render(<Tables tables={odd} dataset={ORCHARD} />);
    const items = [...found.container.querySelectorAll("[role=listitem]")];
    expect(items.map((item) => [...item.querySelectorAll("a")].map((anchor) => anchor.getAttribute("href")))).toEqual(["t.undeclared", "t.declared", "t.key", "t.keyed", "t.odd"].map((id) => [`#/datasets/orchard/columns/${id}`]));
    expect(items.map((item) => item.querySelector("a")?.textContent)).toEqual(["t.undeclared", "t.declared", "t.key", "t.keyed", "t.odd"]);
    found.unmount();
  });

  it("shows a hostile text in every position as text alone, by construction", { timeout: 120_000 }, async () => {
    const made = tablesOf([table("t", [{ id: "t.a", label: { data: "A" }, identifier: true, datatype: "number" }])]);
    const found = await leafSweep(made, (value) => <Tables tables={value} dataset={ORCHARD} />, ["SECTION", "H2", "H3", "P", "DIV", "A"], ["stat", "suppressed"]);
    expect(found.problems).toEqual([]);
    expect(found.leaves).toBeGreaterThan(0);
  });
});
