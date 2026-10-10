/**
 * The operator's client (D423): the curator token, the operator's name, the CSRF token and the
 * session handles, held in this module's closures and sent by `client.ts`'s `send` alone, to this
 * origin's `/operator/` alone. `createCurator` makes one; `operator.ts` makes the page's one and
 * installs its listeners (this module has no side effect, so that the tests make as many as they
 * need).
 *
 * **What it holds, and where.** The token, the CSRF token and each dataset's session handle live
 * in a closure of `createCurator`, never in storage, a URL, the history, the DOM, a log or an
 * error's message: nothing this module returns holds one (an answer to `open` is given without its
 * `handle`; the CSRF answer is not given at all). **Locked, the client holds no secret:** a lock
 * (idle, a refused token, too many refused tokens, the page hidden or restored) and the forget
 * action drop the token, the CSRF token and every handle alike. A session that was open stays
 * open on the server: its handle is gone with the lock, and taking it over is the CLI's until the
 * browser's take-over lands (D423).
 *
 * **The epoch.** Each lock, forget and unlock starts an epoch: it aborts every request in flight
 * (one `AbortController` an epoch) and an answer that comes back in a later epoch than its
 * request's is dropped, unread for any decision (a late `open` never refills the handles), as an
 * unknown outcome (`stale`). Every write an answer makes to the closure (the CSRF token, the
 * unlocked state, a handle, a draft, a dropped handle) is made by a `commit` that runs in the same
 * step as the epoch check, with no `await` between them, so that no lock, forget or 401 can come
 * between the check and the write: locked, the client holds nothing, whatever the timing.
 *
 * **The idle lock.** Before every request, and on `visibilitychange`, `pageshow`, each trusted
 * input event and a timer, the client compares the wall clock (`Date.now`) and the monotonic
 * clock (`performance.now`) with the last activity: ten minutes or more on either, or a wall
 * clock earlier than the last activity, locks (a suspended laptop's timers do not run and its
 * monotonic clock may not advance; its wall clock does). Activity is a trusted input event alone
 * (`isTrusted`: a key, a pointer, a wheel, a touch), never a request or a timer, and an event that
 * comes once the limit is past locks rather than counting. `pagehide` locks; a `pageshow` that
 * restores the page from the back/forward cache (`persisted`) locks too.
 *
 * **The answers.** A `401` locks (`token`); a `429` whose refusal names the limit
 * `token_failures` locks (`failures`), and one naming another limit does not; `CSRF_REQUIRED`
 * fetches the CSRF token again and sends the request once more, once, and no other refusal is
 * ever sent again; a `CONFLICT` or `NO_SESSION` answer to a change, publish or discard drops that
 * dataset's handle (a stale handle and a stale `expected` are both `CONFLICT`, which only the
 * message tells apart). Every decision reads the status, a refusal's `code` or its `limit.name`,
 * never its message. A request whose outcome is unknown (`client.ts`'s `Unknown`, or `stale`) is
 * never sent again by the client.
 *
 * **The requests.** Typed per operation, and none takes a number (D423): the four operations that
 * would send one back (`take-over`, the `accept` edit, a proposal's rejection and a named
 * release's queue) wait for the request half; `change` takes the three edits that carry no
 * number (`remove`, `confirm`, `remove_descriptor`), checked again at run time, member by member,
 * and rebuilt; `withdraw` takes a release's manifest, never its label. Upload and erase are not
 * wired.
 */
import { isServerNumber } from "./box";
import { type BodyArgument, CSRF_FORM, type Credentials, send, TOKEN_FORM, type Unknown } from "./client";
import type { components } from "./generated/openapi";
import * as routes from "./generated/routes";
import type { JsonOut } from "./json";
import type { OperationId, Route } from "./route";

type Schemas = components["schemas"];

/** How long without a trusted input event locks the client: ten minutes, a client rule (D412,
 * D423; the server has no idle timeout). */
export const IDLE_LIMIT_MS = 10 * 60 * 1000;

/** How often the timer looks at the clocks while the client is unlocked: so that the lock shows
 * without a request (the check before every request is what holds the rule). */
export const IDLE_CHECK_MS = 15 * 1000;

/** The longest operator name, in code points (§5.1, D262). */
export const NAME_LIMIT = 200;

