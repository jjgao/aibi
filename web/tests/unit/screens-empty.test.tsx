/**
 * An empty list of the server is said, never shown as blank (round 1 of #123, m6: missing is not
 * negative). The cells are generated from the list positions of the two answers: every array position
 * the table reaches (outside `x-aibi-json` interiors) is either one of `WORDS`, a position whose empty
 * list the screens show in fixed words (a cell each: the orchard's answer with that list emptied at
 * every place it occurs shows the words once for each, and the answer as it is shows none), or one of
 * `OTHERWISE`, named with the reason an empty list there is not a blank claim (it is a heading's list
 * omitted with its heading, a list whose component has words of its own, or a descriptor member not
 * shown); a list position of neither fails the coverage cell, so a list added to an answer is placed
 * here.
 */
import type { ReactElement } from "react";
import { describe, expect, it } from "vitest";

import { type Shape, SHAPES } from "../../src/api/generated/shapes";
import { EMPTY, Hits, NO_TABLE as NO_HIT_TABLE, NO_VALUE } from "../../src/catalogue/Catalogue";
import { Description, NO_ANALYSIS } from "../../src/dataset/Dataset";
import { NO_RELATIONSHIP, NO_TABLE as NO_GRAPH_TABLE } from "../../src/dataset/Graph";
import { NO_COLUMN, NO_TABLES } from "../../src/dataset/Tables";
import { walked } from "./answers";
import { render } from "./render";
import { notShownProblems, orchardText } from "./screens";
import { REF } from "./typed-support";

/** A list position's cell: the screen, the position, the words, and what is set beside the emptied list. */
interface Cell {
  readonly component: "CatalogHits" | "DatasetDescription";
  readonly position: string;
  readonly words: string;
  /** Members of the object holding the list, set when it is emptied (a count the server gives with it). */
  readonly beside?: Readonly<Record<string, unknown>>;
}

const WORDS: readonly Cell[] = [
  { component: "CatalogHits", position: "/hits", words: EMPTY },
  { component: "CatalogHits", position: "/hits/[]/tables", words: NO_HIT_TABLE, beside: { tables_left_out: 0 } },
  { component: "CatalogHits", position: "/hits/[]/facets/{}", words: NO_VALUE },
  { component: "DatasetDescription", position: "/applicable_analyses", words: NO_ANALYSIS },
  { component: "DatasetDescription", position: "/graph/tables", words: NO_GRAPH_TABLE },
  { component: "DatasetDescription", position: "/graph/edges", words: NO_RELATIONSHIP },
  { component: "DatasetDescription", position: "/tables", words: NO_TABLES },
  { component: "DatasetDescription", position: "/tables/[]/columns", words: NO_COLUMN },
];

/** The list positions whose emptiness is not a blank claim, each with its reason. */
const OTHERWISE: Readonly<Record<string, string>> = {
  "CatalogHits/caveats": "Caveats: a list of notes about the answer, shown when there are some (R21)",
  "CatalogHits/hits/[]/domain_tags": "omitted with its heading when empty (D311)",
  "CatalogHits/hits/[]/data_use": "omitted with its heading when empty (D311)",
  "CatalogHits/hits/[]/packs": "omitted with its heading when empty (D311)",
  "CatalogHits/hits/[]/concepts": "omitted with its heading when empty (D311)",
  "DatasetDescription/caveats": "Caveats: a list of notes about the answer, shown when there are some (R21)",
  "DatasetDescription/applicable_analyses/[]/missing": "listed whenever not empty, as R45 words it (an analysis with none missing says nothing of it)",
  "DatasetDescription/applicable_analyses/[]/unconfirmed": "listed whenever not empty, as R45 words it (an analysis with none unconfirmed says nothing of it)",
  "DatasetDescription/relationships": "descriptor members, not shown (T8's words)",
  "DatasetDescription/coverage": "descriptor members, not shown (T8's words)",
  "DatasetDescription/endpoints": "descriptor members, not shown (T8's words)",
};

/** A shape with its references followed. */
function resolved(shape: Shape): Shape {
  let found = shape;
  for (let depth = 0; found.t === "ref" && depth < 50; depth += 1) {
    const next = SHAPES[found.n];
    if (next === undefined) {
      return found;
    }
    found = next;
  }
  return found;
}

/** Every array position a shape reaches, as a path (`[]` an item, `{}` a map's value), in the
 * descriptors' `x-aibi-json` interiors none, and in a segment list's items none (the shared components'
 * own lists: a caveat's affects and message, a refusal's). */
function arraysOf(shape: Shape, at: string, out: Set<string>, depth = 0): Set<string> {
  if (depth > 40) {
    return out;
  }
  const found = resolved(shape);
  switch (found.t) {
    case "arr":
      out.add(at);
      arraysOf(found.i, `${at}/[]`, out, depth + 1);
      break;
    case "rec":
      for (const [name, member] of Object.entries(found.p)) {
        arraysOf(member, `${at}/${name}`, out, depth + 1);
      }
      break;
    case "map":
      for (const value of [found.v, ...(found.pp ?? []).map(([, entry]) => entry)]) {
        arraysOf(value, `${at}/{}`, out, depth + 1);
      }
      break;
    case "or":
    case "all":
      for (const arm of found.o) {
        arraysOf(arm, at, out, depth + 1);
      }
      break;
    default:
      break;
  }
  return out;
}

