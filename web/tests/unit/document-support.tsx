/**
 * What the document seam's tests share (D432): a person's typing through the real `Field` in jsdom
 * (no test mint of a `Typed`), describe answers walked by the real seam from a stubbed `fetch`
 * (`optionsOf` takes no other), and the oracles, written apart from `document.ts`: §7.1's literal,
 * the server's grammars, the lists the server takes at each role, the options a describe answer
 * gives, and the corpus's value classes.
 */
import { createHash } from "node:crypto";

import { act } from "react";
import { vi } from "vitest";

import { describeDataset, type Answered } from "../../src/api/typed";
import { Field, type Typed, UNTYPED } from "../../src/layout/Field";
import { render } from "./render";
import { json, orchardText } from "./screens";

/** The server's limits (`aibi.core.schema.limits`: `MAX_STRING`, `MAX_DOCUMENT_BYTES`,
 * `MAX_BODY_BYTES`), which the server's corpus test holds the corpus's edge entries to. */
export const MAX_STRING = 4096;
export const MAX_DOCUMENT_BYTES = 2 * 1024 * 1024;
export const MAX_BODY_BYTES = 8 * 1024 * 1024;

/** The orchard's describe answers: the whole one, the first page of its columns, and under a
 * floor of 50 (D431's). */
export const DESCRIBED = { whole: orchardText("describe_dataset", 0), firstPage: orchardText("describe_dataset", 1), floored: orchardText("describe_dataset", 2) } as const;

/** A person typing into the real `Field`: what it hands on for each text typed, or `null` when it
 * hands on nothing (a text the browser empties, such as a line break alone). */
export interface Typist {
  type(raw: string): Typed | null;
  close(): void;
}

export async function typist(): Promise<Typist> {
  let last: Typed | null = null;
  const shown = await render(
    <Field
      value={UNTYPED}
      onChange={(value) => {
        last = value;
      }}
    >
      Value
    </Field>,
  );
  const input = shown.container.querySelector("input");
  if (input === null) {
    throw new Error("no input");
  }
  return {
    type(raw) {
      last = null;
      act(() => {
        // The native setter, as a person's typing sets it (the browser's sanitising included).
        Reflect.set(HTMLInputElement.prototype, "value", raw, input);
        input.dispatchEvent(new Event("input", { bubbles: true }));
      });
      return last;
    },
    close() {
      shown.unmount();
    },
  };
}

/** A describe answer walked by the real seam: `fetch` stubbed to give `body` (and restored). */
export async function described(body: string): Promise<Answered<"describe_dataset">> {
  vi.stubGlobal("fetch", () => Promise.resolve(json(body)));
  try {
    const outcome = await describeDataset({ dataset: "orchard" });
    if (outcome.kind !== "ok") {
      throw new Error(`the describe answer did not walk: ${outcome.kind}`);
    }
    return outcome.value;
  } finally {
    vi.unstubAllGlobals();
  }
}

/** §7.1's literal of a typed value: a value that starts with `$` gets one more. */
export const literalOf = (value: string): string => (value.startsWith("$") ? `$${value}` : value);

/** The server's id grammars (`CatalogHit.dataset`, a table, a column), written here apart from the
 * generated `GRAMMARS`. */
const TABLE = /^[a-z][a-z0-9_]{0,63}$/u;
const COLUMN = /^[a-z][a-z0-9_]{0,63}\.[a-z][a-z0-9_]{0,63}$/u;
export const isTableId = (text: string): boolean => TABLE.test(text) && !text.includes("__");
export const isColumnId = (text: string): boolean => COLUMN.test(text) && !text.includes("__") && Array.from(text).length <= 129;

/** The datatypes the server takes at each role, written here apart from `datatypes.ts`
 * (`distribution.SUMMARISED`, `_CATEGORIES`; `typed_constant`'s every-string datatypes but
 * `list<category>`). */
export const SERVER_LISTS = {
  leaf: ["string", "category"],
  view: ["category", "boolean", "number", "integer", "time_offset"],
  viewUnderK: ["category", "boolean"],
} as const;

/** What the oracle expects `optionsOf` to give, by text, from the raw answer. */
export interface ExpectedOptions {
  readonly dataset: string | null;
  readonly units: readonly { readonly label: string; readonly view: readonly string[] }[];
  readonly leaves: readonly string[];
  readonly left: { readonly tables: boolean; readonly leaves: boolean; readonly view: boolean };
  readonly firstPage: boolean;
  readonly underK: boolean;
}

interface RawColumn {
  readonly id: string;
  readonly datatype?: string;
}

interface RawAnswer {
  readonly dataset: string;
  readonly disclosure: { readonly min_cell_count: number | null };
  readonly tables: readonly { readonly columns: readonly RawColumn[] }[];
  readonly graph: { readonly tables: readonly string[] };
  readonly columns_next?: number;
}

