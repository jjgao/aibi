/**
 * `Num`, `listOf` and `RetryAfter` (D430; D427's R04): a server number is shown by its exact
 * source text, read from the one decoder's box, for every text of JSON's number grammar the corpus
 * enumerates (`answers.ts`, with an oracle of its own); never rounded, never converted; a box whose
 * text is no number is not shown, in fixed words; the element holds the box, never the digits.
 */
import { act, isValidElement, type ReactElement, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { describe, expect, it } from "vitest";

import { box, boxMap, boxText, type ServerNumber } from "../../src/api/box";
import { decode } from "../../src/api/decode";
import { type Lists, listOf, NOT_READ, Num, type Refused, RetryAfter } from "../../src/api/format";
import { LISTED } from "../../src/api/numbers";
import type { Outcome } from "../../src/api/typed";
import { isNumberText, NOT_NUMBERS, NUMBER_TEXTS } from "./answers";
import { HOSTILE, problemsOf } from "./injection";
import { render } from "./render";

/** The box the one decoder makes of a number's text. */
function decoded(text: string): ServerNumber {
  const [found] = decode(`[${text}]`) as readonly ServerNumber[];
  if (found === undefined) {
    throw new Error("no number");
  }
  return found;
}

/** Every string an element holds in its props, children included, without rendering it. */
function stringsOf(node: ReactNode): string[] {
  if (typeof node === "string") {
    return [node];
  }
  if (Array.isArray(node)) {
    return node.flatMap((child: ReactNode) => stringsOf(child));
  }
  if (isValidElement<Record<string, unknown>>(node)) {
    return Object.values(node.props).flatMap((value) => stringsOf(value as ReactNode));
  }
  return [];
}

describe("Num", () => {
  it("R04: shows every number of JSON's grammar by its exact source text, from the decoder's box, never rounded or converted", async () => {
    expect.hasAssertions();
    expect(NUMBER_TEXTS.length).toBe(672);
    expect(NUMBER_TEXTS.filter((text) => !isNumberText(text))).toEqual([]);
    const boxes = NUMBER_TEXTS.map(decoded);
    const shown = await render(
      <div>
        {boxes.map((value, index) => (
          <p key={index}>
            <Num value={value} />
          </p>
        ))}
      </div>,
    );
    const texts = [...shown.container.querySelectorAll("p")].map((paragraph) => paragraph.textContent);
    expect(texts).toEqual(NUMBER_TEXTS);
    expect([...shown.container.querySelectorAll("bdi.number")].length).toBe(NUMBER_TEXTS.length);
    for (const [text, expected] of [
      ["1e-07", "1e-07"],
      ["-0.0", "-0.0"],
      ["1.10", "1.10"],
      ["9007199254740993", "9007199254740993"],
      ["1e-400", "1e-400"],
      ["0.000000000000000000001", "0.000000000000000000001"],
    ] as const) {
      const one = await render(<Num value={decoded(text)} />);
      expect(one.container.innerHTML).toBe(`<bdi class="number">${expected}</bdi>`);
      one.unmount();
    }
    shown.unmount();
  });

  it("shows a box whose text is no JSON number as not shown, in fixed words, never its text", async () => {
    expect(NOT_NUMBERS.filter((text) => isNumberText(text))).toEqual([]);
    for (const text of NOT_NUMBERS) {
      const shown = await render(<Num value={box(text)} />);
      expect(shown.container.textContent, JSON.stringify(text)).toBe(NOT_READ);
      expect(shown.container.querySelector("bdi")).toBeNull();
      shown.unmount();
    }
  });

  it("puts no element and no raw unsafe code point of a hostile text in the DOM (the injection corpus, forged into a box)", async () => {
    const found: string[] = [];
    for (const row of HOSTILE) {
      const shown = await render(<Num value={box(row.text)} />);
      found.push(...problemsOf(shown.container, row, [], ["number unread"]));
      expect(shown.container.textContent).toBe(NOT_READ);
      shown.unmount();
    }
    expect(found).toEqual([]);
  });

  it("holds the box in its element, never the digits: no string of the number in the element or what Num returns", () => {
    const value = decoded("12345");
    const element = <Num value={value} />;
    expect((element.props as { readonly value: unknown }).value).toBe(value);
    expect(stringsOf(element)).toEqual([]);
    const returned = Num({ value });
    expect(stringsOf(returned)).toEqual([]);
    expect(JSON.stringify(Object.keys(returned.props as object))).toBe('["value","children"]');
  });

  it("gives Digits no box outside a render: called as a function, it throws (D419's boundary: deliberate code)", () => {
    const returned = Num({ value: decoded("12345") }) as ReactElement<{ readonly children: ReactElement<object> }>;
    const digits = returned.props.children.type as (props: object) => ReactElement;
    expect(() => digits({})).toThrow();
    // the box is handed by the context alone: `Digits` is given no props, so passing it the box by props reads nothing
    expect(returned.props.children.props).toEqual({});
    expect(() => digits(returned.props.children.props)).toThrow();
  });

  it("a detached root and its textContent read the digits: the residual (deliberate code), closed in the app by D429's react-dom/client in the entries alone", async () => {
    const host = document.createElement("div");
    const root = createRoot(host);
    await act(async () => {
      root.render(<Num value={decoded("42")} />);
      await Promise.resolve();
    });
    expect(host.textContent).toBe("42");
    await act(async () => {
      root.unmount();
      await Promise.resolve();
    });
  });
});

/** A hit's lists: domain tags and facets of the given lengths. */
function listsOf(tags: number, facets: readonly (readonly [string, number])[] = []): Lists {
  const data = (count: number, prefix: string): readonly { readonly data: ReturnType<typeof boxText> }[] => Array.from({ length: count }, (_, index) => ({ data: boxText(`${prefix}${String(index)}`) }));
  return { domain_tags: data(tags, "tag"), facets: boxMap(facets.map(([key, count]) => [boxText(key), data(count, key)] as const)) };
}

/** The words of an element (or `null` for none). */
async function wordsOf(element: ReactElement | null): Promise<string | null> {
  if (element === null) {
    return null;
  }
  const shown = await render(<p>{element}</p>);
  const text = shown.container.textContent;
  shown.unmount();
  return text;
}

describe("listOf", () => {
  it.each([0, 1, LISTED - 1, LISTED, LISTED + 1, 100, 1000])("shows the first %i… members of the domain tags, and counts the rest (LISTED is the export's 64)", async (length) => {
    expect(LISTED).toBe(64);
    const { members, more } = listOf(listsOf(length), "domain_tags");
    expect(members.length).toBe(Math.min(length, LISTED));
    if (length <= LISTED) {
      expect(more).toBeNull();
      return;
    }
    const shown = await render(<p>{more}</p>);
    expect(shown.container.textContent).toBe(`and ${String(length - LISTED)} more`);
    shown.unmount();
  });

  it("finds one facet's values, named by its key (sameText), and none for a facet the hit does not have", async () => {
    const lists = listsOf(3, [
      ["tissue.site", 70],
      ["assay.kind", 200],
    ]);
    const kind = listOf(lists, { facet: boxText("assay.kind") });
    expect(kind.members.length).toBe(LISTED);
    const shown = await render(<p>{kind.more}</p>);
    expect(shown.container.textContent).toBe("and 136 more");
    shown.unmount();
    const site = listOf(lists, { facet: boxText("tissue.site") });
    expect(site.members.length).toBe(LISTED);
    const other = await render(<p>{site.more}</p>);
    expect(other.container.textContent).toBe("and 6 more");
    other.unmount();
    expect(listOf(lists, { facet: boxText("absent.facet") })).toEqual({ members: [], more: null });
    expect(listOf(lists, "domain_tags").more).toBeNull();
    expect(listOf(lists, "domain_tags").members.length).toBe(3);
  });

  it("gives the members and the remainder of the one list, whatever the lists' lengths differ by (m2 of #121 round 1)", async () => {
    const lengths = [0, 3, LISTED, LISTED + 1, 200];
    for (const tags of lengths) {
      for (const values of lengths) {
        const lists = listsOf(tags, [["f", values]]);
        for (const [at, length] of [
          ["domain_tags", tags],
          [{ facet: boxText("f") }, values],
        ] as const) {
          const { members, more } = listOf(lists, at);
          expect(members.length, `${String(tags)} tags, ${String(values)} values`).toBe(Math.min(length, LISTED));
          expect(await wordsOf(more)).toBe(length > LISTED ? `and ${String(length - LISTED)} more` : null);
        }
      }
    }
  });
});

/** A refused outcome with a `Retry-After` of `seconds`. */
function refusedWith(seconds: number | null): Refused {
  const outcome: Extract<Outcome<unknown>, { readonly kind: "refused" }> = { kind: "refused", status: 429, refusals: [], retryAfter: seconds };
  const named: Refused = outcome;
  const back: Extract<Outcome<unknown>, { readonly kind: "refused" }> = named;
  return back;
}

describe("RetryAfter", () => {
  it("states the server's Retry-After in whole seconds, and nothing without one", async () => {
    for (const seconds of [0, 1, 30, 9999999999]) {
      const shown = await render(<RetryAfter refused={refusedWith(seconds)} />);
      expect(shown.container.textContent).toBe(`Try again after ${String(seconds)} seconds (the server's Retry-After).`);
      shown.unmount();
    }
    const none = await render(<RetryAfter refused={refusedWith(null)} />);
    expect(none.container.innerHTML).toBe("");
    none.unmount();
  });

  it("refuses a count that is no whole number from 0, in fixed words (the client's parse gives none)", () => {
    for (const seconds of [-1, 1.5, Number.NaN, Number.POSITIVE_INFINITY, 2 ** 53]) {
      expect(() => RetryAfter({ refused: refusedWith(seconds) })).toThrow(RangeError);
    }
  });
});
