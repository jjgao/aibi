/**
 * The components that open a box (D428): `Text`, `Ref`, `Segments` and `Option` render the visible
 * form through `showText` alone; the injection harness (`injection.tsx`) over each of their
 * positions; `Link`, `Field` and `keyOf`.
 */
import { act, type ReactElement } from "react";
import { describe, expect, it } from "vitest";

import { boxText } from "../../src/api/box";
import type { BoxedSegment } from "../../src/api/typed";
import { idOfAddress, showText } from "../../src/api/text";
import { Field, type Typed, UNTYPED } from "../../src/layout/Field";
import { type Form, FORM } from "../../src/layout/form";
import { keyOf, NO_INDEX } from "../../src/layout/key";
import { Link } from "../../src/layout/Link";
import { NO_OPTION, NoOption, Option } from "../../src/layout/Option";
import { Segments } from "../../src/layout/Segments";
import { Ref, Text } from "../../src/layout/Text";
import { injectionProblems } from "./injection";
import { render } from "./render";

const data = (text: string, truncated = false): BoxedSegment => ({ kind: "data", data: boxText(text), truncated });
const words = (text: string): BoxedSegment => ({ kind: "text", text: boxText(text) });

describe("the injection harness over every position that opens a box", () => {
  it.each([
    ["Text", (box: ReturnType<typeof boxText>) => <Text value={box} />, []],
    ["Ref", (box: ReturnType<typeof boxText>) => <Ref value={box} />, []],
    ["a data token of Segments", (box: ReturnType<typeof boxText>) => <Segments value={[{ kind: "data", data: box, truncated: true }]} />, []],
    ["a text segment of Segments", (box: ReturnType<typeof boxText>) => <Segments value={[{ kind: "text", text: box }]} />, []],
    [
      "Option",
      (box: ReturnType<typeof boxText>) => (
        <select aria-label="list">
          <Option label={box} at={keyOf(0)} />
        </select>
      ),
      ["SELECT", "OPTION"],
    ],
  ] as const)("%s: text alone, no element the text spelled, no raw unsafe code point, what D312 leaves raw raw", async (_name, position, own) => {
    expect(await injectionProblems(position, own)).toEqual([]);
  });

  it("would see a position that renders a box's text raw (the harness's own control)", async () => {
    expect(await injectionProblems(() => <span className="escape">{"\u202e"}</span>, [], [{ name: "control", text: "x", raw: false }])).toEqual(["control: U+202e raw"]);
    expect(await injectionProblems(() => <b>x</b>, [], [{ name: "control", text: "x", raw: false }])).toEqual(["control: a <b> element"]);
    expect(await injectionProblems(() => <Text value={boxText("\u034f")} />, [], [{ name: "control", text: "\u3164", raw: true }])).toEqual(["control: U+3164 not raw"]);
  });
});

describe("Text and Ref", () => {
  it("R01: isolates a text from data in its own bdi, escapes each run in one span, and cuts at 2,000 code points with a mark", async () => {
    expect.hasAssertions();
    const shown = await render(<Text value={boxText(`a\u202e\u202db${"x".repeat(3000)}`)} />);
    const bdi = shown.container.querySelector("bdi.data");
    expect(bdi).not.toBeNull();
    expect([...shown.container.querySelectorAll(".escape")].map((span) => span.textContent)).toEqual(["\\u202e\\u202d"]);
    expect(shown.container.querySelector(".cut")?.textContent).toBe("…");
    expect(shown.container.querySelector(".cut")?.getAttribute("title")).toBe("cut");
    expect(Array.from(bdi?.textContent ?? "").length).toBe(2000 - 2 + 12 + 1);
    shown.unmount();
  });

  it("shows a short text whole, with no cut mark", async () => {
    const shown = await render(<Text value={boxText("short")} />);
    expect(shown.container.innerHTML).toBe('<bdi class="data">short</bdi>');
    shown.unmount();
  });

  it("does not cut a pointer, a reference or a data token (Ref)", async () => {
    const shown = await render(<Ref value={boxText("y".repeat(5000))} />);
    expect(shown.container.textContent).toBe("y".repeat(5000));
    expect(shown.container.querySelector(".cut")).toBeNull();
    expect(shown.container.innerHTML.startsWith('<bdi class="data">')).toBe(true);
    shown.unmount();
  });
});

