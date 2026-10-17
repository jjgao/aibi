/**
 * `Caveats` and `Inspection` (D430; D427's R21–R24 and R36, §8.3). The corpus is the document's own:
 * every core caveat code with the severity the schema declares for it (`schemas/openapi.json`'s
 * `Caveat`, its `if`/`then` pairs), a pack's code, and a severity the page does not know; each walked
 * from JSON as an answer's caveat is.
 */
import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { VOCABULARIES } from "../../src/api/generated/shapes";
import { blocks, type BoxedCaveat, Caveats, FOR_INSPECTION, Inspection, SEVERITY_WORDS } from "../../src/layout/Caveat";
import { walked } from "./answers";
import { leafSweep, problemsOf } from "./injection";
import { render } from "./render";

const DOCUMENT = JSON.parse(readFileSync(path.join(import.meta.dirname, "../../../schemas/openapi.json"), "utf8")) as {
  components: { schemas: { Caveat: { allOf: { if: { properties: { code: { const: string } } }; then: { properties: { severity: { const: string } } } }[] } } };
};

/** Each core code with its declared severity, in the document's order. */
const DECLARED: readonly (readonly [string, string])[] = DOCUMENT.components.schemas.Caveat.allOf.map((rule) => [rule.if.properties.code.const, rule.then.properties.severity.const] as const);

/** A caveat's JSON. */
const caveatJson = (code: string, severity: string, message = `the ${code} caveat`, affects: readonly string[] = []): string => JSON.stringify({ code, severity, message: [{ text: `${message}: ` }, { data: `${code} data` }], affects });

const caveat = (code: string, severity: string, message?: string, affects?: readonly string[]): BoxedCaveat => walked("Caveat", caveatJson(code, severity, message, affects)) as BoxedCaveat;

/** The rendered caveats' items, each its words. */
async function itemsOf(caveats: readonly BoxedCaveat[]): Promise<{ readonly items: string[]; readonly classes: string[]; readonly html: string }> {
  const shown = await render(<Caveats caveats={caveats} />);
  const items = [...shown.container.querySelectorAll('[role="listitem"]')];
  const found = { items: items.map((item) => item.textContent), classes: items.map((item) => item.className), html: shown.container.innerHTML };
  shown.unmount();
  return found;
}

/** The corpora of the rows: R21's (every warn and block caveat, a pack's too), R22's (one that blocks, and three that do not), R24's (info among warn) and R36's (reversed, with repeats). */
const R21: readonly BoxedCaveat[] = [...DECLARED.filter(([, severity]) => severity !== "info"), ["onco.CALLER_UNVALIDATED", "warn"] as const, ["onco.PANEL_GAP", "block"] as const].map(([code, severity]) => caveat(code, severity));
const R22_BLOCKED: readonly BoxedCaveat[] = [caveat("SMALL_N", "warn"), caveat("TIME_ORIGIN_MISMATCH", "block")];
const R22_PLAIN: readonly (readonly BoxedCaveat[])[] = [[], [caveat("SMALL_N", "warn")], DECLARED.filter(([, severity]) => severity !== "block").map(([code, severity]) => caveat(code, severity))];
const R24_INFO = DECLARED.filter(([, severity]) => severity === "info");
const R24: readonly BoxedCaveat[] = [caveat("SMALL_N", "warn"), ...R24_INFO.map(([code, severity]) => caveat(code, severity, undefined, ["/values/0/estimate"])), caveat("DRAFT_RELEASE", "warn")];
const R36: readonly BoxedCaveat[] = [...[...DECLARED].reverse(), ...[...DECLARED].reverse().slice(0, 3)].map(([code, severity]) => caveat(code, severity));

