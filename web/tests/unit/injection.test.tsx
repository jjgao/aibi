/**
 * The injection harness's own tests (D430, m8 of #121 round 1, MA-2 of round 2 and MA-1r3 of round 3):
 * `leafSweep`, at each of nine kinds of position (a list's member, a nested one, a `BoxedMap`'s key and
 * value, a native `Map`'s, a `Set`'s, a symbol key, a non-enumerable property), finds at that leaf and
 * no other (1) a sentinel control, shown only when the leaf is the text of a hostile row, at every
 * row (so a harness that replaces a leaf by anything else fails), and (2) a leaf rendered truly raw
 * (`{readText(leaf)}`), at the eight rows whose text has a code point React does not escape (the seven
 * rows with no unsafe code point are not found so: markup such as `<img>`, `<script>` and the isolate's end tag is
 * escaped by React, and `javascript:` is plain text); the leaves
 * it finds are the independent count's, written out here by construction; it renders every leaf under
 * every row.
 */
import { describe, expect, it } from "vitest";

import { boxMap, boxText, entriesOf, readText, type ServerText } from "../../src/api/box";
import { Text } from "../../src/layout/Text";
import { countTexts, HOSTILE, leafSweep, leavesOf } from "./injection";

const t = boxText;
const SYMBOL = Symbol("hidden by name");

/** A value and its one target leaf: the path the sweep names it by, how a component reads it, and how
 * many leaves the value holds (written out where the value is built). */
interface Case {
  readonly name: string;
  readonly value: Record<string | symbol, unknown>;
  readonly path: string;
  readonly leaves: number;
  readonly read: (value: Record<string | symbol, unknown>) => ServerText;
}

function hidden(): Record<string, unknown> {
  const object: Record<string, unknown> = { shown: t("shown") };
  Object.defineProperty(object, "hidden", { value: t("hidden"), enumerable: false });
  return object;
}

const CASES: readonly Case[] = [
  { name: "a list's last member", value: { list: [t("a0"), t("a1"), t("a2")] }, path: "list/2", leaves: 3, read: (v) => (v["list"] as ServerText[])[2] as ServerText },
  { name: "a nested object's member", value: { outer: { inner: [{ id: t("x") }, { id: t("y") }] } }, path: "outer/inner/1/id", leaves: 2, read: (v) => ((v["outer"] as { inner: { id: ServerText }[] }).inner[1] as { id: ServerText }).id },
  { name: "a BoxedMap's second key", value: { map: boxMap([[t("k0"), t("v0")], [t("k1"), t("v1")]]) }, path: "map/#key1", leaves: 4, read: (v) => entriesOf(v["map"] as ReturnType<typeof boxMap<ServerText>>)[1]?.[0] as ServerText },
  { name: "a BoxedMap's second value", value: { map: boxMap([[t("k0"), t("v0")], [t("k1"), t("v1")]]) }, path: "map/#val1", leaves: 4, read: (v) => entriesOf(v["map"] as ReturnType<typeof boxMap<ServerText>>)[1]?.[1] as ServerText },
  { name: "a native Map's second key", value: { map: new Map([[t("k0"), t("v0")], [t("k1"), t("v1")]]) }, path: "map/#mkey1", leaves: 4, read: (v) => [...(v["map"] as Map<ServerText, ServerText>).keys()][1] as ServerText },
  { name: "a native Map's second value", value: { map: new Map([[t("k0"), t("v0")], [t("k1"), t("v1")]]) }, path: "map/#mval1", leaves: 4, read: (v) => [...(v["map"] as Map<ServerText, ServerText>).values()][1] as ServerText },
  { name: "a Set's third member", value: { set: new Set([t("s0"), t("s1"), t("s2")]) }, path: "set/#set2", leaves: 3, read: (v) => [...(v["set"] as Set<ServerText>)][2] as ServerText },
  { name: "a symbol key", value: { shown: t("shown"), [SYMBOL]: t("symbol") }, path: "@sym1", leaves: 2, read: (v) => v[SYMBOL] as ServerText },
  { name: "a non-enumerable property", value: hidden(), path: "hidden", leaves: 2, read: (v) => v["hidden"] as ServerText },
];

