/**
 * The app's one place of I/O (D419): `fetch`, `Response` and `Request` are named here alone (the
 * lint refuses them, `XMLHttpRequest`, `EventSource`, `WebSocket` and every `.json()` anywhere
 * else in `src/`), and every response body the app reads goes through `readCapped` and then
 * `decode`, the one decoder. M5.1c-1c builds the operator's client (the token, sessions, CSRF) on
 * `exchange`.
 *
 * **The cap.** The server bounds no response (only requests, `max_body_bytes`), and
 * `response.text()` buffers the whole body before any bound applies, so the client states its
 * own: 8 MiB of the body as it arrives, a client policy and no server rule (D419; the largest
 * golden envelope is far below it, which a test holds). The reader always counts the bytes the
 * stream gives (after any content coding), and cancels the stream the moment they pass the cap;
 * a `Content-Length` above it only refuses earlier. The bytes are decoded as UTF-8 as they come
 * (`TextDecoder`, `stream: true`, `fatal`: a malformed body is refused, not repaired).
 *
 * **The oracle.** `start` makes every body reader of this realm's `Response.prototype` and
 * `Request.prototype` but the stream (`json`, `text`, `arrayBuffer`, `blob`, `bytes`,
 * `formData`) throw, for good (not writable, not configurable), so that a body read through them
 * anywhere but here fails at run time; the end-to-end matrix checks that every `/api` and
 * `/operator` response a page received was decoded. What the seal does not stop, the lint does
 * (D419): a body's stream (`response.body`, which the client reads itself), another realm's
 * `fetch` (an `about:blank` iframe has readers of its own), `XMLHttpRequest`, a JSON module
 * import (`import(url, { with: { type: "json" } })`) and the Cache API (`caches`) are refused by
 * name, as written out, in the app's source, and not at run time. A member of an alias of the
 * global object named by a computed expression (`w[k]`, a literal key in a variable included) is
 * not seen by the lint and stopped by nothing at run time: the box protects numbers alone, and
 * this is a documented residual outside the threat model (a component's accident and a lazy
 * hand, D419). The seal's
 * mark is identity with the module's own `sealed` function, compared with `===` and read back
 * after it is defined, so a reader another script fixed there is not mistaken for it and a
 * definition that did not take refuses the start. Patching a builtin before `start` is out of
 * scope (deliberate code). `start` also refuses to start an
 * engine without JSON source text (`context.source`) or `JSON.rawJSON`, which the client needs to
 * read and send numbers exactly.
 */
import { decode, sourceTextSupported } from "./decode";
import type { operations } from "./generated/openapi";
import type { JsonOut } from "./json";
import { isRoute, type OperationId, type Route } from "./route";

/** The client's bound on a response body: 8 MiB, a client policy (no server rule is behind it). */
export const RESPONSE_CAP_BYTES = 8 * 1024 * 1024;

/** The words of each refusal: fixed, and never any text of the request or the answer. */
export const REFUSALS = {
  tooLarge: "The server's answer is larger than this client reads (8 MiB).",
  notJson: "The server's answer is not JSON.",
  notUtf8: "The server's answer is not UTF-8 text.",
  interrupted: "The server's answer was cut off before its end.",
  notARoute: "A request goes to a route the client made, on this origin.",
  notFinite: "A request holds a number that is not finite.",
  sealed: "A body is read by the client's decoder alone (D419).",
  unsupported:
    "This browser cannot read the server's numbers exactly (it lacks JSON source text access). Use a current Chromium, Edge or Chrome.",
} as const;

export class ClientError extends Error {
  constructor(words: string) {
    super(words);
    this.name = "ClientError";
  }
}

/** The body readers `start` seals: every one of the Fetch API's but the stream. */
export const SEALED_READERS = ["json", "text", "arrayBuffer", "blob", "bytes", "formData"] as const;

function sealed(): never {
  throw new TypeError(REFUSALS.sealed);
}

/** Whether a property is this module's own sealed reader: fixed to `sealed` by identity, not
 * writable, not configurable (a comparison, no builtin called: nothing a script that ran before
 * `start` patched can answer for it). */
function holdsSeal(prototype: object, name: string): boolean {
  const found = Object.getOwnPropertyDescriptor(prototype, name);
  return found?.value === sealed && found.writable === false && found.configurable === false;
}

/** Seal the body readers of `Response.prototype` and `Request.prototype`; `false` if one cannot
 * be (it is already fixed to something else, or the definition did not take: each descriptor is
 * read again after it is defined). Idempotent: a reader that is already this module's sealed one
 * is left as it is. */
export function sealBodyReaders(): boolean {
  for (const prototype of [Response.prototype, Request.prototype]) {
    for (const name of SEALED_READERS) {
      if (holdsSeal(prototype, name)) {
        continue;
      }
      if (Object.getOwnPropertyDescriptor(prototype, name)?.configurable === false) {
        return false;
      }
      Object.defineProperty(prototype, name, { value: sealed, writable: false, configurable: false, enumerable: false });
      if (!holdsSeal(prototype, name)) {
        return false;
      }
    }
  }
  return true;
}