/** The most edits a change carries (the server's `MAX_CHANGE_EDITS`). */
export const EDIT_LIMIT = 256;

/** The trusted input events that are activity. */
export const ACTIVITY_EVENTS = ["keydown", "pointerdown", "wheel", "touchstart"] as const;

/** A session handle's form (D267). */
const HANDLE_FORM = /^ses_[A-Za-z0-9_-]{43}$/u;

/** A release's manifest or a draft's id: `sha256:` and 64 lower-case hexadecimal digits. */
const DIGEST_FORM = /^sha256:[0-9a-f]{64}$/u;

/** Why the client is locked: never unlocked yet, forgotten, idle, its token refused, too many
 * refused tokens from this address, the page hidden, or the page restored from the cache. */
export type LockReason = "start" | "forgotten" | "idle" | "token" | "failures" | "page" | "restored";

/** What the client refused before sending anything. */
export type Invalid = "token" | "name" | "dataset" | "manifest" | "edits" | "session";

/** An operation's outcome. `answer`: a 2xx and its decoded body (an `open`'s without its
 * handle); `refused`: any other status, with its refusals' codes (and its body, for the screens'
 * words); `locked`: the client is locked (nothing was sent, or the answer locked it); `unknown`:
 * the request may have reached the server and what it did is not known; `invalid`: refused
 * before sending. */
export type Outcome =
  | { readonly kind: "answer"; readonly status: number; readonly body: JsonOut }
  | { readonly kind: "refused"; readonly status: number; readonly codes: readonly string[]; readonly body: JsonOut }
  | { readonly kind: "locked"; readonly reason: LockReason }
  | { readonly kind: "unknown"; readonly reason: Unknown | "stale" }
  | { readonly kind: "invalid"; readonly what: Invalid };

/** An unlock's outcome: unlocked, or why not (the CSRF answer is never given). */
export type Unlocked =
  | { readonly kind: "unlocked" }
  | { readonly kind: "refused"; readonly status: number; readonly codes: readonly string[] }
  | { readonly kind: "locked"; readonly reason: LockReason }
  | { readonly kind: "unknown"; readonly reason: Unknown | "stale" }
  | { readonly kind: "invalid"; readonly what: Invalid };

/** The edits a browser's change carries: those with no number in them (D423). `set` and `put`
 * carry a JSON value (`DescriptorJson`, which can hold a number taken back from a response) and
 * `accept` a proposal's id: they wait for the request half. */
export type Edit = Schemas["RemoveField"] | Schemas["Confirm"] | Schemas["RemoveDescriptor"];

/** What the shell shows: whether the client is locked, and why. */
export interface View {
  readonly locked: boolean;
  readonly reason: LockReason | null;
}

/** The clocks the client reads; the page's are `Date.now` and `performance.now`. */
export interface Clocks {
  readonly wall: () => number;
  readonly monotonic: () => number;
}

/** The timer the client sets while unlocked. */
export interface Timers {
  readonly set: (run: () => void, every: number) => unknown;
  readonly clear: (timer: unknown) => void;
}

/** Where the client listens: the window (input, `pagehide`, `pageshow`) and the document
 * (`visibilitychange`). */
export interface Targets {
  readonly window: EventTarget;
  readonly document: EventTarget;
}

const PAGE_CLOCKS: Clocks = { wall: () => Date.now(), monotonic: () => performance.now() };

const PAGE_TIMERS: Timers = {
  set: (run, every) => setInterval(run, every),
  clear: (timer) => {
    if (typeof timer === "number") {
      clearInterval(timer);
    }
  },
};

// ---------------------------------------------------------------------------------------------
// The operator's name (D262): what `aibi.core.operator.auth.valid_name` accepts, and the header
// `encode_operator` makes of it. `tests/fixtures/operator-names.json`, which the server's
// `scripts/export_operator_names.py` writes from those functions, holds the two equal.

/** The bidi formatting characters (D262). */
const BIDI_FORMATTING: ReadonlySet<number> = new Set([0x061c, 0x200e, 0x200f, 0x202a, 0x202b, 0x202c, 0x202d, 0x202e, 0x2066, 0x2067, 0x2068, 0x2069]);

/** Whether a code point may not stand in a name: a C0 control, DEL, a C1 control, a line or
 * paragraph separator, a bidi formatting character, a surrogate (a lone one: a pair is one code
 * point) or a noncharacter (U+FDD0 to U+FDEF, and the last two of each plane). */
