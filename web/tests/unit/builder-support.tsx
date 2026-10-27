/**
 * What the builder's tests share (D433): the builder rendered under `<StrictMode>` from a fresh module
 * graph (the tab's queue is module state), its options minted by the real seam from a describe answer
 * the real walk returned (`fetch` stubbed), every later request recorded, and a person's actions
 * fired through the rendered controls alone: a list's option chosen by its id (`optionAt`, D441), a field typed into
 * with the native setter, a button pressed. The builder's answers are the server's own
 * (`orchard-answers.json`'s `builder` section, eight answers, each with its request, written by `test_openapi_results.py`).
 */
import { act, StrictMode } from "react";
import { vi } from "vitest";

import { optionAt } from "../../e2e/options";
import { render, type Rendered } from "./render";
import { answersOf, json, orchardText, type Sent } from "./screens";

/** The orchard's describe answers: whole, its first page of three columns, and under a floor of 50. */
export const DESCRIBED = { whole: orchardText("describe_dataset", 0), firstPage: orchardText("describe_dataset", 1), floored: orchardText("describe_dataset", 2) } as const;

/** A builder answer of the server's: its operation, its text, its status and the request it answers
 * (the server's record of the body the page sends). */
export interface Recorded {
  readonly operation: string;
  readonly body: string;
  readonly status: number;
  readonly request: string;
}

/** The builder's answer of an operation and status, by its place among them. */
function textOf(operation: string, status: number, index: number): Recorded {
  const found = answersOf(operation, status)[index];
  if (found?.request === undefined) {
    throw new Error(`no builder answer ${operation} ${String(status)} #${String(index)}`);
  }
  return { operation, body: found.body, status: found.status, request: found.request };
}

/** The builder's answers (the server's own, each with its request): the two-cohort document's
 * validation, count and run; its category view validated and counted under a floor of 50; a numeric
 * view validated and counted (422) under that floor; a string view column validated. The last two
 * answer documents this form cannot send (it offers no string view column, and Count waits for a
 * valid validation): a cell that serves one serves it to another body, by name, as a synthetic answer. */
export const ANSWERS = {
  validated: textOf("validate_document", 200, 2),
  counted: textOf("count_cohort", 200, 1),
  categoryUnderK: textOf("validate_document", 200, 3),
  countedUnderK: textOf("count_cohort", 200, 2),
  ran: textOf("run_analysis", 200, 3),
  numericUnderK: textOf("validate_document", 200, 4),
  numericCountUnderK: textOf("count_cohort", 422, 0),
  stringView: textOf("validate_document", 200, 5),
} as const;

/** A response of an answer. */
export const respond = (answer: { readonly body: string; readonly status: number }): Response => json(answer.body, answer.status);

/** The server's answer to the request the page sent: the one recorded for that tool and that body,
 * byte for byte; none is a test's failure (`builderPage` reports it when the page closes). */
export function byRequest(sent: Sent): Response {
  const found = Object.values(ANSWERS).find((one) => one.operation === sent.tool && one.request === sent.body);
  if (found === undefined) {
    throw new Error(`no answer of the server's to this request: ${sent.tool} ${String(sent.body)}`);
  }
  return respond(found);
}

/** A synthetic answer, served whatever the page sent (a cell names why it serves one). */
export const synthetic = (answer: Recorded): ((sent: Sent) => Response) => () => respond(answer);

/** The document the builder's fixtures were asked about, as the server recorded the request it
 * answered (the page's body is held to it, never to a copy written here). */
export const BUILT: unknown = (JSON.parse(ANSWERS.validated.request) as { document: unknown }).document;

/** A rendered builder and a person's hands on it. */
export interface BuilderPage {
  readonly container: HTMLElement;
  /** The builder's requests, in order (the describe call before the render is not among them). A
   * request that no answer answers fails the cell when the page closes. */
  readonly sent: Sent[];
  /** Choose the option of the id `text` (its text the id, or the id, ` — ` and its name: `optionAt`) in
   * the `nth` list labelled `label` (`"(choose)"`: none). */
  choose(label: "Unit table" | "Column", text: string, nth?: number): Promise<void>;
  /** Type `text` into the `nth` field. */
  type(nth: number, text: string): Promise<void>;
  /** Press the `nth` button named `name`. */
  press(name: string, nth?: number): Promise<void>;
  button(name: string, nth?: number): HTMLButtonElement;
  selects(label: "Unit table" | "Column"): HTMLSelectElement[];
  inputs(): HTMLInputElement[];
  /** The page's text, its runs of white space one space. */
  text(): string;
  /** Let the page read what has arrived. */
  settle(): Promise<void>;
  close(): void;
}