/** The list items of an element, each its markup. */
async function listitemsOf(element: React.ReactElement): Promise<{ readonly items: string[]; readonly parents: (string | null)[]; readonly section: string | null }> {
  const shown = await render(element);
  const items = [...shown.container.querySelectorAll('[role="listitem"]')];
  const found = { items: items.map((item) => item.outerHTML), parents: items.map((item) => item.parentElement?.getAttribute("role") ?? null), section: shown.container.querySelector("section")?.className ?? null };
  shown.unmount();
  return found;
}

describe("Caveats", () => {
  it("R21: shows every warn and block caveat, core and pack codes alike, with its code, its severity and its message", async () => {
    expect.hasAssertions();
    expect(DECLARED.map(([code]) => code)).toEqual(VOCABULARIES["Caveat.code"]);
    const shown = [...DECLARED.filter(([, severity]) => severity !== "info"), ["onco.CALLER_UNVALIDATED", "warn"] as const, ["onco.PANEL_GAP", "block"] as const];
    const { items } = await itemsOf(R21);
    expect(items).toEqual(shown.map(([code, severity]) => `${code} (${severity === "warn" ? "warning" : "block"}): the ${code} caveat: ${code} data`));
  });

  it("M1: an Inspection contains its parts: its list items are Caveats' own, in order, in both of its branches, over the corpora of R21, R22, R24 and R36", async () => {
    expect.hasAssertions();
    const BLOCK = caveat("onco.PANEL_GAP", "block");
    for (const corpus of [R21, R22_BLOCKED, ...R22_PLAIN, R24, R36]) {
      const plain = corpus.filter((one) => !blocks([one]));
      for (const [caveats, section] of [
        [plain, "output"],
        [[...plain, BLOCK], "inspection"],
        [corpus, blocks(corpus) ? "inspection" : "output"],
      ] as const) {
        const parts = await listitemsOf(<Caveats caveats={caveats} />);
        const whole = await listitemsOf(
          <Inspection caveats={caveats}>
            <p className="figure">the output</p>
          </Inspection>,
        );
        expect(parts.items.length).toBe(caveats.length);
        expect(parts.parents).toEqual(caveats.map(() => "list"));
        expect(whole.items).toEqual(parts.items);
        expect(whole.section).toBe(section);
      }
    }
  });

  it("R22: puts an output with a block caveat under a banner, for inspection, never presented as an answer; one without is shown as it is", async () => {
    expect.hasAssertions();
    const blocked = await render(
      <Inspection caveats={R22_BLOCKED}>
        <p className="figure">the output</p>
      </Inspection>,
    );
    const section = blocked.container.querySelector("section.inspection");
    expect(section?.getAttribute("aria-label")).toBe("for inspection, not an answer");
    expect(section?.firstElementChild?.textContent).toBe(FOR_INSPECTION);
    expect(section?.firstElementChild?.getAttribute("role")).toBe("alert");
    expect(section?.querySelector("p.figure")?.textContent).toBe("the output");
    expect(blocked.container.querySelector("section.output")).toBeNull();
    blocked.unmount();
    for (const caveats of R22_PLAIN) {
      const plain = await render(
        <Inspection caveats={caveats}>
          <p className="figure">the output</p>
        </Inspection>,
      );
      expect(plain.container.textContent).not.toContain(FOR_INSPECTION);
      expect(plain.container.querySelector("section.inspection")).toBeNull();
      expect(plain.container.querySelector("section.output p.figure")?.textContent).toBe("the output");
      plain.unmount();
    }
  });

  it("R23: reads a severity the page does not know as block, with the banner", async () => {
    expect.hasAssertions();
    for (const severity of ["fatal", "BLOCK", "Block", "warn ", "", "info‮", "<img src=x onerror=alert(1)>"]) {
      const odd = caveat("onco.NEW_RULE", severity);
      expect(blocks([odd])).toBe(true);
      const { items, classes } = await itemsOf([odd]);
      expect(items).toEqual(["onco.NEW_RULE (block): the onco.NEW_RULE caveat: onco.NEW_RULE data"]);
      expect(classes).toEqual(["caveat block"]);
      const wrapped = await render(
        <Inspection caveats={[odd]}>
          <p>x</p>
        </Inspection>,
      );
      expect(wrapped.container.textContent).toContain(FOR_INSPECTION);
      wrapped.unmount();
    }
  });

  it("R24: shows the info caveats too, secondary, in place", async () => {
    expect.hasAssertions();
    const info = R24_INFO;
    expect(info.map(([code]) => code)).toEqual(["SUPPRESSED", "NOT_ESTIMABLE", "LIFT_DIFFERS", "POOLED_ACROSS_DATASETS"]);
    const mixed = R24;
    const { items, classes } = await itemsOf(mixed);
    expect(classes).toEqual(["caveat warn", "caveat info", "caveat info", "caveat info", "caveat info", "caveat warn"]);
    expect(items.slice(1, 5)).toEqual(info.map(([code]) => `${code} (note): the ${code} caveat: ${code} data affects: /values/0/estimate`));
    expect(SEVERITY_WORDS).toEqual({ info: "note", warn: "warning", block: "block" });
    expect(blocks(mixed)).toBe(false);
  });

  it("R36: shows the caveats in the server's order, never sorted again, merged or dropped", async () => {
    expect.hasAssertions();
    const order = [...DECLARED].reverse();
    const twice = [...order, ...order.slice(0, 3)];
    const { items } = await itemsOf(R36);
    expect(items.map((item) => item.split(" ")[0])).toEqual(twice.map(([code]) => code));
    expect(items.length).toBe(DECLARED.length + 3);
  });

  it("shows nothing for no caveats", async () => {
    expect((await itemsOf([])).html).toBe("");
  });

  it("the harness's own span classes admit those named alone (its own control)", async () => {
    const shown = await render(
      <p>
        <span className="affects">a</span>
        <span className="other">b</span>
      </p>,
    );
    expect(problemsOf(shown.container, { name: "control", text: "x", raw: false }, ["P"], ["affects"])).toEqual(["control: a span of class other"]);
    shown.unmount();
  });

  it("shows an affected pointer whole, never cut, however long (D428 m16: a pointer is bounded by the schema, not the page; m4 of #121 round 1)", async () => {
    const pointer = `/${"p".repeat(2500)}`;
    const shown = await render(<Caveats caveats={[caveat("SMALL_N", "warn", undefined, [pointer, `${pointer}/q`])]} />);
    expect(shown.container.textContent).toContain(`affects: ${pointer}, ${pointer}/q`);
    expect(shown.container.querySelector(".cut")).toBeNull();
    shown.unmount();
  });

  // Each sweep renders every position (the code, the severity, a message's text and data segments, each affected pointer)
  // under every hostile row; they grow with the corpus, so they have the shared forms' timeout.
  it.each([
    ["warn", "Caveats (the output branch of Inspection)"],
    ["block", "Caveats (the banner branch of Inspection)"],
  ])("shows a hostile text in every position of a %s caveat as text alone, by construction: %s", { timeout: 60_000 }, async (severity) => {
    const one = walked("Caveat", JSON.stringify({ code: "onco.X", severity, message: [{ text: "w " }, { data: "d" }], affects: ["/a", "/b"] })) as BoxedCaveat;
    const OWN = ["DIV", "SECTION", "P"];
    const CLASSES = ["affects"];
    const alone = await leafSweep([one], (caveats) => <Caveats caveats={caveats} />, OWN, CLASSES);
    expect(alone.problems).toEqual([]);
    expect(alone.leaves).toBeGreaterThan(0);
    const wrapped = await leafSweep(
      [one],
      (caveats) => (
        <Inspection caveats={caveats}>
          <p>x</p>
        </Inspection>
      ),
      OWN,
      CLASSES,
    );
    expect(wrapped.problems).toEqual([]);
    expect(wrapped.leaves).toBe(alone.leaves);
  });
});