describe("Segments", () => {
  it("R02: shows the server's words escaped, with no bdi and no data mark, and each data token isolated and marked", async () => {
    expect.hasAssertions();
    const shown = await render(<Segments value={[words("No column is named "), data("The page could not reach the server."), words(" in\u202e this table.")]} />);
    expect(shown.container.innerHTML).toBe(
      'No column is named <bdi class="data token">The page could not reach the server.</bdi> in<span class="escape">\\u202e</span> this table.',
    );
    shown.unmount();
  });

  it("marks a token the server cut once, with the server's mark, never a second from the page's cut", async () => {
    const shown = await render(<Segments value={[data("z".repeat(2500), true)]} />);
    expect(shown.container.querySelectorAll(".cut")).toHaveLength(1);
    expect(shown.container.querySelector("bdi")?.textContent).toBe(`${"z".repeat(2500)}…`);
    shown.unmount();
  });

  it("puts the server's cut mark inside the data token's isolate, where Text puts the page's own: one placement", async () => {
    const token = await render(<Segments value={[data("t", true)]} />);
    expect(token.container.innerHTML).toBe('<bdi class="data token">t<span class="cut" title="cut">…</span></bdi>');
    token.unmount();
    const cut = await render(<Text value={boxText("t".repeat(2001))} />);
    expect(cut.container.innerHTML).toBe(`<bdi class="data">${"t".repeat(2000)}<span class="cut" title="cut">…</span></bdi>`);
    cut.unmount();
  });

  it("never cuts the server's words: a text segment of more than 2,000 code points is shown whole, with no cut mark (the server's page does not cut them)", async () => {
    const shown = await render(<Segments value={[words("w".repeat(5000))]} />);
    expect(shown.container.textContent).toBe("w".repeat(5000));
    expect(shown.container.querySelector(".cut")).toBeNull();
    shown.unmount();
  });
});

/** Each component, its output, and the one ancestor-free rendering it is held to. */
const COMPONENTS: readonly (readonly [string, (box: ReturnType<typeof boxText>) => ReactElement])[] = [
  ["Text", (box) => <Text value={box} />],
  ["Ref", (box) => <Ref value={box} />],
  ["a data token of Segments", (box) => <Segments value={[{ kind: "data", data: box, truncated: true }]} />],
  ["a text segment of Segments", (box) => <Segments value={[{ kind: "text", text: box }]} />],
  [
    "Option",
    (box) => (
      <select aria-label="list">
        <Option label={box} at={keyOf(0)} />
      </select>
    ),
  ],
];

/** A text with an override, a lone surrogate, a carriage return and a line feed, and enough more
 * to pass the cut. */
const LONG = `a\u202eb\ud800c\r\nd${"x".repeat(2500)}`;

describe("each component sets its own form: its output does not depend on an ancestor's (round 1 of #117)", () => {
  const ANCESTORS: readonly (Form | null)[] = [null, "cut", "whole", "flat"];

  it.each(COMPONENTS.flatMap(([name, element]) => ANCESTORS.map((form) => [name, element, form] as const)))("%s under the ancestor form %s", async (_name, element, form) => {
    const bare = await render(element(boxText(LONG)));
    const under = await render(form === null ? element(boxText(LONG)) : <FORM value={form}>{element(boxText(LONG))}</FORM>);
    expect(under.container.innerHTML).toBe(bare.container.innerHTML);
    bare.unmount();
    under.unmount();
  });

  it("each form is the component's own (the cut, the spans and the flat text differ between them)", async () => {
    const html = async (element: ReactElement): Promise<string> => {
      const shown = await render(element);
      const found = shown.container.innerHTML;
      shown.unmount();
      return found;
    };
    const shown: string[] = [];
    for (const [, element] of COMPONENTS) {
      shown.push(await html(element(boxText(LONG))));
    }
    const [text, ref, , segment, option] = shown;
    expect(text).toContain('<span class="escape">\\u202e</span>');
    expect(text).toContain('<span class="cut" title="cut">…</span>');
    expect(ref).toContain('<span class="escape">\\u202e</span>');
    expect(ref).not.toContain('class="cut"');
    expect(segment).not.toContain("<bdi");
    expect(segment).not.toContain('class="cut"');
    expect(option).not.toContain("<span");
    expect(option).toContain("a\\u202eb");
  });

  it("the ancestor does decide for the bare door (the matrix's own control: showText has no form of its own)", async () => {
    const html = async (form: Form): Promise<string> => {
      const shown = await render(<FORM value={form}>{showText(boxText(LONG))}</FORM>);
      const found = shown.container.innerHTML;
      shown.unmount();
      return found;
    };
    expect(new Set([await html("cut"), await html("whole"), await html("flat")]).size).toBe(3);
  });

  it("shows a real U+202E and the six characters that spell it apart, in every component but Option's (a marked escape cannot pass for data that spells one, D312)", async () => {
    for (const [name, element] of COMPONENTS.filter(([name]) => name !== "Option")) {
      const real = await render(<FORM value="flat">{element(boxText("a\u202eb"))}</FORM>);
      const spelled = await render(<FORM value="flat">{element(boxText("a\\u202eb"))}</FORM>);
      expect(real.container.innerHTML, name).not.toBe(spelled.container.innerHTML);
      real.unmount();
      spelled.unmount();
    }
  });

  it("shows a 5,000-character text under a whole ancestor cut and marked, in Text", async () => {
    const shown = await render(
      <FORM value="whole">
        <Text value={boxText("x".repeat(5000))} />
      </FORM>,
    );
    expect(Array.from(shown.container.textContent).length).toBe(2001);
    expect(shown.container.querySelector(".cut")).not.toBeNull();
    shown.unmount();
  });
});

