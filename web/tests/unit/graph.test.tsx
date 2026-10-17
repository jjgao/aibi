/**
 * `Graph` (D431; D427's R65a): the table graph as a list, its tables (the heading says, in fixed
 * words, that coverage tables are not in it) and each relationship with its child and parent tables
 * and its cardinality in words (one the page does not know: its text, marked). Every text is swept.
 */
import { describe, expect, it } from "vitest";

import { boxText } from "../../src/api/box";
import { Graph, GRAPH_HEADING, NO_RELATIONSHIP, NO_TABLE, type TableGraph, UNKNOWN_CARDINALITY } from "../../src/dataset/Graph";
import { walked } from "./answers";
import { leafSweep } from "./injection";
import { render } from "./render";
import { describedOf, orchardText } from "./screens";

const graphOf = (body: unknown): TableGraph => walked("TableGraph", JSON.stringify(body)) as TableGraph;

async function shown(graph: TableGraph): Promise<string> {
  const found = await render(<Graph graph={graph} />);
  const text = found.container.textContent;
  found.unmount();
  return text;
}

describe("Graph", () => {
  it("R65a: the table graph is shown as a list: the tables but coverage tables, and each relationship with its child and parent tables and its cardinality", async () => {
    expect.hasAssertions();
    const orchard = describedOf(orchardText("describe_dataset")).graph;
    expect(await shown(orchard)).toBe(`${GRAPH_HEADING}Tables: harvests, treesRelationship rel:harvests.tree_id: harvests to trees, many to one`);
    const graph = graphOf({
      tables: ["a", "b", "c"],
      edges: [
        { relationship: "rel:b.a_id", child: "b", parent: "a", cardinality: "many-to-one" },
        { relationship: "rel:c.b_id", child: "c", parent: "b", cardinality: "one-to-one" },
      ],
    });
    const found = await render(<Graph graph={graph} />);
    expect([...found.container.querySelectorAll("[role=listitem]")].map((item) => item.textContent)).toEqual(["Relationship rel:b.a_id: b to a, many to one", "Relationship rel:c.b_id: c to b, one to one"]);
    expect(found.container.querySelector("h2")?.textContent).toBe(GRAPH_HEADING);
    expect(GRAPH_HEADING).toContain("coverage tables are not in it");
    found.unmount();
  });

  it("R65a: shows a cardinality the page does not know as its text, marked, never dropped", async () => {
    expect.hasAssertions();
    const graph = { tables: [boxText("a")], edges: [{ relationship: boxText("rel:a.x"), child: boxText("a"), parent: boxText("a"), cardinality: boxText("many-to-many") }] } as unknown as TableGraph;
    expect(await shown(graph)).toContain(`a to a, many-to-many ${UNKNOWN_CARDINALITY}`);
  });

  it("R65a: shows a graph with no edge and no table as its heading and the words for each (an empty list is never blank: m6)", async () => {
    expect.hasAssertions();
    expect(await shown(graphOf({ tables: [], edges: [] }))).toBe(`${GRAPH_HEADING}Tables: ${NO_TABLE}${NO_RELATIONSHIP}`);
    expect(NO_TABLE).toBe("No table.");
    expect(NO_RELATIONSHIP).toBe("No relationship.");
  });

  it("shows a hostile text in every position as text alone, by construction", { timeout: 60_000 }, async () => {
    const unknown = { tables: [boxText("a")], edges: [{ relationship: boxText("r"), child: boxText("a"), parent: boxText("b"), cardinality: boxText("odd") }] } as unknown as TableGraph;
    for (const graph of [describedOf(orchardText("describe_dataset")).graph, unknown]) {
      const found = await leafSweep(graph, (value) => <Graph graph={value} />, ["SECTION", "H2", "P", "DIV"]);
      expect(found.problems).toEqual([]);
      expect(found.leaves).toBeGreaterThan(0);
    }
  });
});