function forbidden(point: number): boolean {
  return (
    point <= 0x1f ||
    (point >= 0x7f && point <= 0x9f) ||
    point === 0x2028 ||
    point === 0x2029 ||
    BIDI_FORMATTING.has(point) ||
    (point >= 0xd800 && point <= 0xdfff) ||
    (point >= 0xfdd0 && point <= 0xfdef) ||
    point % 0x10000 >= 0xfffe
  );
}

/** A token or a handle standing alone: no base64url character touching either end (D265). */
const SECRET_ALONE = /(?<![A-Za-z0-9_-])(?:aibi|ses)_[A-Za-z0-9_-]{43}(?![A-Za-z0-9_-])/u;

/** How many times a name is percent-decoded when a secret is looked for in it (the server's
 * `SECRET_DECODINGS`). */
const SECRET_DECODINGS = 3;

const HEX = /^[0-9A-Fa-f]{2}/u;

/** A run of ASCII text percent-decoded to bytes, as Python's `_unquote_impl`: each `%` followed
 * by two hexadecimal digits is a byte, any other `%` stays. */
function unquotedBytes(text: string): Uint8Array {
  const bytes: number[] = [];
  const [first = "", ...rest] = text.split("%");
  for (const character of first) {
    bytes.push(character.charCodeAt(0));
  }
  for (const item of rest) {
    const pair = HEX.exec(item);
    const tail = pair === null ? `%${item}` : item.slice(2);
    if (pair !== null) {
      bytes.push(Number.parseInt(pair[0], 16));
    }
    for (const character of tail) {
      bytes.push(character.charCodeAt(0));
    }
  }
  return Uint8Array.from(bytes);
}

/** Python's `urllib.parse.unquote(text)` (UTF-8, `errors="replace"`): each run of ASCII
 * percent-decoded and its bytes decoded as UTF-8 with U+FFFD for a malformed sequence (a BOM
 * kept), every other character kept as it is. */
export function unquote(text: string): string {
  if (!text.includes("%")) {
    return text;
  }
  const decoder = new TextDecoder("utf-8", { ignoreBOM: true });
  let out = "";
  let ascii = "";
  for (const character of text) {
    if (character.charCodeAt(0) <= 0x7f) {
      ascii += character;
    } else {
      out += decoder.decode(unquotedBytes(ascii)) + character;
      ascii = "";
    }
  }
  return out + decoder.decode(unquotedBytes(ascii));
}

/** Whether a text holds a token's or a handle's shape standing alone, as written or
 * percent-decoded up to three times (the server's `holds_secret`). */
function holdsSecret(text: string): boolean {
  let value = text;
  for (let step = 0; step <= SECRET_DECODINGS; step += 1) {
    if (SECRET_ALONE.test(value)) {
      return true;
    }
    const decoded = unquote(value);
    if (decoded === value) {
      return false;
    }
    value = decoded;
  }
  return false;
}

/** Whether a name is one the server accepts (`valid_name`): 1 to 200 code points, none
 * `forbidden`, holding no secret's shape. */
export function validName(name: string): boolean {
  const points = Array.from(name);
  if (points.length < 1 || points.length > NAME_LIMIT) {
    return false;
  }
  for (const point of points) {
    if (forbidden(point.codePointAt(0) ?? 0)) {
      return false;
    }
  }
  return !holdsSecret(name);
}

/** A text percent-encoded as Python's `quote(text, safe="")` encodes it: UTF-8, everything but
 * `[A-Za-z0-9._~-]` encoded in upper-case hexadecimal (`encodeURIComponent` leaves `!`, `'`, `(`,
 * `)` and `*`, which are encoded here); `null` for a text holding a lone surrogate, which UTF-8
 * cannot encode (`encodeURIComponent` throws on it). */