/** The oracle of `optionsOf`, over the answer's text. */
export function expectedOptions(text: string): ExpectedOptions {
  const answer = JSON.parse(text) as RawAnswer;
  const underK = answer.disclosure.min_cell_count !== null;
  const viewTypes: readonly string[] = underK ? SERVER_LISTS.viewUnderK : SERVER_LISTS.view;
  const columns = answer.tables.flatMap((table) => table.columns);
  const tableOf = (id: string): string | null => {
    const dot = id.indexOf(".");
    return dot > 0 ? id.slice(0, dot) : null;
  };
  const units = answer.graph.tables.filter(isTableId).map((table) => ({
    label: table,
    view: columns.filter((column) => tableOf(column.id) === table && isColumnId(column.id) && column.datatype !== undefined && viewTypes.includes(column.datatype)).map((column) => column.id),
  }));
  const leaves = columns.filter((column) => isColumnId(column.id) && column.datatype !== undefined && (SERVER_LISTS.leaf as readonly string[]).includes(column.datatype)).map((column) => column.id);
  const unitColumns = columns.filter((column) => units.some((unit) => tableOf(column.id) === unit.label));
  return {
    dataset: isTableId(answer.dataset) ? answer.dataset : null,
    units,
    leaves,
    left: {
      tables: answer.graph.tables.some((table) => !isTableId(table)),
      leaves: leaves.length < columns.length,
      view: unitColumns.length > units.reduce((sum, unit) => sum + unit.view.length, 0),
    },
    firstPage: answer.columns_next !== undefined,
    underK,
  };
}

/** Whether a code point is a noncharacter (U+FDD0–U+FDEF, or U+nFFFE and U+nFFFF of any plane) or a
 * surrogate (a lone one: a string's code points read by `codePointAt`). */
export const refusedCodePoint = (point: number): boolean => (point >= 0xfdd0 && point <= 0xfdef) || (point & 0xfffe) === 0xfffe || (point >= 0xd800 && point <= 0xdfff);

/** A text's code points (a lone surrogate is one). */
export const codePoints = (text: string): number[] => Array.from(text, (one) => one.codePointAt(0) ?? 0);

/** A text's UTF-8 length (a lone surrogate counts as three bytes, as the server's surrogatepass
 * encoding does; a body never holds one, `JSON.stringify` escaping it). */
export const utf8Length = (text: string): number => codePoints(text).reduce((sum, point) => sum + (point < 0x80 ? 1 : point < 0x800 ? 2 : point < 0x10000 ? 3 : 4), 0);

/** The SHA-256 of a body's UTF-8 bytes, in hex. */
export const digestOf = (body: string): string => createHash("sha256").update(body, "utf8").digest("hex");

/** The server's measure of a document as written: its JSON as Python's `json.dumps(document,
 * ensure_ascii=False)` writes it (`", "` and `": "` between members), in UTF-8, by surrogatepass.
 * Each string is written as `JSON.stringify` writes it, but a lone surrogate raw, as Python's does;
 * the documents measured near a limit hold no control character that the two write apart. */
export function documentMeasure(document: unknown): number {
  const write = (value: unknown): number => {
    if (typeof value === "string") {
      return utf8Length(JSON.stringify(value).replace(/\\ud[89a-f][0-9a-f]{2}/gu, "\u0000\u0000\u0000"));
    }
    if (Array.isArray(value)) {
      return 2 + value.reduce((sum: number, item: unknown, index) => sum + write(item) + (index > 0 ? 2 : 0), 0);
    }
    if (typeof value === "object" && value !== null) {
      return 2 + Object.entries(value).reduce((sum, [key, item], index) => sum + write(key) + 2 + write(item) + (index > 0 ? 2 : 0), 0);
    }
    return JSON.stringify(value).length;
  };
  return write(document);
}

/** The class of a corpus entry, as the server's verdict sorts it (the server's test reclassifies
 * each entry from its typed values and its bytes, and holds this to its own):
 * - `D'`: a body over `MAX_BODY_BYTES` (413, every tool);
 * - `C`: a value holding a noncharacter or a surrogate (422 `INVALID_JSON` at the value);
 * - `D`: a document over `MAX_DOCUMENT_BYTES` by the server's measure (validate 200 `valid: false`
 *   `LIMIT_EXCEEDED` with no path; count and run 422);
 * - `B`: a value of more than `MAX_STRING` code points (validate 200 `valid: false`
 *   `LIMIT_EXCEEDED` at the value);
 * - `A`: valid, each value where it was written. */
export function classOf(values: readonly string[], measure: number, bodyBytes: number): "A" | "B" | "C" | "D" | "D'" {
  if (bodyBytes > MAX_BODY_BYTES) {
    return "D'";
  }
  if (values.some((value) => codePoints(value).some(refusedCodePoint))) {
    return "C";
  }
  if (measure > MAX_DOCUMENT_BYTES) {
    return "D";
  }
  return values.some((value) => codePoints(value).length > MAX_STRING) ? "B" : "A";
}
