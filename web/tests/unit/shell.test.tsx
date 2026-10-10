/**
 * The token's shell and the confirmation step (D412, D423), in jsdom against a fake `fetch`: the
 * token field is uncontrolled (React writes a controlled one's value into the `value` attribute,
 * where the token would stand in the DOM), emptied as soon as the form is sent; the token never
 * reaches the DOM, before or after; the shell's words are fixed; the forget action locks; every
 * link of the operator entry opens with `rel="noopener noreferrer"`; a destructive operation runs
 * only on "Confirm".
 */
import { act, useRef, useState } from "react";
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

/** How many strings, among everything React keeps of a tree (each fiber's hooks, props and update
 * queue, and what they hold), contain `secret`: a count, so that a failing absence prints none. DOM
 * nodes and functions are not entered (a closure is not readable, and the DOM has its own scan). */
function inReactState(container: HTMLElement, secret: string): number {
  const key = Object.keys(container).find((name) => name.startsWith("__reactContainer$"));
  const start: unknown = key === undefined ? undefined : Reflect.get(container, key);
  const seen = new Set<unknown>();
  let found = 0;
  const scan = (value: unknown): void => {
    if (typeof value === "string") {
      found += value.includes(secret) ? 1 : 0;
      return;
    }
    if (typeof value !== "object" || value === null || seen.has(value) || value instanceof Node || value instanceof Window) {
      return;
    }
    seen.add(value);
    for (const member of Object.values(value)) {
      scan(member);
    }
  };
  const fibers: unknown[] = [start];
  const visited = new Set<unknown>();
  while (fibers.length > 0) {
    const fiber = fibers.pop();
    if (typeof fiber !== "object" || fiber === null || visited.has(fiber)) {
      continue;
    }
    visited.add(fiber);
    for (const part of ["memoizedState", "memoizedProps", "pendingProps", "updateQueue", "dependencies", "key", "type"]) {
      scan(Reflect.get(fiber, part));
    }
    fibers.push(Reflect.get(fiber, "child"), Reflect.get(fiber, "sibling"), Reflect.get(fiber, "alternate"));
  }
  return found;
}

describe("the scan of React's state", () => {
  it("sees a secret held in a hook's state, a ref or a prop (so that an empty scan means something)", async () => {
    function Keeps({ shown }: { shown: string }) {
      const [state] = useState(TOKEN);
      const ref = useRef(`${TOKEN}!`);
      return <p>{shown.length + state.length + ref.current.length}</p>;
    }
    const rendered = await render(<Keeps shown={`x${TOKEN}`} />);
    expect(inReactState(rendered.container, TOKEN)).toBeGreaterThanOrEqual(3);
    rendered.unmount();
    const clean = await render(<Keeps shown="x" />);
    expect(inReactState(clean.container, "no-such-secret-in-the-tree")).toBe(0);
    clean.unmount();
  });
});

describe("the token's shell", () => {
  it("asks for a name and the token in an uncontrolled password field, without autocomplete", async () => {
    const rendered = await shell();
    expect(rendered.words()).toBe("Enter your name and the curator token to begin. Your name Curator token Unlock");
    const token = field(rendered, "token");
    expect([token.type, token.getAttribute("autocomplete"), token.hasAttribute("value")]).toEqual(["password", "off", false]);
    expect(field(rendered, "operator").getAttribute("autocomplete")).toBe("off");
    rendered.unmount();
  });

  it("asks the password managers to leave the token field alone (a request, no barrier: Chrome may still offer to save it)", async () => {
    const rendered = await shell();
    const token = field(rendered, "token");
    expect(["data-1p-ignore", "data-lpignore", "data-bwignore"].map((name) => token.getAttribute(name))).toEqual(["true", "true", "true"]);
    expect(field(rendered, "operator").hasAttribute("data-1p-ignore")).toBe(false);
    rendered.unmount();
  });

  it("holds the token in no React state, props or ref, while the unlock is pending and once it is done", async () => {
    const rendered = await shell();
    type(field(rendered, "operator"), "Ada");
    type(field(rendered, "token"), TOKEN);
    let answer: (response: Response) => void = () => undefined;
    vi.stubGlobal("fetch", () => new Promise<Response>((done) => {
      answer = done;
    }));
    await submit(rendered);
    expect(rendered.words()).toContain("Unlocking.");
    expect(inReactState(rendered.container, TOKEN)).toBe(0);
    await act(async () => {
      answer(new Response(JSON.stringify({ csrf: CSRF }), { status: 200, headers: { "Content-Type": "application/json" } }));
      await Promise.resolve();
    });
    await rendered.until((words) => words.startsWith("Unlocked."));
    expect(inReactState(rendered.container, TOKEN)).toBe(0);
    expect(inReactState(rendered.container, CSRF)).toBe(0);
    rendered.unmount();
  });

  it("shows Cancel while an unlock is pending; Cancel aborts it, and the shell is locked and empty-handed", async () => {
    const rendered = await shell();
    const buttons = () => [...rendered.container.querySelectorAll("button")].map((button) => button.textContent);
    expect(buttons()).toEqual(["Unlock"]);
    type(field(rendered, "operator"), "Ada");
    type(field(rendered, "token"), TOKEN);
    let aborted = false;
    vi.stubGlobal("fetch", (_url: string, init?: RequestInit) => new Promise<Response>((_done, fail) => {
      init?.signal?.addEventListener("abort", () => {
        aborted = true;
        fail(new DOMException("aborted", "AbortError"));
      });
    }));
    await submit(rendered);
    expect(buttons()).toEqual(["Unlock", "Cancel"]);
    expect([...rendered.container.querySelectorAll("button")].find((button) => button.textContent === "Unlock")?.disabled).toBe(true);
    await act(async () => {
      [...rendered.container.querySelectorAll("button")].find((button) => button.textContent === "Cancel")?.click();
      await Promise.resolve();
    });
    expect(aborted).toBe(true);
    const expected = "The token is forgotten. Enter it again to go on. Your name Curator token Unlock Unlocking was cancelled. Nothing is held. Enter the token again to go on.";
    expect(await rendered.until((words) => words === expected)).toBe(expected);
    expect(buttons()).toEqual(["Unlock"]);
    expect(inMarkup()).toBe(0);
    expect(inReactState(rendered.container, TOKEN)).toBe(0);
    const { operator } = await import("../../src/api/operator");
    expect(operator.view()).toEqual({ locked: true, reason: "forgotten" });
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