export function percentEncode(text: string): string | null {
  let encoded: string;
  try {
    encoded = encodeURIComponent(text);
  } catch {
    return null;
  }
  return encoded.replace(/[!'()*]/gu, (character) => `%${character.charCodeAt(0).toString(16).toUpperCase()}`);
}

/** A name as the `Aibi-Operator` header carries it (the server's `encode_operator`), `null` for
 * a name `validName` refuses: the client never sends a name the server would refuse, and never a
 * token pasted where the name goes. */
export function operatorHeader(name: string): string | null {
  return validName(name) ? percentEncode(name) : null;
}

// ---------------------------------------------------------------------------------------------
// Reading answers: the status, a refusal's code and its limit's name, and the strings the client
// holds (a handle, a draft, the CSRF token). Never a refusal's message.

type JsonRecord = { readonly [member: string]: JsonOut };

function isList(value: JsonOut | undefined): value is readonly JsonOut[] {
  return Array.isArray(value);
}

function isRecord(value: JsonOut | undefined): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value) && !isServerNumber(value);
}

/** A member of an object, `undefined` for anything else or a member it does not own. */
function member(value: JsonOut | undefined, key: string): JsonOut | undefined {
  return isRecord(value) && Object.hasOwn(value, key) ? value[key] : undefined;
}

/** A string member, `null` if it is none or not in `form`. */
function text(value: JsonOut | undefined, key: string, form: RegExp): string | null {
  const found = member(value, key);
  return typeof found === "string" && form.test(found) ? found : null;
}

/** The refusals of a body: their codes and their limits' names (an object's members, never its
 * message). */
function refusals(body: JsonOut): { readonly codes: readonly string[]; readonly limits: readonly string[] } {
  const listed = member(body, "refusals");
  const codes: string[] = [];
  const limits: string[] = [];
  for (const refusal of isList(listed) ? listed : []) {
    const code = member(refusal, "code");
    if (typeof code === "string") {
      codes.push(code);
    }
    const name = member(member(refusal, "limit"), "name");
    if (typeof name === "string") {
      limits.push(name);
    }
  }
  return { codes, limits };
}

/** A body without its `handle` member: what `open` gives a caller. */
function withoutHandle(body: JsonOut): JsonOut {
  if (!isRecord(body)) {
    return body;
  }
  return Object.freeze(Object.fromEntries(Object.entries(body).filter(([key]) => key !== "handle")));
}

// ---------------------------------------------------------------------------------------------
// Edits: the three kinds without a number, member by member, rebuilt.

/** The members each kind of edit may have: every one a string, but `confirm`'s `pointers`, a list
 * of strings. */
const EDIT_MEMBERS: ReadonlyMap<string, readonly string[]> = new Map([
  ["remove", ["op", "descriptor", "pointer"]],
  ["confirm", ["op", "descriptor", "evidence", "pointers"]],
  ["remove_descriptor", ["op", "descriptor"]],
]);

/** A list of strings, copied, or `null`. */
function strings(value: unknown): readonly string[] | null {
  if (!Array.isArray(value)) {
    return null;
  }
  const list: readonly unknown[] = value;
  const found = list.filter((item): item is string => typeof item === "string");
  return found.length === list.length ? Object.freeze(found) : null;
}

/** An edit rebuilt from its own enumerable members, each read once (`Object.entries`), `null` if
 * it is not one of the three kinds exactly: a member its kind does not have, a value that is not
 * a string (or, for `pointers`, a list of strings), a required member missing. A number, a box or
 * any other value never reaches the request. */
function rebuilt(edit: unknown): Edit | null {
  if (typeof edit !== "object" || edit === null || Array.isArray(edit)) {
    return null;
  }
  const members = new Map<string, unknown>(Object.entries(edit));
  const op = members.get("op");
  const allowed = typeof op === "string" ? EDIT_MEMBERS.get(op) : undefined;
  if (allowed === undefined || [...members.keys()].some((key) => !allowed.includes(key))) {
    return null;
  }
  const descriptor = members.get("descriptor");
  if (typeof descriptor !== "string") {
    return null;
  }
  if (op === "remove") {
    const pointer = members.get("pointer");
    return typeof pointer === "string" ? Object.freeze({ op, descriptor, pointer }) : null;
  }
  if (op === "remove_descriptor") {
    return Object.freeze({ op, descriptor });
  }
  const evidence = members.get("evidence");
  const pointers = members.has("pointers") ? strings(members.get("pointers")) : undefined;
  if ((evidence !== undefined && typeof evidence !== "string") || pointers === null) {
    return null;
  }
  return Object.freeze({
    op: "confirm",
    descriptor,
    ...(evidence === undefined ? {} : { evidence }),
    ...(pointers === undefined ? {} : { pointers }),
  });
}

