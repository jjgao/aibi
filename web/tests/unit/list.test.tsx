/**
 * `List` and `ServerList` (D430; D427's R03, D312, §8.4). The hits are the orchard's catalogue answer
 * with its lists replaced, walked as an answer is: D312's lists (domain tags, a facet's values) cut at
 * `LISTED`, the export's 64, with "and N more" counted by the page over what the server sent; a
 * list the server cut (`tables`) shown whole, with `tables_left_out`, the server's own number.
 */
import { describe, expect, it } from "vitest";

import { boxMap, boxText } from "../../src/api/box";
import { LISTED } from "../../src/api/numbers";
import type { components } from "../../src/api/generated/openapi";
import type { Boxed } from "../../src/api/typed";
import { List, ServerList } from "../../src/layout/List";
import { Ref } from "../../src/layout/Text";
import { walked } from "./answers";
import { leafSweep } from "./injection";
import { render } from "./render";
import { ORCHARD } from "./typed-support";

type Hit = Boxed<components["schemas"]["CatalogHit"]>;

const SEARCH = ORCHARD.find((answer) => answer.operation === "search_catalog" && answer.status === 200);
const ORCHARD_HIT = (JSON.parse(SEARCH?.body ?? "{}") as { hits: Record<string, unknown>[] }).hits[0] ?? {};

/** The orchard's hit, its tags, facets, tables and tables left out replaced, walked. */
function hitOf(tags: number, facets: Readonly<Record<string, number>> = {}, leftOut = "0", tables?: unknown[]): Hit {
  const values = (count: number, prefix: string): { data: string }[] => Array.from({ length: count }, (_, index) => ({ data: `${prefix} ${String(index)}` }));
  const body = {
    ...ORCHARD_HIT,
    domain_tags: values(tags, "tag"),
    facets: Object.fromEntries(Object.entries(facets).map(([key, count]) => [key, values(count, key)])),
    ...(tables === undefined ? {} : { tables }),
  };
  return (walked("CatalogHit", JSON.stringify(body).replace(/"tables_left_out":0\b/u, `"tables_left_out":${leftOut}`)) as Hit);
}

/** What a list shows: each member's text, and what follows them. */
async function shownOf(element: React.ReactElement): Promise<{ readonly members: string[]; readonly more: string | null }> {
  const shown = await render(<div>{element}</div>);
  const found = {
    members: [...shown.container.querySelectorAll('[role="listitem"]')].map((item) => item.textContent),
    more: shown.container.querySelector(".more")?.textContent ?? null,
  };
  shown.unmount();
  return found;
}

describe("List", () => {
  it("R03: shows at most 64 members of D312's lists, then how many more, and the server's own number for a list it cut", async () => {
    expect.hasAssertions();
    expect(LISTED).toBe(64);
    const three = await render(<List hit={hitOf(66)} at="domain_tags" />);
    expect(three.container.textContent.startsWith("tag 0, tag 1, tag 2, ")).toBe(true);
    expect(three.container.textContent.endsWith("tag 63, and 2 more")).toBe(true);
    three.unmount();
    for (const length of [0, 1, 63, 64, 65, 130, 1000]) {
      const hit = hitOf(length, { "tissue.site": length, "assay.kind": 2 });
      for (const at of ["domain_tags", { facet: boxText("tissue.site") }] as const) {
        const { members, more } = await shownOf(<List hit={hit} at={at} />);
        expect(members.length).toBe(Math.min(length, LISTED));
        expect(members.slice(0, 2)).toEqual(Array.from({ length: Math.min(length, 2) }, (_, index) => `${at === "domain_tags" ? "tag" : "tissue.site"} ${String(index)}`));
        expect(more).toBe(length > LISTED ? `and ${String(length - LISTED)} more` : null);
      }
      expect((await shownOf(<List hit={hit} at={{ facet: boxText("absent.facet") }} />)).members).toEqual([]);
    }
    // the lists differ in length: each is cut and counted on its own (m2 of #121 round 1)
    for (const [tags, values] of [
      [3, 200],
      [200, 3],
      [LISTED + 1, 1000],
      [0, LISTED + 1],
    ] as const) {
      const hit = hitOf(tags, { "tissue.site": values });
      for (const [at, length] of [
        ["domain_tags", tags],
        [{ facet: boxText("tissue.site") }, values],
      ] as const) {
        const { members, more } = await shownOf(<List hit={hit} at={at} />);
        expect(members.length, `${String(tags)} tags, ${String(values)} values`).toBe(Math.min(length, LISTED));
        expect(more).toBe(length > LISTED ? `and ${String(length - LISTED)} more` : null);
      }
    }
    const table = (ORCHARD_HIT["tables"] as unknown[])[0];
    for (const [leftOut, more] of [
      ["0", null],
      ["36", "and 36 more, left out by the server"],
      ["9007199254740993", "and 9007199254740993 more, left out by the server"],
    ] as const) {
      const cut = hitOf(0, {}, leftOut, Array.from({ length: 100 }, () => table));
      const { members, more: said } = await shownOf(<ServerList members={cut.tables} each={(one) => <Ref value={one.id} />} leftOut={cut.tables_left_out} />);
      expect(members.length).toBe(100);
      expect(said).toBe(more);
    }
  });

  it("cuts a member of more than 2,000 code points, with the cut mark, as any text from data (Text, not Ref)", async () => {
    const member = `m_${"x".repeat(2500)}`;
    const shown = await render(<List hit={{ domain_tags: [{ data: boxText(member) }], facets: boxMap([]) }} at="domain_tags" />);
    expect(shown.container.querySelector(".cut")).not.toBeNull();
    expect(shown.container.textContent).not.toContain(member);
    expect(shown.container.textContent).toContain(member.slice(0, 2000));
    shown.unmount();
  });

  // Each sweep renders every ServerText position (a tag, a facet's key and its values, a cut list's member) under every
  // hostile row; they grow with the corpus, so they have the shared forms' timeout.
  it("shows a hostile text in every position of D312's lists as text alone, by construction", { timeout: 60_000 }, async () => {
    const OWN = ["DIV", "SPAN"];
    const CLASSES = ["list", ""];
    const hit = { domain_tags: [{ data: boxText("t1") }, { data: boxText("t2") }], facets: boxMap([[boxText("f.one"), [{ data: boxText("v") }]]]) };
    // Every prop is in the swept value: `at`'s facet key is a leaf too (the key `List` finds a facet by, not one it shows).
    for (const at of ["domain_tags", { facet: boxText("f.one") }] as const) {
      const found = await leafSweep({ hit, at }, (props) => <List hit={props.hit} at={props.at} />, OWN, CLASSES);
      expect(found.problems).toEqual([]);
      expect(found.leaves).toBeGreaterThanOrEqual(4);
    }
    const cut = await leafSweep([{ id: boxText("t") }], (members) => <ServerList members={members} each={(one) => <Ref value={one.id} />} leftOut={hitOf(0).tables_left_out} />, OWN, CLASSES);
    expect(cut.problems).toEqual([]);
    expect(cut.leaves).toBeGreaterThan(0);
  });
});
