/**
 * The app's one place of I/O (D419): `fetch`, `Response` and `Request` are named here alone (the
 * lint refuses them, `XMLHttpRequest`, `EventSource`, `WebSocket` and every `.json()` anywhere
 * else in `src/`), and every response body the app reads goes through `readCapped` and then
 * `decode`, the one decoder. `exchange` sends without a credential; `send` is the operator's
 * client's (`curator.ts`, D423), the one function that sets the curator token, the operator's name
 * and the CSRF token, on this origin's `/operator/` alone.
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
import { decode, DecodeError, sourceTextSupported } from "./decode";
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
  notCredential: "A credential the operator's client sends is not in its form; it was not sent.",
  sealed: "A body is read by the client's decoder alone (D419).",
  unsupported:
    "This browser cannot read the server's numbers exactly (it lacks JSON source text access). Use a current Chromium, Edge or Chrome.",
} as const;

export type Refusal = keyof typeof REFUSALS;

/** A refusal of the client's own: its words are `REFUSALS[refusal]`, fixed, and `refusal` names
 * which (what `send` decides on: never the words). */
export class ClientError extends Error {
  readonly refusal: Refusal;

  constructor(refusal: Refusal) {
    super(REFUSALS[refusal]);
    this.name = "ClientError";
    this.refusal = refusal;
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
    throw new ClientError("tooLarge");
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
        throw new ClientError("interrupted");
      });
      if (read.done) {
        break;
      }
      total += read.value.byteLength;
      if (total > cap) {
        await reader.cancel();
        throw new ClientError("tooLarge");
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
    throw new ClientError("notUtf8");
  }
}

/** A request's JSON text; a number that is not finite (which `JSON.stringify` writes as `null`)
 * is refused. The type of what it is given is the operation's (`RequestBody`). */
function encode(body: unknown): string {
  return JSON.stringify(body, (_key, value: unknown) => {
    if (typeof value === "number" && !Number.isFinite(value)) {
      throw new ClientError("notFinite");
    }
    return value;
  });
}

/** The JSON body an operation takes, as the generated types say (`never`: it takes none, or one
 * that is no JSON, such as an upload's, which no client sends yet: D423). */
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

/** A request's `RequestInit`: the method, no ambient credential (`omit`: the server sets no
 * cookie and asks no HTTP authentication of a browser), no redirect followed, nothing cached, and
 * the JSON body encoded here, as a string made afresh for each request (a refused number is
 * refused before anything is sent). */
function prepared(route: Route, headers: Headers, body: unknown): RequestInit {
  headers.set("Accept", "application/json");
  const init: RequestInit = { method: route.method, headers, credentials: "omit", cache: "no-store", redirect: "error" };
  if (body !== undefined) {
    headers.set("Content-Type", "application/json");
    init.body = encode(body);
  }
  return init;
}

/** The answer to a sent request: refused unread if it is not JSON (by its type), else read
 * through the cap and the decoder. */
async function received<O extends OperationId>(route: Route<O>, response: Response): Promise<Answer<O>> {
  const type = response.headers.get("Content-Type");
  if (type === null || !/^application\/json\s*(?:;|$)/iu.test(type)) {
    await response.body?.cancel();
    throw new ClientError("notJson");
  }
  const text = await readCapped(response);
  return { operation: route.operation, status: response.status, body: decode(text) };
}

/** Send a request to a route `routes.ts` made, with the operation's JSON body (its type the
 * generated one: a request integer is a JS number, which no `ServerNumber` is) or none, and read
 * the answer through the cap and the decoder. No credential goes with it: the curator token, the
 * operator's name and the CSRF token are `send`'s alone, which the operator's client
 * (`curator.ts`) calls and nothing else may (the lint, the import-graph test and the build's
 * gate). An answer that is not JSON (by its type) is refused unread. */
export async function exchange<O extends OperationId>(route: Route<O>, ...given: BodyArgument<O>): Promise<Answer<O>> {
  if (!isRoute(route) || !/^\/(?:api|operator)\/[^\\]*$/u.test(route.url) || route.url.includes("//")) {
    throw new ClientError("notARoute");
  }
  const [body] = given;
  const init = prepared(route, new Headers(), body);
  return received(route, await fetch(route.url, init));
}

/** What the operator's client sends beside a request (D423): the curator token (as
 * `Authorization: Bearer`), the operator's name as the `Aibi-Operator` header carries it
 * (percent-encoded, `curator.ts`'s `operatorHeader`), and the CSRF token (`Aibi-CSRF`; `null` for
 * `GET /operator/csrf`, which needs none). */