/** Whether a value is a string: for a check at run time of what the types already say. */
function isText(value: unknown): value is string {
  return typeof value === "string";
}

// ---------------------------------------------------------------------------------------------
// The client.

/** What the client holds while unlocked. */
interface Held {
  readonly token: string;
  readonly operator: string;
  csrf: string;
}

/** What an operation writes to the closure from an answer, given the outcome in the same step as
 * the epoch check; it gives the outcome the caller sees. */
type Commit = (outcome: Outcome) => Outcome;

const asIs: Commit = (outcome) => outcome;

/** A dataset's open session, as this client opened it: its handle and the draft it last saw. */
interface Session {
  readonly handle: string;
  draft: string;
}

export interface Curator {
  /** Unlock with a token and a name: the token is checked for its form, the name encoded, and
   * the CSRF token fetched with them; neither is kept unless the server accepts the token. */
  unlock(token: string, name: string): Promise<Unlocked>;
  /** Forget the token, the CSRF token and every handle, and abort every request in flight. */
  forget(): void;
  /** What the shell shows. */
  view(): View;
  /** Listen for a change of `view`; gives the function that stops listening. */
  subscribe(listener: () => void): () => void;
  /** Whether the client holds a session handle for a dataset (never the handle). */
  holds(dataset: string): boolean;
  /** Listen on the page: the idle lock's events, `pagehide` and `pageshow`. */
  install(targets: Targets): void;
  datasets(): Promise<Outcome>;
  dataset(dataset: string): Promise<Outcome>;
  queue(dataset: string): Promise<Outcome>;
  open(dataset: string): Promise<Outcome>;
  change(dataset: string, edits: readonly Edit[]): Promise<Outcome>;
  publish(dataset: string): Promise<Outcome>;
  discard(dataset: string): Promise<Outcome>;
  withdraw(dataset: string, manifest: string): Promise<Outcome>;
}

/** The operator's client (module docstring). `clocks` and `timers` are the page's but in the
 * tests. */