describe("Option", () => {
  it("R07: holds the flat form: the escapes as literal text, no span, no raw unsafe code point; its value an index", async () => {
    expect.hasAssertions();
    const shown = await render(
      <select aria-label="list">
        <Option label={boxText("a\u202eb\r\nc")} at={keyOf(0)} />
        <Option label={boxText("q".repeat(2001))} at={keyOf(1)} />
      </select>,
    );
    const options = [...shown.container.querySelectorAll("option")];
    expect(options.map((option) => option.children.length)).toEqual([0, 0]);
    expect(options[0]?.textContent).toBe("a\\u202eb\nc");
    expect(options[0]?.value).toBe("0");
    expect(options[1]?.textContent).toBe(`${"q".repeat(2000)}…`);
    expect(options[1]?.value).toBe("1");
    expect(shown.container.textContent.includes("\u202e")).toBe(false);
    shown.unmount();
  });

  it("R07: NoOption chooses nothing: fixed words, its value empty, never an index", async () => {
    expect.hasAssertions();
    const shown = await render(
      <select aria-label="list" defaultValue="">
        <NoOption />
        <Option label={boxText("a")} at={keyOf(0)} />
      </select>,
    );
    const options = [...shown.container.querySelectorAll("option")];
    expect(options.map((option) => [option.textContent, option.value])).toEqual([
      [NO_OPTION, ""],
      ["a", "0"],
    ]);
    expect(NO_OPTION).toBe("(choose)");
    expect(shown.container.querySelector("select")?.selectedIndex).toBe(0);
    shown.unmount();
  });
});

describe("keyOf, Link and Field", () => {
  it("keyOf mints an index alone", () => {
    expect(keyOf(0)).toBe(0);
    expect(keyOf(41)).toBe(41);
    for (const refused of [-1, 0.5, Number.NaN, Infinity, 2 ** 53]) {
      expect(() => keyOf(refused)).toThrow(new RangeError(NO_INDEX));
    }
  });

  it("Link composes #/datasets/<id> from a DatasetId alone", async () => {
    const id = idOfAddress("orchard");
    expect(id).not.toBeNull();
    const shown = await render(id === null ? <p /> : <Link to={id}>Orchard</Link>);
    expect(shown.container.innerHTML).toBe('<a href="#/datasets/orchard">Orchard</a>');
    shown.unmount();
  });

  it("Field holds what the person typed, and hands each change on as a Typed", async () => {
    const typed: Typed[] = [];
    const shown = await render(
      <Field value={UNTYPED} onChange={(value) => typed.push(value)}>
        Name
      </Field>,
    );
    const input = shown.container.querySelector("input");
    expect(input?.value).toBe("");
    expect(input?.getAttribute("autocomplete")).toBe("off");
    act(() => {
      if (input !== null) {
        // The native setter, as a person's typing sets it (React's own tracker is then told apart).
        Reflect.set(HTMLInputElement.prototype, "value", "orchard", input);
        input.dispatchEvent(new Event("input", { bubbles: true }));
      }
    });
    expect(typed).toEqual(["orchard"]);
    shown.unmount();
  });
});