export interface Credentials {
  readonly token: string;
  readonly operator: string;
  readonly csrf: string | null;
}

/** The curator token's form (D261): `aibi_` and 43 base64url characters. */
export const TOKEN_FORM = /^aibi_[A-Za-z0-9_-]{43}$/u;

/** An `Aibi-Operator` value's form (D262), upper-case hexadecimal alone (the one encoding the
 * server accepts). */
export const OPERATOR_FORM = /^(?:[A-Za-z0-9._~-]|%[0-9A-F]{2})+$/u;

/** A CSRF token's form (D263): base64url of an HMAC-SHA256, 43 characters. */
export const CSRF_FORM = /^[A-Za-z0-9_-]{43}$/u;

/** Why the outcome of a request that may have reached the server is unknown (D423): its `fetch`
 * failed (`network`: no answer came, which says nothing of whether the server acted), the client
 * aborted it (`aborted`: a lock or a forget), or its answer could not be read (`interrupted`,
 * `tooLarge`, `notJson`, `notUtf8`, `undecodable`). The operator's client never sends such a
 * request again by itself; the person reads the state afresh. */
export type Unknown = "network" | "aborted" | "interrupted" | "tooLarge" | "notJson" | "notUtf8" | "undecodable";

/** The refusals of the client's own that come from reading an answer: an unknown outcome each. */
const UNREAD: ReadonlyMap<Refusal, Unknown> = new Map([
  ["interrupted", "interrupted"],
  ["tooLarge", "tooLarge"],
  ["notJson", "notJson"],
  ["notUtf8", "notUtf8"],
]);

/** What `send` gives: the answer, or why the outcome is unknown. */
export type Sent<O extends OperationId = OperationId> =
  | { readonly kind: "answer"; readonly answer: Answer<O> }
  | { readonly kind: "unknown"; readonly reason: Unknown };

/** Whether a route's URL is a path under this origin's `/operator/` that no URL parser can move
 * (D423, defence in depth: `routes.ts` makes only such URLs): a single-slash absolute path of
 * printable ASCII, with no backslash, no `//` and no dot segment, plain or percent-encoded (`.`,
 * `..`, `%2e` in any case and mixes of them, which `fetch` removes or climbs). */
function underOperator(url: string): boolean {
  if (!/^\/operator\/[\x21-\x5b\x5d-\x7e]*$/u.test(url) || url.includes("//")) {
    return false;
  }
  const path = url.split(/[?#]/u, 1)[0] ?? "";
  return !path.split("/").some((part) => /^(?:\.|%2e){1,2}$/iu.test(part));
}

/** Send an operator request (D423): to a route `routes.ts` made under this origin's `/operator/`
 * alone, with the credentials as headers (each refused unsent if it is not in its form, in fixed
 * words that hold none of it), aborted with `signal`. Never throws once the request may have
 * left: a failed `fetch`, an abort and an answer that cannot be read are an unknown outcome,
 * `{kind: "unknown"}`, which the caller never sends again by itself. A route elsewhere, a
 * credential not in its form and a body with a number that is not finite throw, sending
 * nothing. The body is encoded afresh on each call. */
export async function send<O extends OperationId>(
  route: Route<O>,
  credentials: Credentials,
  signal: AbortSignal,
  ...given: BodyArgument<O>
): Promise<Sent<O>> {
  if (!isRoute(route) || !underOperator(route.url)) {
    throw new ClientError("notARoute");
  }
  const { token, operator, csrf } = credentials;
  if (!TOKEN_FORM.test(token) || !OPERATOR_FORM.test(operator) || (csrf !== null && !CSRF_FORM.test(csrf))) {
    throw new ClientError("notCredential");
  }
  const headers = new Headers({ Authorization: `Bearer ${token}`, "Aibi-Operator": operator });
  if (csrf !== null) {
    headers.set("Aibi-CSRF", csrf);
  }
  const [body] = given;
  const init = prepared(route, headers, body);
  init.signal = signal;
  let response: Response;
  try {
    response = await fetch(route.url, init);
  } catch {
    return { kind: "unknown", reason: signal.aborted ? "aborted" : "network" };
  }
  try {
    return { kind: "answer", answer: await received(route, response) };
  } catch (error) {
    if (signal.aborted) {
      return { kind: "unknown", reason: "aborted" };
    }
    if (error instanceof DecodeError) {
      return { kind: "unknown", reason: "undecodable" };
    }
    const unread = error instanceof ClientError ? UNREAD.get(error.refusal) : undefined;
    if (unread !== undefined) {
      return { kind: "unknown", reason: unread };
    }
    throw error;
  }
}