/** Whether this engine can run the app: JSON source text access and `JSON.rawJSON`. */
export function supported(): boolean {
  return sourceTextSupported() && "rawJSON" in JSON && typeof JSON.rawJSON === "function";
}

/** Start the client before anything renders: `false` (and nothing sealed) if the engine lacks
 * what the client needs, or if a body reader cannot be sealed, for the entry to show
 * `REFUSALS.unsupported` alone. (`engine` is the
 * check, a parameter for the tests.) */
export function start(engine: () => boolean = supported): boolean {
  if (!engine()) {
    return false;
  }
  return sealBodyReaders();
}

/** A body's text, read from its stream under `cap`; the stream is cancelled when refused. */
export async function readCapped(response: Response, cap: number = RESPONSE_CAP_BYTES): Promise<string> {
  const body = response.body;
  const declared = response.headers.get("Content-Length");
  if (declared !== null && /^[0-9]{1,16}$/u.test(declared) && Number(declared) > cap) {
    await body?.cancel();
    throw new ClientError(REFUSALS.tooLarge);
  }
  if (body === null) {
    return "";
  }
  const reader = body.getReader();
  const decoder = new TextDecoder("utf-8", { fatal: true });
  let total = 0;
  let text = "";
  try {
    for (;;) {
      const read = await reader.read().catch(() => {
        throw new ClientError(REFUSALS.interrupted);
      });
      if (read.done) {
        break;
      }
      total += read.value.byteLength;
      if (total > cap) {
        await reader.cancel();
        throw new ClientError(REFUSALS.tooLarge);
      }
      text += utf8(decoder, read.value, { stream: true });
    }
    text += utf8(decoder);
  } catch (error) {
    await reader.cancel().catch(() => undefined);
    throw error;
  } finally {
    reader.releaseLock();
  }
  return text;
}

/** One step of the UTF-8 decoder, a malformed sequence refused in fixed words. */
function utf8(decoder: TextDecoder, bytes?: Uint8Array, options?: TextDecodeOptions): string {
  try {
    return decoder.decode(bytes, options);
  } catch {
    throw new ClientError(REFUSALS.notUtf8);
  }
}

/** A request's JSON text; a number that is not finite (which `JSON.stringify` writes as `null`)
 * is refused. The type of what it is given is the operation's (`RequestBody`). */
function encode(body: unknown): string {
  return JSON.stringify(body, (_key, value: unknown) => {
    if (typeof value === "number" && !Number.isFinite(value)) {
      throw new ClientError(REFUSALS.notFinite);
    }
    return value;
  });
}

/** The JSON body an operation takes, as the generated types say (`never`: it takes none, or one
 * that is no JSON, such as an upload's, which is M5.1c-1c's). */
export type RequestBody<O extends OperationId> = operations[O] extends {
  readonly requestBody: { readonly content: { readonly "application/json": infer B } };
}
  ? B
  : never;

/** `exchange`'s body argument: the operation's JSON body, or nothing for an operation without
 * one. */
export type BodyArgument<O extends OperationId> = [RequestBody<O>] extends [never] ? [] : [body: RequestBody<O>];

/** An answer: its HTTP status and its decoded body. */
export interface Answer<O extends OperationId = OperationId> {
  readonly operation: O;
  readonly status: number;
  readonly body: JsonOut;
}

/** Send a request to a route `routes.ts` made, with the operation's JSON body (its type the
 * generated one: a request integer is a JS number, which no `ServerNumber` is) or none, and read
 * the answer through the cap and the decoder. No ambient credential goes with it (`omit`: the
 * server sets no cookie and asks no HTTP authentication of a browser; the curator token,
 * M5.1c-1c's, is a header the client sets), no redirect is followed, nothing is cached. An answer
 * that is not JSON (by its type) is refused unread. */
export async function exchange<O extends OperationId>(route: Route<O>, ...given: BodyArgument<O>): Promise<Answer<O>> {
  if (!isRoute(route) || !/^\/(?:api|operator)\/[^\\]*$/u.test(route.url) || route.url.includes("//")) {
    throw new ClientError(REFUSALS.notARoute);
  }
  const headers = new Headers({ Accept: "application/json" });
  const init: RequestInit = { method: route.method, headers, credentials: "omit", cache: "no-store", redirect: "error" };
  const [body] = given;
  if (body !== undefined) {
    headers.set("Content-Type", "application/json");
    init.body = encode(body);
  }
  const response = await fetch(route.url, init);
  const type = response.headers.get("Content-Type");
  if (type === null || !/^application\/json\s*(?:;|$)/iu.test(type)) {
    await response.body?.cancel();
    throw new ClientError(REFUSALS.notJson);
  }
  const text = await readCapped(response);
  return { operation: route.operation, status: response.status, body: decode(text) };
}
