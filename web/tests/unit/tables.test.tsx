/**
 * `Tables` (D431, D440; D427's R66a): each table headed by its id and its label (the hand-made
 * tables' labels differ from their ids, so the two places are told apart), then its row count and
 * its `stat:` reference (never cut), and its columns, each with its id, label, datatype in words (absent:
 * undeclared; unknown: its text, marked) and identifier flag (both arms). Every text is swept.
 */
import { describe, expect, it } from "vitest";

import { boxText } from "../../src/api/box";
import { IDENTIFIER, NO_COLUMN, NO_TABLES, Tables, type TablesOut, UNDECLARED, UNKNOWN_DATATYPE } from "../../src/dataset/Tables";
import { leafSweep } from "./injection";
import { render } from "./render";
import { describedOf, orchardText } from "./screens";

const REFERENCE = (table: string): string => `stat:sha256:${"cd".repeat(32)}/${table}/n_rows`;

const tablesOf = (tables: unknown[]): TablesOut => describedOf(JSON.stringify({ ...(JSON.parse(orchardText("describe_dataset")) as Record<string, unknown>), tables })).tables;

const table = (name: string, columns: unknown[], count: number | null = 3): unknown => ({ id: name, label: { data: `Label of ${name}` }, descriptor: {}, rows: { count, reference: REFERENCE(name) }, columns });

async function shown(tables: TablesOut): Promise<{ readonly heads: string[]; readonly rows: string[]; readonly columns: string[][] }> {
  const found = await render(<Tables tables={tables} />);
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
    const found = await render(<Tables tables={tablesOf([])} />);
    expect(found.container.textContent).toBe(`Tables${NO_TABLES}`);
    found.unmount();
  });

  it("R66a: a table with no column on this page says so in words, never blank (m6: its columns may be on another page)", async () => {
    expect.hasAssertions();
    const found = await render(<Tables tables={tablesOf([table("one", []), table("two", [{ id: "two.a", label: { data: "A" }, identifier: false }])])} />);
    const sections = [...found.container.querySelectorAll("section.table")].map((section) => section.textContent);
    expect(sections).toEqual([`Table one (Label of one)Rows: 3 ${REFERENCE("one")}${NO_COLUMN}`, `Table two (Label of two)Rows: 3 ${REFERENCE("two")}two.a A, ${UNDECLARED}`]);
    expect(NO_COLUMN).toBe("No column on this page.");
    found.unmount();
  });

  it("R66a: the heading's parentheses are the page's own, around one isolate of the label as sent, whatever it holds (D440)", async () => {
    expect.hasAssertions();
    const labels = [`${"L".repeat(4095)}‮`, "<img src=x onerror=alert(1)></bdi><script>alert(1)</script>", "one", "   ", "a\r\nb", "x) Table evil (‮y", "javascript:alert(1)"];
    for (const label of labels) {
      const found = await render(<Tables tables={tablesOf([{ ...(table("one", []) as object), label: { data: label } }])} />);
      const heading = found.container.querySelector("h3");
      const children = [...(heading?.childNodes ?? [])].map((node) => (node.nodeType === Node.TEXT_NODE ? `text ${node.textContent ?? ""}` : node.nodeName));
      expect(children, label.slice(0, 40)).toEqual(["text Table ", "BDI", "text  (", "BDI", "text )"]);
      expect(heading?.querySelectorAll("bdi")[0]?.textContent).toBe("one");
      expect(found.container.querySelectorAll("img, script, a, b").length).toBe(0);
      found.unmount();
    }
  });

  it("shows a hostile text in every position as text alone, by construction", { timeout: 120_000 }, async () => {
    const made = tablesOf([table("t", [{ id: "t.a", label: { data: "A" }, identifier: true, datatype: "number" }])]);
    const found = await leafSweep(made, (value) => <Tables tables={value} />, ["SECTION", "H2", "H3", "P", "DIV"], ["stat", "suppressed"]);
    expect(found.problems).toEqual([]);
    expect(found.leaves).toBeGreaterThan(0);
  });
});
