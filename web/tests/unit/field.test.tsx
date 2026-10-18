/**
 * `Field` and what the person types (D433; D427's R74; §7.1, A6, D432). What a person types into the
 * builder's field reaches the request as one value leaf's string value alone, as §7.1's literal,
 * never empty, and comes back only through its own field and the server's readback: driven here
 * through the real builder and the real seam, `fetch` stubbed.
 */
import { act, StrictMode } from "react";
import { describe, expect, it, vi } from "vitest";

import { Field, UNTYPED } from "../../src/layout/Field";
import { ANSWERS, type BuilderPage, builderPage, synthetic } from "./builder-support";

let page: BuilderPage | undefined;

/** Close the page and the stubs (this file names no hook: D427's gate). */
function close(): void {
  page?.close();
  page = undefined;
  vi.unstubAllGlobals();
}

const HOSTILE = ["$x", "$$", "<img src=x onerror=alert(1)>", "‮abc", "trees.variety", "1", "a\r\nb"];

describe("Field", () => {
  it("R74: what the person types is one value leaf's string value alone, sent as §7.1's literal, never empty, shown back only in its own field", async () => {
    expect.hasAssertions();
    try {
      for (const typed of HOSTILE) {
        // A synthetic answer: what is checked is the body the page sends, which the server never answered.
        page = await builderPage(synthetic(ANSWERS.validated));
        await page.choose("Unit table", "trees");
        await page.press("Add a condition");
        await page.choose("Column", "trees.variety", 0);
        const kept = typed.replace(/[\r\n]/gu, "");
        const before = page.container.textContent.split(kept).length;
        await page.type(0, typed);
        await page.choose("Column", "trees.height_m", 1);
        expect(page.inputs()[0]?.value).toBe(kept);
        // Shown back in its own field alone: the page's text holds it no more often than before it was typed.
        expect(page.container.textContent.split(kept).length).toBe(before);
        await page.press("Validate");
        const [sent] = page.sent;
        const document = (JSON.parse(sent?.body ?? "{}") as { document: { cohorts: { first: { all: { kind: string; column: string; values: string[] }[] } } } }).document;
        expect(document.cohorts.first.all).toEqual([{ kind: "value", column: "trees.variety", values: [kept.startsWith("$") ? `$${kept}` : kept] }]);
        // Never a key: no member name of the document is the typed value.
        const keys: string[] = [];
        JSON.stringify(document, (key: string, value: unknown) => {
          keys.push(key);
          return value;
        });
        expect(keys.includes(kept)).toBe(false);
        page.close();
        page = undefined;
      }
      page = await builderPage();
      await page.choose("Unit table", "trees");
      await page.press("Add a condition");
      await page.choose("Column", "trees.variety", 0);
      await page.choose("Column", "trees.height_m", 1);
      expect(page.button("Validate").disabled).toBe(true);
      await page.press("Validate");
      expect(page.sent).toEqual([]);
    } finally {
      close();
    }
  });

  it("is controlled: it shows the value it is given, and hands on what is typed", async () => {
    const seen: string[] = [];
    const { render } = await import("./render");
    const shown = await render(
      <StrictMode>
        <Field
          value={UNTYPED}
          onChange={(value) => {
            seen.push(value);
          }}
        >
          Value
        </Field>
      </StrictMode>,
    );
    const input = shown.container.querySelector("input");
    act(() => {
      if (input !== null) {
        Reflect.set(HTMLInputElement.prototype, "value", "pear", input);
        input.dispatchEvent(new Event("input", { bubbles: true }));
      }
    });
    expect(seen).toEqual(["pear"]);
    expect(input?.value).toBe("");
    shown.unmount();
  });
});