/** Whether a position is inside a caveat (its own component's lists: `affects`, `message`). */
const inCaveat = (position: string): boolean => /\/caveats\/\[\]\//u.test(position);

/** `value` with the lists at `position` emptied (`beside` set in the object holding each), and how
 * many it emptied that held a member. */
function emptied(value: unknown, tokens: readonly string[], beside: Readonly<Record<string, unknown>>): { readonly value: unknown; readonly lists: number } {
  const [head, ...rest] = tokens;
  if (head === undefined) {
    return { value: [], lists: Array.isArray(value) && value.length > 0 ? 1 : 0 };
  }
  if (head === "[]") {
    const items = (value as unknown[]).map((item) => emptied(item, rest, beside));
    return { value: items.map((item) => item.value), lists: items.reduce((sum, item) => sum + item.lists, 0) };
  }
  const object = value as Record<string, unknown>;
  if (head === "{}") {
    const entries = Object.entries(object).map(([key, member]) => [key, emptied(member, rest, beside)] as const);
    return { value: Object.fromEntries(entries.map(([key, one]) => [key, one.value])), lists: entries.reduce((sum, [, one]) => sum + one.lists, 0) };
  }
  const inner = emptied(object[head], rest, beside);
  return { value: { ...object, [head]: inner.value, ...(rest.length === 0 ? beside : {}) }, lists: inner.lists };
}

/** The orchard's answer the screen shows (the first of its operation), as text; the catalogue's hits
 * given a pack's facet, which the orchard (no pack) has none of. */
const TEXT = {
  CatalogHits: JSON.stringify(
    ((answer: { hits: Record<string, unknown>[] }) => ({ ...answer, hits: answer.hits.map((hit) => ({ ...hit, facets: { "demo.genre": [{ data: "fiction" }] } })) }))(JSON.parse(orchardText("search_catalog", 0)) as { hits: Record<string, unknown>[] }),
  ),
  DatasetDescription: orchardText("describe_dataset", 0),
} as const;

const MAKE: Readonly<Record<Cell["component"], (value: never) => ReactElement>> = {
  CatalogHits: (value) => <Hits answer={value} />,
  DatasetDescription: (value) => <Description answer={value} />,
};

const count = (text: string, words: string): number => text.split(words).length - 1;

describe("an empty list of the server is said in words", () => {
  it.each(WORDS)("$component $position: the words appear once for each list emptied, and nowhere in the answer as it is", async ({ component, position, words, beside }) => {
    const whole = JSON.parse(TEXT[component]) as unknown;
    const shown = await render(MAKE[component](walked(component, TEXT[component]) as never));
    expect(count(shown.container.textContent, words)).toBe(0);
    if (component === "DatasetDescription") {
      expect(notShownProblems(shown.container, "orchard")).toEqual([]);
    }
    shown.unmount();
    const made = emptied(whole, position.slice(1).split("/"), beside ?? {});
    expect(made.lists, "the answer has a list there to empty").toBeGreaterThan(0);
    const blank = await render(MAKE[component](walked(component, JSON.stringify(made.value)) as never));
    expect(count(blank.container.textContent, words)).toBe(made.lists);
    if (component === "DatasetDescription") {
      expect(notShownProblems(blank.container, "orchard")).toEqual([]);
    }
    blank.unmount();
  });

  it("a hit whose tables the server left out says that, never that it has no table", async () => {
    const made = emptied(JSON.parse(TEXT.CatalogHits) as unknown, ["hits", "[]", "tables"], { tables_left_out: 3 });
    const shown = await render(MAKE.CatalogHits(walked("CatalogHits", JSON.stringify(made.value)) as never));
    expect(count(shown.container.textContent, NO_HIT_TABLE)).toBe(0);
    // None sent: the whole words, with no separator before "and" (the ", " belongs to a list with members).
    expect(count(shown.container.textContent, "Tables: and 3 more, left out by the server")).toBe(made.lists);
    expect(count(shown.container.textContent, ", and 3 more")).toBe(0);
    shown.unmount();
  });

  it("a hit whose tables the server sent and left some out says the members, then the rest after a comma", async () => {
    const whole = JSON.parse(TEXT.CatalogHits) as { hits: { tables: unknown[] }[] };
    const given = whole.hits.filter((hit) => hit.tables.length > 0).length;
    expect(given, "the answer has a hit with tables sent").toBeGreaterThan(0);
    const made = { ...whole, hits: whole.hits.map((hit) => ({ ...hit, tables_left_out: 3 })) };
    const shown = await render(MAKE.CatalogHits(walked("CatalogHits", JSON.stringify(made)) as never));
    expect(count(shown.container.textContent, ", and 3 more, left out by the server")).toBe(given);
    expect(count(shown.container.textContent, "Tables: and 3 more")).toBe(0);
    shown.unmount();
  });

  it.each(["CatalogHits", "DatasetDescription"] as const)("%s: every list position of the answer is a cell above or named otherwise", (component) => {
    const found = [...arraysOf(REF(component), "", new Set())].filter((position) => !inCaveat(position));
    const cells = WORDS.filter((cell) => cell.component === component).map((cell) => cell.position);
    const named = Object.keys(OTHERWISE)
      .filter((key) => key.startsWith(`${component}/`))
      .map((key) => key.slice(component.length));
    expect(found.filter((position) => !cells.includes(position) && !named.includes(position))).toEqual([]);
    expect([...cells, ...named].filter((position) => !found.includes(position))).toEqual([]);
  });
});
