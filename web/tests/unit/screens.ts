/**
 * What the screens' tests share (D431): `fetch` stubbed by a server of the test's (every request
 * recorded: its tool and its body's text), the server's own answers over the orchard
 * (`tests/fixtures/orchard-answers.json`, written by the server's test), and an answer walked as
 * the screens meet it.
 */
import { vi } from "vitest";

import type { Answered } from "../../src/api/typed";
import { MEMBERS_NOT_SHOWN } from "../../src/dataset/Dataset";
import { walked } from "./answers";
import { ORCHARD, type Written } from "./typed-support";

/** A request the page sent: its tool (the last segment of its path) and its body's text. */
export interface Sent {
  readonly tool: string;
  readonly body: string | null;
}

/** A JSON response of `status` with `body`. */
export function json(body: string, status = 200, headers: Readonly<Record<string, string>> = {}): Response {
  return new Response(body, { status, headers: { "Content-Type": "application/json", ...headers } });
}

/** `fetch` stubbed by `answer`, given each request's tool and body; the requests, as sent. */
export function serving(answer: (sent: Sent) => Response | Promise<Response>): Sent[] {
  const sent: Sent[] = [];
  vi.stubGlobal("fetch", (url: string, init?: RequestInit): Promise<Response> => {
    const one: Sent = { tool: url.slice(url.lastIndexOf("/") + 1), body: typeof init?.body === "string" ? init.body : null };
    sent.push(one);
    return Promise.resolve(answer(one));
  });
  return sent;
}

/** The orchard's answers of an operation and status, in the file's order. */
export function answersOf(operation: string, status = 200): readonly Written[] {
  return ORCHARD.filter((answer) => answer.operation === operation && answer.status === status);
}

/** The `index`th orchard answer of an operation and status, as a response. */
export function orchard(operation: string, status = 200, index = 0): Response {
  const found = answersOf(operation, status)[index];
  if (found === undefined) {
    throw new Error(`no orchard answer ${operation} ${String(status)} #${String(index)}`);
  }
  return json(found.body, found.status, found.headers);
}

/** An answer of `search_catalog`, walked from its text. */
export function hitsOf(text: string): Answered<"search_catalog"> {
  return walked("CatalogHits", text) as Answered<"search_catalog">;
}

/** An answer of `describe_dataset`, walked from its text. */
export function describedOf(text: string): Answered<"describe_dataset"> {
  return walked("DatasetDescription", text) as Answered<"describe_dataset">;
}

/** The text of the `index`th orchard answer of an operation's 200. */
export function orchardText(operation: string, index = 0): string {
  const found = answersOf(operation)[index];
  if (found === undefined) {
    throw new Error(`no orchard answer ${operation} #${String(index)}`);
  }
  return found.body;
}

/** What is wrong, if anything, with the dataset page's notice that descriptor members are not shown
 * (T8; round 2 of #123, r2-m2): exactly one `.legend`, equal to the fixed words around `dataset`, and
 * the words' first half nowhere else in the page, and not hidden. The class holds on every answer the page is given,
 * the paged ones too (a page that dropped the notice when the columns are paged read a member as
 * absent). */
export function notShownProblems(container: Element, dataset: string): string[] {
  const notices = [...container.querySelectorAll(".legend")].map((one) => one.textContent);
  const wanted = `${MEMBERS_NOT_SHOWN[0]}${dataset}${MEMBERS_NOT_SHOWN[1]}`;
  const problems: string[] = [];
  if (notices.length !== 1 || notices[0] !== wanted) {
    problems.push(`${String(notices.length)} notices, one wanted, equal to the words around ${dataset}`);
  }
  // Seen, not only present: an element hidden by the `hidden` attribute (jsdom's own style sheet has
  // `[hidden] { display: none }`) or by an inline style, on it or on an ancestor, says nothing.
  for (const notice of container.querySelectorAll(".legend")) {
    for (let at: Element | null = notice; at !== null; at = at.parentElement) {
      const style = getComputedStyle(at);
      if (style.display === "none" || style.visibility === "hidden" || at.getAttribute("aria-hidden") === "true") {
        problems.push(`the notice is hidden (${at.tagName.toLowerCase()})`);
        break;
      }
    }
  }
  const total = container.textContent.split(MEMBERS_NOT_SHOWN[0]).length - 1;
  if (total !== 1) {
    problems.push(`the words said ${String(total)} times, once wanted`);
  }
  return problems;
}