/** The modules of a fresh graph: the seam and the builder (one queue of their own). */
export async function freshBuilder(): Promise<{
  readonly api: typeof import("../../src/api");
  readonly Builder: typeof import("../../src/builder/Builder").Builder;
  readonly builder: typeof import("../../src/builder/Builder");
  readonly load: typeof import("../../src/catalogue/load");
}> {
  vi.resetModules();
  const api = await import("../../src/api");
  const builder = await import("../../src/builder/Builder");
  const load = await import("../../src/catalogue/load");
  return { api, Builder: builder.Builder, builder, load };
}

/** Let promises and React settle. */
export async function settle(): Promise<void> {
  await act(async () => {
    for (let turn = 0; turn < 30; turn += 1) {
      await Promise.resolve();
    }
  });
}

/** The builder of `describe`'s options, its later requests answered by `answer`. */
export async function builderPage(answer: (sent: Sent) => Response | Promise<Response> = byRequest, describe: string = DESCRIBED.whole): Promise<BuilderPage> {
  const { api, Builder } = await freshBuilder();
  vi.stubGlobal("fetch", () => Promise.resolve(json(describe)));
  const outcome = await api.describeDataset({ dataset: "orchard" });
  if (outcome.kind !== "ok") {
    throw new Error(`the describe answer did not walk: ${outcome.kind}`);
  }
  const sent: Sent[] = [];
  const unanswered: string[] = [];
  vi.stubGlobal("fetch", (url: string, init?: RequestInit): Promise<Response> => {
    const one: Sent = { tool: url.slice(url.lastIndexOf("/") + 1), body: typeof init?.body === "string" ? init.body : null };
    sent.push(one);
    try {
      return Promise.resolve(answer(one));
    } catch (error) {
      unanswered.push(error instanceof Error ? error.message : "an answer threw");
      return Promise.reject(new TypeError("no answer"));
    }
  });
  const shown: Rendered = await render(
    <StrictMode>
      <Builder options={api.optionsOf(outcome.value)} />
    </StrictMode>,
  );
  return pageOf(shown, sent, unanswered);
}

/** A person's hands on a rendered builder. */
export function pageOf(shown: Rendered, sent: Sent[], unanswered: readonly string[] = []): BuilderPage {
  const container = shown.container;
  const selects = (label: "Unit table" | "Column"): HTMLSelectElement[] =>
    [...container.querySelectorAll("label")].filter((one) => (one.firstChild?.textContent ?? "").trim() === label).flatMap((one) => [...one.querySelectorAll("select")]);
  const buttons = (name: string): HTMLButtonElement[] => [...container.querySelectorAll("button")].filter((one) => one.textContent === name);
  const button = (name: string, nth = 0): HTMLButtonElement => {
    const found = buttons(name)[nth];
    if (found === undefined) {
      throw new Error(`no button ${name} #${String(nth)}`);
    }
    return found;
  };
  return {
    container,
    sent,
    async choose(label, text, nth = 0) {
      const select = selects(label)[nth];
      if (select === undefined) {
        throw new Error(`no list ${label} #${String(nth)}`);
      }
      const option = select.options[optionAt([...select.options].map((one) => one.textContent), text)];
      if (option === undefined) {
        throw new Error(`no option ${text} in ${label} #${String(nth)}`);
      }
      await act(async () => {
        select.value = option.value;
        select.dispatchEvent(new Event("change", { bubbles: true }));
        await Promise.resolve();
      });
    },
    async type(nth, text) {
      const input = [...container.querySelectorAll("input")][nth];
      if (input === undefined) {
        throw new Error(`no field #${String(nth)}`);
      }
      await act(async () => {
        Reflect.set(HTMLInputElement.prototype, "value", text, input);
        input.dispatchEvent(new Event("input", { bubbles: true }));
        await Promise.resolve();
      });
    },
    async press(name, nth = 0) {
      const found = button(name, nth);
      await act(async () => {
        found.click();
        await Promise.resolve();
      });
      await settle();
    },
    button,
    selects,
    inputs: () => [...container.querySelectorAll("input")],
    text: () => container.textContent.replace(/\s+/gu, " ").trim(),
    settle,
    close() {
      shown.unmount();
      if (unanswered.length > 0) {
        throw new Error(`the page sent requests no answer of the server's answers: ${unanswered.join("; ")}`);
      }
    },
  };
}

/** Fill the form as the fixtures' document has it: unit `trees`, `first` with `trees.variety` is
 * `apple`, a second cohort with no condition, the view of `trees.height_m` and `trees.variety`. */
export async function fillBuilt(page: BuilderPage): Promise<void> {
  await page.choose("Unit table", "trees");
  await page.press("Add a condition");
  await page.choose("Column", "trees.variety", 0);
  await page.type(0, "apple");
  await page.press("Add a second cohort");
  await page.choose("Column", "trees.height_m", 1);
  await page.press("Add a column");
  await page.choose("Column", "trees.variety", 2);
}
