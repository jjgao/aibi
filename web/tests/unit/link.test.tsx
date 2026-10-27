/**
 * `Link` (D428, D431; D427's R55): a link to a dataset's screen is `#/datasets/<id>`, a fragment of
 * the page's own document, composed by `Link` from a `DatasetId` alone (`idOf` of a server text,
 * `idOfAddress` of the address: the generated grammar bounds its characters); a link to a column's
 * screen is `#/datasets/<id>/columns/<column>`, composed by `ColumnLink` from a `DatasetId` and a
 * `ColumnId` alone (D443: `columnOf`, `columnOfAddress`). Text that is not of the grammar gives no
 * id, so no link: never `javascript:`, another origin, a path or a query. The type side (a string or
 * a box refused as a target) is `tests/gates/mints.test.ts`'s; the lint's `HREF_TEMPLATE` holds the
 * attribute's form, a closed list of the two templates.
 */
import { describe, expect, it } from "vitest";

import { boxText } from "../../src/api/box";
import { GRAMMARS } from "../../src/api/generated/shapes";
import { columnOf, columnOfAddress, idOf, idOfAddress } from "../../src/api/text";
import { ColumnLink, Link } from "../../src/layout/Link";
import { render } from "./render";

describe("Link", () => {
  it("R55: a link to a dataset's screen is #/datasets/<id>, composed by Link from a DatasetId alone, never from text, the address or another origin", async () => {
    expect.hasAssertions();
    for (const id of [idOf(boxText("orchard")), idOfAddress("orchard"), idOf(boxText("a_1"))]) {
      expect(id).not.toBeNull();
      if (id !== null) {
        const shown = await render(<Link to={id}>x</Link>);
        const anchor = shown.container.querySelector("a");
        expect(anchor?.getAttribute("href")).toBe(`#/datasets/${id}`);
        expect([...(anchor?.attributes ?? [])].map((attribute) => attribute.name)).toEqual(["href"]);
        shown.unmount();
      }
    }
    // Texts that would make another target are no id: no link can be made of them.
    for (const text of ["javascript:alert(1)", "//evil.example", "https://evil.example/x", "a/b", "a?b=c", "a#b", "../x", "%2e%2e", "ORCHARD", "orch%61rd", "\u202etxt", "", "a b"]) {
      expect(idOf(boxText(text)), text).toBeNull();
      expect(idOfAddress(text), text).toBeNull();
    }
  });

  it("R55: the target is the page's own fragment, whatever the children", async () => {
    expect.hasAssertions();
    const id = idOfAddress("orchard");
    if (id === null) {
      throw new Error("orchard is an id");
    }
    const shown = await render(
      <Link to={id}>
        <span>javascript:alert(1)</span>
      </Link>,
    );
    expect(shown.container.querySelector("a")?.getAttribute("href")).toBe("#/datasets/orchard");
    shown.unmount();
  });

  it("R55: a link to a column's screen is #/datasets/<id>/columns/<column>, composed by ColumnLink from a DatasetId and a ColumnId alone", async () => {
    expect.hasAssertions();
    const dataset = idOfAddress("orchard");
    for (const column of [columnOf(boxText("trees.variety")), columnOfAddress("trees.variety"), columnOf(boxText("a_1.b_2")), columnOfAddress(`t.${"a".repeat(64)}`)]) {
      expect(column).not.toBeNull();
      if (column !== null && dataset !== null) {
        const shown = await render(
          <ColumnLink dataset={dataset} column={column}>
            <span>javascript:alert(1)</span>
          </ColumnLink>,
        );
        const anchor = shown.container.querySelector("a");
        expect(anchor?.getAttribute("href")).toBe(`#/datasets/orchard/columns/${column}`);
        expect([...(anchor?.attributes ?? [])].map((attribute) => attribute.name)).toEqual(["href"]);
        shown.unmount();
      }
    }
    // Texts that would make another target are no column id: no link can be made of them.
    for (const text of ["javascript:alert(1)", "//evil.example", "trees", ".x", "trees.", "trees.x.y", "trees.x/y", "trees.x?y", "trees.x#y", "trees.a__b", "Trees.x", "trees.%2e", "trees.\u202ex", "trees.x ", "", `t.${"a".repeat(65)}`, "variety"]) {
      expect(columnOf(boxText(text)), text).toBeNull();
      expect(columnOfAddress(text), text).toBeNull();
    }
  });
});

describe("the column door against the column grammar (D443)", () => {
  /** The column grammar's own verdict, applied from `GRAMMARS.column` here (the doors take it by name: a
   * door that reads another grammar's, as the analysis grammar's reserved prefixes, differs on these). */
  const { p, m, x } = GRAMMARS.column;
  const oracle = (raw: string): boolean => new RegExp(p, "u").test(raw) && Array.from(raw).length <= m && !x.some((refused) => new RegExp(refused, "u").test(raw));

  /** The table parts and column parts: the reserved prefixes of the analysis grammar and the identifiers' ends. */
  const TABLES = ["trees", "stat", "value", "cohort", "core", "cov", "covered", "dataset", "drv", "ep", "exists", "ids", "iss", "leaf", "model", "rel", "sha256", "analysis", "a", "a_1", "a__b", "_a", "A", "1a", "", "a".repeat(64), "a".repeat(65), "t\u202e", "é"];
  const COLUMNS = ["variety", "x", "a_1", "a__b", "_x", "X", "1x", "", "b".repeat(64), "b".repeat(65), "x\n", "x y", "x.y", "x\u202e", "é", "ı"];

  it("accepts and refuses exactly what GRAMMARS.column does, over every pair (the reserved prefixes of the analysis grammar accepted)", () => {
    expect.hasAssertions();
    let accepted = 0;
    for (const table of TABLES) {
      for (const column of COLUMNS) {
        const raw = `${table}.${column}`;
        const expected = oracle(raw) ? raw : null;
        expect(columnOfAddress(raw), raw).toBe(expected);
        expect(columnOf(boxText(raw)), raw).toBe(expected);
        accepted += expected === null ? 0 : 1;
      }
    }
    expect(accepted).toBeGreaterThan(50);
    for (const prefix of ["stat", "value", "cohort", "core", "cov", "covered", "dataset", "drv", "ep", "exists", "ids", "iss", "leaf", "model", "rel", "sha256"]) {
      expect(columnOfAddress(`${prefix}.variety`), prefix).toBe(`${prefix}.variety`);
    }
  });
});

