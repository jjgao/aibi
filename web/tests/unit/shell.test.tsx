/**
 * The token's shell and the confirmation step (D412, D423), in jsdom against a fake `fetch`: the
 * token field is uncontrolled (React writes a controlled one's value into the `value` attribute,
 * where the token would stand in the DOM), emptied as soon as the form is sent; the token never
 * reaches the DOM, before or after; the shell's words are fixed; the forget action locks; every
 * link of the operator entry opens with `rel="noopener noreferrer"`; a destructive operation runs
 * only on "Confirm".
 */
import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Destructive } from "../../src/operator/Confirm";
import { render, type Rendered } from "./render";

const TOKEN = `aibi_${"T0k-_n".repeat(7)}Q`;
const CSRF = `${"CsRf-_".repeat(7)}Z`;

let asked: { url: string; headers: Headers }[] = [];
let csrfStatus = 200;

beforeEach(() => {
  document.body.replaceChildren();
  vi.resetModules();
  asked = [];
  csrfStatus = 200;
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    asked.push({ url, headers: new Headers(init?.headers) });
    const body = csrfStatus === 200 ? { csrf: CSRF } : { refusals: [{ code: "TOKEN_REQUIRED", path: null, message: [], alternatives: [] }] };
    return Promise.resolve(new Response(JSON.stringify(body), { status: csrfStatus, headers: { "Content-Type": "application/json" } }));
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

async function shell(): Promise<Rendered> {
  const { Shell } = await import("../../src/operator/Shell");
  return render(<Shell />);
}

/** Type into an input as a person does: the value set through the element's own setter and an
 * `input` event, which React's change handling reads. */
function type(input: HTMLInputElement, value: string): void {
  Reflect.set(HTMLInputElement.prototype, "value", value, input);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

function field(rendered: Rendered, name: string): HTMLInputElement {
  const found = rendered.container.querySelector(`input[name="${name}"]`);
  if (!(found instanceof HTMLInputElement)) {
    throw new Error(`no ${name} field`);
  }
  return found;
}

/** How many times the token stands in the document's markup (a count: a failing absence prints
 * no secret). */
const inMarkup = (): number => document.documentElement.outerHTML.split(TOKEN).length - 1;

async function submit(rendered: Rendered): Promise<void> {
  const form = rendered.container.querySelector("form");
  await act(async () => {
    form?.requestSubmit();
    await Promise.resolve();
  });
}

describe("the token's shell", () => {
  it("asks for a name and the token in an uncontrolled password field, without autocomplete", async () => {
    const rendered = await shell();
    expect(rendered.words()).toBe("Enter your name and the curator token to begin. Your name Curator token Unlock");
    const token = field(rendered, "token");
    expect([token.type, token.getAttribute("autocomplete"), token.hasAttribute("value")]).toEqual(["password", "off", false]);
    expect(field(rendered, "operator").getAttribute("autocomplete")).toBe("off");
    rendered.unmount();
  });

  it("never writes the token into the DOM: not while it is typed, not once sent, and empties the field", async () => {
    const rendered = await shell();
    type(field(rendered, "operator"), "Ada");
    await act(async () => {
      type(field(rendered, "token"), TOKEN);
      await Promise.resolve();
    });
    expect(field(rendered, "token").hasAttribute("value")).toBe(false);
    expect(inMarkup()).toBe(0);
    await submit(rendered);
    await rendered.until((words) => words.startsWith("Unlocked."));
    expect(asked.map(({ url, headers }) => [url, headers.get("Authorization") === `Bearer ${TOKEN}`, headers.get("Aibi-Operator")])).toEqual([["/operator/csrf", true, "Ada"]]);
    expect(inMarkup()).toBe(0);
    expect(rendered.container.querySelector('input[name="token"]')).toBeNull();
    rendered.unmount();
  });

  it("empties the field the moment the form is sent, before the answer", async () => {
    const rendered = await shell();
    type(field(rendered, "operator"), "Ada");
    type(field(rendered, "token"), TOKEN);
    const token = field(rendered, "token");
    let during = "unread";
    vi.stubGlobal("fetch", () => {
      during = token.value;
      return new Promise<Response>(() => undefined);
    });
    await submit(rendered);
    expect(during).toBe("");
    expect(token.value).toBe("");
    expect(rendered.words()).toContain("Unlocking.");
    rendered.unmount();
  });

  it("forgets the token on the forget action, and says so", async () => {
    const rendered = await shell();
    type(field(rendered, "operator"), "Ada");
    type(field(rendered, "token"), TOKEN);
    await submit(rendered);
    await rendered.until((words) => words.startsWith("Unlocked."));
    const forget = [...rendered.container.querySelectorAll("button")].find((button) => button.textContent === "Forget the token");
    await act(async () => {
      forget?.click();
      await Promise.resolve();
    });
    expect(rendered.words()).toBe("The token is forgotten. Enter it again to go on. Your name Curator token Unlock");
    rendered.unmount();
  });

  it.each([
    ["a refused token", TOKEN, "Ada", 401, "The server refused the token. Enter it again to go on. Your name Curator token Unlock"],
    ["a token not in its form", "aibi_short", "Ada", 200, "The token is forgotten. Enter it again to go on. Your name Curator token Unlock The token is not in its form: aibi_ and 43 characters."],
    [
      "the token as the name",
      TOKEN,
      TOKEN,
      200,
      "The token is forgotten. Enter it again to go on. Your name Curator token Unlock The name is not one the server accepts: 1 to 200 characters, with no control, line-break or bidi formatting character and no token in it.",
    ],
  ])("says, in fixed words, what came of %s", async (_case, token, name, status, expected) => {
    csrfStatus = status;
    const rendered = await shell();
    type(field(rendered, "operator"), name);
    type(field(rendered, "token"), token);
    await submit(rendered);
    expect(await rendered.until((words) => words === expected)).toBe(expected);
    expect(asked.length).toBe(status === 401 ? 1 : 0);
    expect(field(rendered, "operator").value === TOKEN ? 0 : inMarkup()).toBe(0);
    rendered.unmount();
  });
});

describe("the operator entry's links", () => {
  it("each opens with rel noopener noreferrer, to a fixed path", async () => {
    const { Operator } = await import("../../src/operator/Operator");
    const rendered = await render(<Operator />);
    const links = [...rendered.container.querySelectorAll("a")];
    expect(links.length).toBeGreaterThan(0);
    for (const link of links) {
      expect([link.getAttribute("href"), link.getAttribute("rel")]).toEqual(["/", "noopener noreferrer"]);
    }
    rendered.unmount();
  });
});

describe("the confirmation step", () => {
  it.each(["withdraw", "discard", "publish"] as const)("%s runs only on Confirm, after its full effect is shown", async (action: Destructive) => {
    const { Confirm, EFFECTS } = await import("../../src/operator/Confirm");
    const run = vi.fn();
    const rendered = await render(<Confirm action={action} onConfirm={run} />);
    const button = (words: string) => [...rendered.container.querySelectorAll("button")].find((found) => found.textContent === words);
    expect(rendered.words()).toBe(EFFECTS[action].action);
    await act(async () => {
      button(EFFECTS[action].action)?.click();
      await Promise.resolve();
    });
    expect(rendered.words()).toBe(`${EFFECTS[action].effect} Confirm Cancel`);
    expect(run).not.toHaveBeenCalled();
    await act(async () => {
      button("Cancel")?.click();
      await Promise.resolve();
    });
    expect(run).not.toHaveBeenCalled();
    expect(rendered.words()).toBe(EFFECTS[action].action);
    await act(async () => {
      button(EFFECTS[action].action)?.click();
      await Promise.resolve();
    });
    await act(async () => {
      button("Confirm")?.click();
      await Promise.resolve();
    });
    expect(run).toHaveBeenCalledTimes(1);
    rendered.unmount();
  });
});