export function createCurator(clocks: Clocks = PAGE_CLOCKS, timers: Timers = PAGE_TIMERS): Curator {
  let held: Held | null = null;
  const sessions = new Map<string, Session>();
  let epoch = 0;
  let controller = new AbortController();
  let current: View = Object.freeze({ locked: true, reason: "start" });
  const listeners = new Set<() => void>();
  let lastWall = 0;
  let lastMonotonic = 0;
  let timer: unknown = null;

  function show(next: View): void {
    current = Object.freeze(next);
    for (const listener of [...listeners]) {
      listener();
    }
  }

  /** Start a new epoch: abort what is in flight, so that its answers are dropped. */
  function nextEpoch(): void {
    epoch += 1;
    controller.abort();
    controller = new AbortController();
  }

  function lock(reason: LockReason): void {
    nextEpoch();
    held = null;
    sessions.clear();
    if (timer !== null) {
      timers.clear(timer);
      timer = null;
    }
    show({ locked: true, reason });
  }

  /** Whether the idle limit is past: on either clock, or the wall clock went backwards. */
  function expired(): boolean {
    const wall = clocks.wall();
    const monotonic = clocks.monotonic();
    return wall < lastWall || wall - lastWall >= IDLE_LIMIT_MS || monotonic - lastMonotonic >= IDLE_LIMIT_MS;
  }

  /** Lock if unlocked and idle; whether the client is (now) locked. */
  function checkIdle(): boolean {
    if (held === null) {
      return true;
    }
    if (expired()) {
      lock("idle");
      return true;
    }
    return false;
  }

  function activity(event: Event): void {
    if (!event.isTrusted || checkIdle()) {
      return;
    }
    lastWall = clocks.wall();
    lastMonotonic = clocks.monotonic();
  }

  function credentialsOf(found: Held, csrf: string | null): Credentials {
    return { token: found.token, operator: found.operator, csrf };
  }

  /** What an answer decides, beside its body: a 401 locks; a 429 naming `token_failures` locks;
   * any other status is the answer's, or a refusal. */
  function settled(status: number, body: JsonOut): Outcome {
    if (status === 401) {
      lock("token");
      return { kind: "locked", reason: "token" };
    }
    const { codes, limits } = refusals(body);
    if (status === 429 && limits.includes("token_failures")) {
      lock("failures");
      return { kind: "locked", reason: "failures" };
    }
    if (status >= 200 && status <= 299) {
      return { kind: "answer", status, body };
    }
    return { kind: "refused", status, codes, body };
  }

  /** The CSRF token, fetched with `found`'s token and name, in epoch `at`: the token, or the
   * outcome that stopped it. `commit` is given the token in the same step as the epoch check (no
   * `await` between them), and is the one place it is written to the closure. */
  async function fetchCsrf(
    found: { readonly token: string; readonly operator: string },
    at: number,
    signal: AbortSignal,
    commit: (csrf: string) => void,
  ): Promise<string | Outcome> {
    const sent = await send(routes.csrf(), { token: found.token, operator: found.operator, csrf: null }, signal);
    if (at !== epoch) {
      return { kind: "unknown", reason: sent.kind === "unknown" ? sent.reason : "stale" };
    }
    if (sent.kind === "unknown") {
      return sent;
    }
    const outcome = settled(sent.answer.status, sent.answer.body);
    if (outcome.kind !== "answer") {
      return outcome;
    }
    const csrf = text(outcome.body, "csrf", CSRF_FORM);
    if (csrf === null) {
      return { kind: "unknown", reason: "undecodable" };
    }
    commit(csrf);
    return csrf;
  }

  /** Send an operation's request while unlocked and not idle; on `CSRF_REQUIRED`, fetch the CSRF
   * token again and send it once more. An answer of an earlier epoch is dropped, unread. Every
   * other answer goes through `commit` in the same step as the epoch check (no `await` between
   * them), and `commit` is the one place an operation writes to the closure (a handle, a draft),
   * so that no lock can come between the check and the write. */
  async function perform<O extends OperationId>(route: Route<O>, commit: Commit, ...given: BodyArgument<O>): Promise<Outcome> {
    const found = held;
    if (found === null || checkIdle()) {
      return { kind: "locked", reason: current.reason ?? "start" };
    }
    const at = epoch;
    const { signal } = controller;
    let sent = await send(route, credentialsOf(found, found.csrf), signal, ...given);
    for (let attempt = 0; ; attempt += 1) {
      if (at !== epoch) {
        return { kind: "unknown", reason: sent.kind === "unknown" ? sent.reason : "stale" };
      }
      if (sent.kind === "unknown") {
        return sent;
      }
      const { status, body } = sent.answer;
      if (attempt > 0 || status !== 403 || !refusals(body).codes.includes("CSRF_REQUIRED")) {
        return commit(settled(status, body));
      }
      const fresh = await fetchCsrf(found, at, signal, (csrf) => {
        found.csrf = csrf;
      });
      if (typeof fresh !== "string") {
        return fresh;
      }
      sent = await send(route, credentialsOf(found, fresh), signal, ...given);
    }
  }

  /** A dataset's route, or `null` for a name no route can carry (`segment` refuses it). */
  function routed<O extends OperationId>(make: () => Route<O>): Route<O> | null {
    try {
      return make();
    } catch {
      return null;
    }
  }

  /** Drop a dataset's session if it is still `session` (not one opened since). */
  function drop(dataset: string, session: Session): void {
    if (sessions.get(dataset) === session) {
      sessions.delete(dataset);
    }
  }

  /** After a change, a publish or a discard: a `CONFLICT` or `NO_SESSION` drops the handle. */
  function dropOnConflict(dataset: string, session: Session, outcome: Outcome): Outcome {
    if (outcome.kind === "refused" && (outcome.codes.includes("CONFLICT") || outcome.codes.includes("NO_SESSION"))) {
      drop(dataset, session);
    }
    return outcome;
  }

  async function end(dataset: string, kind: "publish" | "discard"): Promise<Outcome> {
    const route = routed(() => routes[kind]({ dataset }));
    if (route === null) {
      return { kind: "invalid", what: "dataset" };
    }
    const session = sessions.get(dataset);
    if (session === undefined) {
      return { kind: "invalid", what: "session" };
    }
    return perform(
      route,
      (outcome) => {
        if (outcome.kind === "answer") {
          drop(dataset, session);
        }
        return dropOnConflict(dataset, session, outcome);
      },
      { handle: session.handle, expected: session.draft },
    );
  }

  async function read(make: () => Route): Promise<Outcome> {
    const route = routed(make);
    return route === null ? { kind: "invalid", what: "dataset" } : perform(route, asIs);
  }

  return {
    async unlock(token, name) {
      lock("forgotten");
      if (!TOKEN_FORM.test(token)) {
        return { kind: "invalid", what: "token" };
      }
      const operator = operatorHeader(name);
      if (operator === null) {
        return { kind: "invalid", what: "name" };
      }
      const fresh = await fetchCsrf({ token, operator }, epoch, controller.signal, (csrf) => {
        held = { token, operator, csrf };
        lastWall = clocks.wall();
        lastMonotonic = clocks.monotonic();
        timer = timers.set(() => {
          checkIdle();
        }, IDLE_CHECK_MS);
        show({ locked: false, reason: null });
      });
      if (typeof fresh !== "string") {
        return fresh.kind === "answer" ? { kind: "unknown", reason: "undecodable" } : fresh.kind === "refused" ? { kind: "refused", status: fresh.status, codes: fresh.codes } : fresh;
      }
      return { kind: "unlocked" };
    },
    forget() {
      lock("forgotten");
    },
    view: () => current,
    subscribe(listener) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    holds: (dataset) => sessions.has(dataset),
    install(targets) {
      for (const name of ACTIVITY_EVENTS) {
        targets.window.addEventListener(name, activity, { capture: true, passive: true });
      }
      targets.window.addEventListener("pagehide", () => {
        lock("page");
      });
      targets.window.addEventListener("pageshow", (event) => {
        if ("persisted" in event && event.persisted === true) {
          lock("restored");
        } else {
          checkIdle();
        }
      });
      targets.document.addEventListener("visibilitychange", () => {
        checkIdle();
      });
    },
    datasets: () => read(() => routes.datasets()),
    dataset: (dataset) => read(() => routes.dataset_state({ dataset })),
    queue: (dataset) => read(() => routes.queue({ dataset }, {})),
    async open(dataset) {
      const route = routed(() => routes.open_session({ dataset }));
      if (route === null) {
        return { kind: "invalid", what: "dataset" };
      }
      return perform(
        route,
        (outcome) => {
          if (outcome.kind !== "answer") {
            return outcome;
          }
          const handle = text(outcome.body, "handle", HANDLE_FORM);
          const draft = text(outcome.body, "draft", DIGEST_FORM);
          if (handle === null || draft === null) {
            return { kind: "unknown", reason: "undecodable" };
          }
          sessions.set(dataset, { handle, draft });
          return { kind: "answer", status: outcome.status, body: withoutHandle(outcome.body) };
        },
        {},
      );
    },
    async change(dataset, edits) {
      const route = routed(() => routes.change({ dataset }));
      if (route === null) {
        return { kind: "invalid", what: "dataset" };
      }
      const given: unknown = edits;
      const list: readonly unknown[] = Array.isArray(given) ? given : [];
      const kept = list.map(rebuilt);
      const valid = kept.filter((edit) => edit !== null);
      if (valid.length < 1 || valid.length > EDIT_LIMIT || valid.length !== kept.length) {
        return { kind: "invalid", what: "edits" };
      }
      const session = sessions.get(dataset);
      if (session === undefined) {
        return { kind: "invalid", what: "session" };
      }
      return perform(
        route,
        (outcome) => {
          if (outcome.kind === "answer") {
            const draft = text(outcome.body, "draft", DIGEST_FORM);
            if (draft === null) {
              drop(dataset, session);
              return { kind: "unknown", reason: "undecodable" };
            }
            session.draft = draft;
          }
          return dropOnConflict(dataset, session, outcome);
        },
        { handle: session.handle, expected: session.draft, edits: valid },
      );
    },
    publish: (dataset) => end(dataset, "publish"),
    discard: (dataset) => end(dataset, "discard"),
    async withdraw(dataset, manifest) {
      const route = routed(() => routes.withdraw({ dataset }));
      if (route === null) {
        return { kind: "invalid", what: "dataset" };
      }
      if (!isText(manifest) || !DIGEST_FORM.test(manifest)) {
        return { kind: "invalid", what: "manifest" };
      }
      return perform(route, asIs, { release: manifest });
    },
  };
}