/** The rows a leaf rendered truly raw (`{readText(leaf)}` as a string child) is found at: those whose
 * text has a code point React does not escape (markup is escaped by React: the `<img>`, `<script>`
 * and `javascript:` rows can never be found so), each with the first unsafe code point found. Written
 * out here, not computed by the harness's own `isUnsafe`. */
const UNESCAPED: readonly (readonly [string, string])[] = [
  ["a right-to-left override", "202e"],
  ["every bidi control", "202a"],
  ["a lone high surrogate", "d800"],
  ["a lone low surrogate", "dc00"],
  ["5,000 overrides", "202e"],
  ["controls, separators and a CRLF", "0"],
  ["invisible format characters", "ad"],
  ["a subdivision flag (tag characters, escaped by D312)", "e0067"],
];

/** A component that shows its target leaf as it should and, when the leaf is the text of a row of the
 * hostile corpus (so that the replacement reached the render, not a stand-in of its own), an element
 * no position of the visible form makes: the sentinel control, found at every row. */
function sentinel(read: Case["read"]) {
  return (value: Case["value"]) => {
    const leaf = read(value);
    return (
      <div>
        <Text value={leaf} />
        {HOSTILE.some((row) => row.text === readText(leaf)) ? <b>raw</b> : null}
      </div>
    );
  };
}

/** A component that renders its target leaf truly raw, as a string child. */
function rawly(read: Case["read"]) {
  return (value: Case["value"]) => <div>{readText(read(value))}</div>;
}

describe("leafSweep, over a component that renders one leaf raw", () => {
  it.each(CASES)("finds the hostile text of $name at every row, at that leaf and at no other (the sentinel control)", { timeout: 60_000 }, async ({ value, path, leaves, read }) => {
    const found = await leafSweep(value, sentinel(read), ["DIV"]);
    expect(found.problems).toEqual(HOSTILE.map((row) => `@/${path}: ${row.name}: a <b> element`));
    expect(found.leaves).toBe(leaves);
    expect(found.renders).toBe(leaves * HOSTILE.length);
    expect(countTexts(value)).toBe(leaves);
    expect(leavesOf(value)).toHaveLength(leaves);
  });

  it.each(CASES)("finds $name rendered truly raw at the rows React does not escape, at that leaf and at no other", { timeout: 60_000 }, async ({ value, path, leaves, read }) => {
    const found = await leafSweep(value, rawly(read), ["DIV"]);
    expect(found.problems).toEqual(UNESCAPED.map(([name, point]) => `@/${path}: ${name}: U+${point} raw`));
    expect(UNESCAPED.every(([name]) => HOSTILE.some((row) => row.name === name))).toBe(true);
    expect(found.renders).toBe(leaves * HOSTILE.length);
  });

  it.each(CASES)("passes a component that shows $name as text alone, rendering every leaf under every row", { timeout: 60_000 }, async ({ value, leaves, read }) => {
    const found = await leafSweep(value, (one) => <Text value={read(one)} />);
    expect(found.problems).toEqual([]);
    expect(found.renders).toBe(leaves * HOSTILE.length);
  });
});

describe("the sweep's counts", () => {
  it("count no box of a number, no plain string and no accessor's result", () => {
    const value = { text: "plain", number: 3, nothing: null, get computed() { return t("an accessor is not called"); }, ok: t("ok") };
    expect(countTexts(value)).toBe(1);
    expect(leavesOf(value)).toEqual([["ok"]]);
  });

  it("refuse a cycle rather than walk it for ever", () => {
    const cycle: Record<string, unknown> = {};
    cycle["self"] = cycle;
    expect(() => leavesOf(cycle)).toThrow("a cycle at @/self");
  });
});
