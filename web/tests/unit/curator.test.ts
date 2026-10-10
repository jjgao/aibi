/**
 * The operator's client (D423), against a fake `fetch`: what it sends, where, with which
 * credentials; what it holds and drops; the epoch, the idle lock, CSRF, the handle rules and the
 * unknown outcomes. Grids at the boundaries (every status 200 to 599, every refusal code, both
 * clocks at the limit minus one, at it and past it), oracles from D423's rules.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { isServerNumber } from "../../src/api/box";
import { ClientError, REFUSALS, RESPONSE_CAP_BYTES, send } from "../../src/api/client";
import { ACTIVITY_EVENTS, createCurator, type Curator, EDIT_LIMIT, type Edit, IDLE_CHECK_MS, IDLE_LIMIT_MS, NAME_LIMIT, type Outcome, type Targets } from "../../src/api/curator";
import type { components } from "../../src/api/generated/openapi";
import * as routes from "../../src/api/generated/routes";
import type { JsonOut } from "../../src/api/json";
import { route } from "../../src/api/route";

const TOKEN = `aibi_${"T0k-_n".repeat(7)}Q`;
const OTHER_TOKEN = `aibi_${"0therT".repeat(7)}W`;
const CSRF = `${"CsRf-_".repeat(7)}Z`;
const CSRF_2 = `${"cSrF_-".repeat(7)}Y`;
const HANDLE = `ses_${"HaNdLe".repeat(7)}X`;
const HANDLE_2 = `ses_${"hAnDlE".repeat(7)}V`;
const DRAFT = `sha256:${"a".repeat(64)}`;
const DRAFT_2 = `sha256:${"b".repeat(64)}`;
const MANIFEST = `sha256:${"c".repeat(64)}`;
const SECRETS = [TOKEN, OTHER_TOKEN, CSRF, CSRF_2, HANDLE, HANDLE_2];
const NAME = "Ada (curator)!";
const NAME_HEADER = "Ada%20%28curator%29%21";

const REFUSAL_CODES: readonly components["schemas"]["RefusalCode"][] = [
  "INVALID_JSON", "DUPLICATE_KEY", "NON_FINITE_NUMBER", "INTEGER_OUT_OF_RANGE", "NULL_NOT_ALLOWED", "MISSING_MEMBER", "UNKNOWN_MEMBER", "WRONG_TYPE",
  "INVALID_VALUE", "UNKNOWN_KIND", "CONFLICTING_MEMBERS", "UNKNOWN_PARAMETER", "INVALID_PARAMETER_REFERENCE", "UNKNOWN_COHORT", "COHORT_CYCLE",
  "COHORT_MISMATCH", "LEAF_NOT_ALLOWED", "DATASET_MISSING", "DUPLICATE_ENTRY", "REFERENCE_NOT_IN_VIEW", "CONCEPT_REQUIRED", "CROSS_DATASET_ONLY",
  "UNKNOWN_DATASET", "LIMIT_EXCEEDED", "UNKNOWN_DESCRIPTOR", "UNKNOWN_TABLE", "UNKNOWN_COLUMN", "UNKNOWN_RELATIONSHIP", "INVALID_UNIT", "INVALID_PATH",
  "NO_PATH", "AMBIGUOUS_PATH", "QUANTIFIER_MISMATCH", "EXCLUDE_SELF_NOT_ALLOWED", "UNDECLARED_DATATYPE", "INVALID_CONSTANT", "NOT_PERMISSIBLE",
  "UNITS_UNCONVERTIBLE", "RANGE_NOT_ALLOWED", "MEMBER_NOT_APPLICABLE", "SCOPE_COLUMN_MENTION", "FILTER_COLUMN_MENTION", "UNKNOWN_SCOPE_COLUMN",
  "ROW_IDS_NOT_ALLOWED", "WITHHELD_UNDER_K", "INVALID_KEY", "MIXED_RELEASES", "NOT_SUPPORTED", "PACK_UNAVAILABLE", "PACK_FAILED", "UNKNOWN_ANALYSIS",
  "COHORTS_OVERLAP", "AGGREGATE_REQUIRED", "AGGREGATE_NOT_ALLOWED", "OPEN_SCOPE", "UNPARSEABLE_SOURCE", "COLUMNS_CHANGED", "UNKNOWN_RELEASE",
  "RELEASE_WITHDRAWN", "ERASURE_BLOCKED", "KEY_NULL", "KEY_NOT_UNIQUE", "CARDINALITY_VIOLATED", "DANGLING_REFERENCE", "COVERAGE_NULL",
  "COVERAGE_UNKNOWN", "OUTSIDE_RECORD_FILTER", "NOT_APPLICABLE_IN_FILTER", "PATH_NOT_CONFINED", "ARCHIVE_REFUSED", "UNSUPPORTED_FORMAT",
  "EMPTY_SOURCE", "DATASET_BUSY", "DATASET_EXISTS", "CONFLICT", "NO_SESSION", "NO_CHANGE", "UNKNOWN_PROPOSAL", "INVALID_EXTENSION",
  "HOST_NOT_ALLOWED", "ORIGIN_NOT_ALLOWED", "TOKEN_REQUIRED", "OPERATOR_REQUIRED", "CSRF_REQUIRED", "NOT_FOUND", "METHOD_NOT_ALLOWED",
  "UNSUPPORTED_MEDIA_TYPE", "LENGTH_REQUIRED", "INTERNAL_ERROR",
];

// ---------------------------------------------------------------------------------------------
// The fake server.

interface Sent {
  readonly url: string;
  readonly method: string;
  readonly headers: Record<string, string>;
  readonly body: string | null;
  readonly signal: AbortSignal | null;
  readonly credentials: string | undefined;
  readonly redirect: string | undefined;
  readonly cache: string | undefined;
}

type Reply = Response | Promise<Response> | (() => Response | Promise<Response>);
type Handler = (request: Sent) => Reply;

let sent: Sent[] = [];
let handler: Handler = () => json(500, {});

function json(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...headers } });
}

/** A refusal body: one refusal with its code, a message and other members. */
function refusal(code: string, message = "words of the server's", extra: Record<string, unknown> = {}): unknown {
  return { refusals: [{ code, path: null, message: [{ text: message }], alternatives: [], ...extra }] };
}

/** The default server: the CSRF route answers with `CSRF`, every other route with what `routesTo`
 * says (by `METHOD path`), else a 404. */
let routesTo: Record<string, Handler> = {};

function server(request: Sent): Reply {
  const key = `${request.method} ${request.url}`;
  const found = routesTo[key];
  if (found !== undefined) {
    return found(request);
  }
  if (key === "GET /operator/csrf") {
    return json(200, { csrf: CSRF });
  }
  return json(404, refusal("NOT_FOUND"));
}

function headersOf(init: RequestInit | undefined): Record<string, string> {
  const found: Record<string, string> = {};
  new Headers(init?.headers).forEach((value, name) => {
    found[name] = value;
  });
  return found;
}

beforeEach(() => {
  sent = [];
  routesTo = {};
  handler = server;
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit): Promise<Response> => {
    const request: Sent = {
      url,
      method: init?.method ?? "GET",
      headers: headersOf(init),
      body: typeof init?.body === "string" ? init.body : null,
      signal: init?.signal ?? null,
      credentials: init?.credentials,
      redirect: init?.redirect,
      cache: init?.cache,
    };
    sent.push(request);
    const reply = handler(request);
    return typeof reply === "function" ? reply() : reply;
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

// ---------------------------------------------------------------------------------------------
// Clocks, timers and the page's events.

class FakeClocks {
  wall = 1_800_000_000_000;
  monotonic = 5_000;
  readonly clocks = { wall: () => this.wall, monotonic: () => this.monotonic };
  advance(ms: number): void {
    this.wall += ms;
    this.monotonic += ms;
  }
}

class FakeTimers {
  readonly set_: { run: () => void; every: number; id: number }[] = [];
  readonly cleared: unknown[] = [];
  private next = 1;
  readonly timers = {
    set: (run: () => void, every: number) => {
      const id = this.next;
      this.next += 1;
      this.set_.push({ run, every, id });
      return id;
    },
    clear: (timer: unknown) => {
      this.cleared.push(timer);
    },
  };
  live(): { run: () => void; every: number; id: number }[] {
    return this.set_.filter((timer) => !this.cleared.includes(timer.id));
  }
}

/** A target that records listeners and calls them with any object (a trusted event cannot be
 * made in jsdom: `isTrusted` is unforgeable there). */
class FakeTarget {
  readonly listeners = new Map<string, ((event: Event) => void)[]>();
  addEventListener(type: string, listener: (event: Event) => void): void {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }
  removeEventListener(): void {
    // never called
  }
  dispatchEvent(): boolean {
    return true;
  }
  fire(type: string, event: Record<string, unknown> = {}): void {
    for (const listener of this.listeners.get(type) ?? []) {
      listener({ type, isTrusted: false, ...event } as unknown as Event);
    }
  }
}

interface Rig {
  readonly client: Curator;
  readonly clocks: FakeClocks;
  readonly timers: FakeTimers;
  readonly window: FakeTarget;
  readonly document: FakeTarget;
}

function rig(): Rig {
  const clocks = new FakeClocks();
  const timers = new FakeTimers();
  const client = createCurator(clocks.clocks, timers.timers);
  const window = new FakeTarget();
  const document = new FakeTarget();
  const targets: Targets = { window, document };
  client.install(targets);
  return { client, clocks, timers, window, document };
}

async function unlocked(): Promise<Rig> {
  const made = rig();
  expect(await made.client.unlock(TOKEN, NAME)).toEqual({ kind: "unlocked" });
  return made;
}

/** An unlocked client with an open session on `d` (its handle `HANDLE`, its draft `DRAFT`). */
async function withSession(): Promise<Rig> {
  routesTo["POST /operator/datasets/d/session/open"] = () => json(200, { dataset: "d", session: 7, base: MANIFEST, draft: DRAFT, handle: HANDLE });
  const made = await unlocked();
  const opened = await made.client.open("d");
  expect(opened.kind).toBe("answer");
  expect(made.client.holds("d")).toBe(true);
  return made;
}

const bodies = () => sent.map((request) => request.body ?? "");

/** Every string a value holds, at any depth (boxes give none). */
function strings(value: unknown): string[] {
  if (typeof value === "string") {
    return [value];
  }
  if (isServerNumber(value)) {
    return [];
  }
  if (Array.isArray(value)) {
    return value.flatMap(strings);
  }
  if (typeof value === "object" && value !== null) {
    return Object.entries(value).flatMap(([key, member]) => [key, ...strings(member)]);
  }
  return [];
}

/** How many times a secret stands in a value (a count, so a failing absence prints no secret). */
const occurrences = (value: unknown): number => strings(value).filter((text) => SECRETS.some((secret) => text.includes(secret))).length;

// ---------------------------------------------------------------------------------------------

describe("unlock", () => {
  it.each([
    ["empty", ""],
    ["42 characters after the prefix", TOKEN.slice(0, -1)],
    ["44 characters after the prefix", `${TOKEN}A`],
    ["another prefix", `aibl_${TOKEN.slice(5)}`],
    ["a character outside base64url", `${TOKEN.slice(0, -1)}+`],
    ["a padded token", ` ${TOKEN}`],
    ["a handle", HANDLE],
  ])("refuses a token not in its form (%s), sending nothing", async (_case, token) => {
    const { client } = rig();
    expect(await client.unlock(token, NAME)).toEqual({ kind: "invalid", what: "token" });
    expect(sent).toEqual([]);
    expect(client.view()).toEqual({ locked: true, reason: "forgotten" });
  });

  it.each([
    ["empty", ""],
    ["the token itself", TOKEN],
    ["201 characters", "a".repeat(201)],
    ["a lone surrogate", "a\ud800"],
    ["a line break", "a\nb"],
    ["a bidi override", "a‮b"],
  ])("refuses a name the server refuses (%s), sending nothing", async (_case, name) => {
    const { client } = rig();
    expect(await client.unlock(TOKEN, name)).toEqual({ kind: "invalid", what: "name" });
    expect(sent).toEqual([]);
  });

  it("fetches the CSRF token with the token and the encoded name, and nothing else, and unlocks", async () => {
    const { client } = rig();
    expect(client.view()).toEqual({ locked: true, reason: "start" });
    expect(await client.unlock(TOKEN, NAME)).toEqual({ kind: "unlocked" });
    expect(sent).toEqual([
      {
        url: "/operator/csrf",
        method: "GET",
        headers: { accept: "application/json", authorization: `Bearer ${TOKEN}`, "aibi-operator": NAME_HEADER },
        body: null,
        signal: expect.any(AbortSignal) as AbortSignal,
        credentials: "omit",
        redirect: "error",
        cache: "no-store",
      },
    ]);
    expect(client.view()).toEqual({ locked: false, reason: null });
  });

  it.each([
    [401, refusal("TOKEN_REQUIRED"), { kind: "locked", reason: "token" }],
    [429, refusal("LIMIT_EXCEEDED", "x", { limit: { name: "token_failures", max: 10 } }), { kind: "locked", reason: "failures" }],
    [429, refusal("LIMIT_EXCEEDED", "x", { limit: { name: "operator_requests", max: 600 } }), { kind: "refused", status: 429, codes: ["LIMIT_EXCEEDED"] }],
    [400, refusal("OPERATOR_REQUIRED"), { kind: "refused", status: 400, codes: ["OPERATOR_REQUIRED"] }],
    [403, refusal("ORIGIN_NOT_ALLOWED"), { kind: "refused", status: 403, codes: ["ORIGIN_NOT_ALLOWED"] }],
    [200, { csrf: "short" }, { kind: "unknown", reason: "undecodable" }],
    [200, {}, { kind: "unknown", reason: "undecodable" }],
    [200, { csrf: 5 }, { kind: "unknown", reason: "undecodable" }],
  ])("a CSRF answer %s keeps the client locked, the token dropped", async (status, body, outcome) => {
    routesTo["GET /operator/csrf"] = () => json(status, body);
    const { client } = rig();
    expect(await client.unlock(TOKEN, NAME)).toEqual(outcome);
    expect(client.view().locked).toBe(true);
    sent = [];
    expect(await client.datasets()).toEqual({ kind: "locked", reason: client.view().reason });
    expect(sent).toEqual([]);
  });

  it("a rejected CSRF fetch is an unknown outcome, and keeps the client locked", async () => {
    handler = () => Promise.reject(new TypeError("Failed to fetch"));
    const { client } = rig();
    expect(await client.unlock(TOKEN, NAME)).toEqual({ kind: "unknown", reason: "network" });
    expect(client.view().locked).toBe(true);
    expect(sent.length).toBe(1);
  });
});

describe("what each operation sends, and where", () => {
  it("sends each operation to its route with the token, the name and the CSRF token, and its body alone", async () => {
    const { client } = await withSession();
    routesTo["POST /operator/datasets/d/session/change"] = () => json(200, { draft: DRAFT_2 });
    routesTo["POST /operator/datasets/d/session/publish"] = () => json(200, { dataset: "d", label: 2 });
    routesTo["POST /operator/datasets/d/withdraw"] = () => json(200, { dataset: "d", labels: [] });
    await client.datasets();
    await client.dataset("d");
    await client.queue("d");
    await client.change("d", [{ op: "confirm", descriptor: "t" }]);
    await client.publish("d");
    await client.withdraw("d", MANIFEST);
    const shown = sent.map((request) => [request.method, request.url, request.body]);
    expect(shown).toEqual([
      ["GET", "/operator/csrf", null],
      ["POST", "/operator/datasets/d/session/open", "{}"],
      ["GET", "/operator/datasets", null],
      ["GET", "/operator/datasets/d", null],
      ["GET", "/operator/datasets/d/queue", null],
      ["POST", "/operator/datasets/d/session/change", `{"handle":"${HANDLE}","expected":"${DRAFT}","edits":[{"op":"confirm","descriptor":"t"}]}`],
      ["POST", "/operator/datasets/d/session/publish", `{"handle":"${HANDLE}","expected":"${DRAFT_2}"}`],
      ["POST", "/operator/datasets/d/withdraw", `{"release":"${MANIFEST}"}`],
    ]);
    for (const request of sent.slice(1)) {
      expect(request.headers).toEqual({
        accept: "application/json",
        authorization: `Bearer ${TOKEN}`,
        "aibi-operator": NAME_HEADER,
        "aibi-csrf": CSRF,
        ...(request.method === "POST" ? { "content-type": "application/json" } : {}),
      });
      expect([request.credentials, request.redirect, request.cache]).toEqual(["omit", "error", "no-store"]);
    }
  });

  it("sends the token only to this origin's /operator/: send refuses every other route, sending nothing", async () => {
    const credentials = { token: TOKEN, operator: "Ada", csrf: null };
    const signal = new AbortController().signal;
    const elsewhere = [routes.health(), routes.search_catalog(), routes.list_analyses()];
    for (const route of elsewhere) {
      await expect(send(route, credentials, signal)).rejects.toThrow(new ClientError("notARoute"));
    }
    const lookalike = { operation: "datasets", method: "GET", url: "/operator/datasets" } as const;
    await expect(send(lookalike, credentials, signal)).rejects.toThrow(new ClientError("notARoute"));
    expect(sent).toEqual([]);
  });

  it.each([
    ["a malformed token", { token: `${TOKEN}x`, operator: "Ada", csrf: null }],
    ["a name not encoded", { token: TOKEN, operator: "Ada Lovelace", csrf: null }],
    ["lower-case hexadecimal", { token: TOKEN, operator: "%c3%a9", csrf: null }],
    ["a malformed CSRF token", { token: TOKEN, operator: "Ada", csrf: `${CSRF}\n` }],
  ])("send refuses %s in fixed words that hold none of it, sending nothing", async (_case, credentials) => {
    const refused = await send(routes.datasets(), credentials, new AbortController().signal).then(
      () => null,
      (error: unknown) => error,
    );
    expect(refused).toEqual(new ClientError("notCredential"));
    expect(refused instanceof Error ? occurrences([refused.message, refused.stack ?? "", refused.name]) : -1).toBe(0);
    expect(sent).toEqual([]);
  });

  it.each([["empty", ""], [".", "."], ["..", ".."], ["a lone surrogate", "d\udc00"]])("refuses a dataset name no route carries (%s), sending nothing", async (_case, dataset) => {
    const { client } = await unlocked();
    sent = [];
    for (const outcome of [
      await client.dataset(dataset),
      await client.queue(dataset),
      await client.open(dataset),
      await client.change(dataset, [{ op: "confirm", descriptor: "t" }]),
      await client.publish(dataset),
      await client.discard(dataset),
      await client.withdraw(dataset, MANIFEST),
    ]) {
      expect(outcome).toEqual({ kind: "invalid", what: "dataset" });
    }
    expect(sent).toEqual([]);
  });

  it.each([["a label", "2"], ["upper-case hex", `sha256:${"C".repeat(64)}`], ["63 digits", MANIFEST.slice(0, -1)], ["empty", ""], ["a number", 2 as never]])(
    "withdraws by a manifest alone, refusing %s unsent",
    async (_case, manifest) => {
      const { client } = await unlocked();
      sent = [];
      expect(await client.withdraw("d", manifest)).toEqual({ kind: "invalid", what: "manifest" });
      expect(sent).toEqual([]);
    },
  );
});

describe("the secrets", () => {
  it("are in no outcome, view or log: an open's answer comes without its handle; the CSRF answer is never given", async () => {
    const log = vi.spyOn(console, "log");
    const warn = vi.spyOn(console, "warn");
    const error = vi.spyOn(console, "error");
    const { client } = rig();
    const outcomes: unknown[] = [await client.unlock(TOKEN, NAME)];
    routesTo["POST /operator/datasets/d/session/open"] = () => json(200, { dataset: "d", session: 7, base: MANIFEST, draft: DRAFT, handle: HANDLE });
    routesTo["POST /operator/datasets/d/session/change"] = () => json(409, refusal("CONFLICT"));
    const opened = await client.open("d");
    outcomes.push(opened, await client.change("d", [{ op: "confirm", descriptor: "t" }]), await client.datasets(), client.view());
    expect(opened.kind === "answer" ? strings(opened.body).sort() : []).toEqual(["base", "dataset", "draft", "session", "d", MANIFEST, DRAFT].sort());
    expect(occurrences(outcomes)).toBe(0);
    expect(log.mock.calls.length + warn.mock.calls.length + error.mock.calls.length).toBe(0);
  });

  it("travel where they belong alone: the token and the CSRF token in their headers, the handle in the bodies of change, publish and discard", async () => {
    const { client } = await withSession();
    routesTo["POST /operator/datasets/d/session/change"] = () => json(200, { draft: DRAFT_2 });
    routesTo["POST /operator/datasets/d/session/discard"] = () => json(200, { dataset: "d", outcome: "discarded" });
    await client.change("d", [{ op: "remove", descriptor: "t", pointer: "/label" }]);
    await client.discard("d");
    await client.datasets();
    for (const request of sent) {
      const route = request.url.replace(/^\/operator\/datasets\/d\/session\//u, "");
      expect(occurrences([request.url])).toBe(0);
      expect(occurrences(Object.entries(request.headers).filter(([name]) => name !== "authorization" && name !== "aibi-csrf"))).toBe(0);
      expect(request.headers["authorization"] === `Bearer ${TOKEN}`).toBe(true);
      const handles = (request.body ?? "").split(HANDLE).length - 1;
      expect([route, handles]).toEqual([route, ["change", "discard", "publish"].includes(route) ? 1 : 0]);
    }
  });
});

describe("the handle rules", () => {
  it.each(["change", "publish", "discard"] as const)("%s: a CONFLICT or NO_SESSION drops the handle, whatever the message says", async (operation) => {
    for (const code of ["CONFLICT", "NO_SESSION"]) {
      for (const message of ["The handle is stale", "The draft moved on", "", "INVALID_VALUE"]) {
        sent = [];
        const { client } = await withSession();
        routesTo[`POST /operator/datasets/d/session/${operation}`] = () => json(409, refusal(code, message));
        const outcome = operation === "change" ? await client.change("d", [{ op: "confirm", descriptor: "t" }]) : await client[operation]("d");
        expect(outcome).toMatchObject({ kind: "refused", status: 409, codes: [code] });
        expect(client.holds("d")).toBe(false);
        sent = [];
        expect(await client.change("d", [{ op: "confirm", descriptor: "t" }])).toEqual({ kind: "invalid", what: "session" });
        expect(sent).toEqual([]);
      }
    }
  });

  it.each(REFUSAL_CODES.filter((code) => code !== "CONFLICT" && code !== "NO_SESSION" && code !== "CSRF_REQUIRED"))(
    "keeps the handle after %s, even with a message that names CONFLICT",
    async (code) => {
      const { client } = await withSession();
      routesTo["POST /operator/datasets/d/session/change"] = () => json(422, refusal(code, "CONFLICT NO_SESSION: the handle is stale"));
      const outcome = await client.change("d", [{ op: "confirm", descriptor: "t" }]);
      expect(outcome).toMatchObject({ kind: "refused", codes: [code] });
      expect(client.holds("d")).toBe(true);
    },
  );

  it("publish and discard drop the handle when they succeed; a change keeps it and sends its new draft next", async () => {
    for (const end of ["publish", "discard"] as const) {
      const { client } = await withSession();
      routesTo[`POST /operator/datasets/d/session/${end}`] = () => json(200, {});
      routesTo["POST /operator/datasets/d/session/change"] = () => json(200, { draft: DRAFT_2 });
      expect((await client.change("d", [{ op: "confirm", descriptor: "t" }])).kind).toBe("answer");
      expect(client.holds("d")).toBe(true);
      expect((await client[end]("d")).kind).toBe("answer");
      expect(client.holds("d")).toBe(false);
      expect(sent.at(-1)?.body).toBe(`{"handle":"${HANDLE}","expected":"${DRAFT_2}"}`);
    }
  });

  it("an answer to open or change without a handle or a draft in form is an unknown outcome, and nothing is held", async () => {
    routesTo["POST /operator/datasets/d/session/open"] = () => json(200, { dataset: "d", session: 7, base: MANIFEST, draft: DRAFT, handle: "ses_short" });
    const { client } = await unlocked();
    expect(await client.open("d")).toEqual({ kind: "unknown", reason: "undecodable" });
    expect(client.holds("d")).toBe(false);
    const other = await withSession();
    routesTo["POST /operator/datasets/d/session/change"] = () => json(200, { draft: 5 });
    expect(await other.client.change("d", [{ op: "confirm", descriptor: "t" }])).toEqual({ kind: "unknown", reason: "undecodable" });
    expect(other.client.holds("d")).toBe(false);
  });

  it("refuses a change, a publish or a discard without a session, sending nothing", async () => {
    const { client } = await unlocked();
    sent = [];
    expect(await client.change("d", [{ op: "confirm", descriptor: "t" }])).toEqual({ kind: "invalid", what: "session" });
    expect(await client.publish("d")).toEqual({ kind: "invalid", what: "session" });
    expect(await client.discard("d")).toEqual({ kind: "invalid", what: "session" });
    expect(sent).toEqual([]);
  });

  it("a late CONFLICT drops only the session its request was sent with, never one opened since in the same epoch", async () => {
    const { client } = await withSession();
    let release: ((response: Response) => void) | undefined;
    routesTo["POST /operator/datasets/d/session/change"] = () =>
      new Promise<Response>((resolve) => {
        release = resolve;
      });
    routesTo["POST /operator/datasets/d/session/publish"] = () => json(200, { dataset: "d", label: 2 });
    const pending = client.change("d", [{ op: "confirm", descriptor: "t" }]);
    await vi.waitFor(() => {
      expect(release).toBeDefined();
    });
    expect((await client.publish("d")).kind).toBe("answer");
    routesTo["POST /operator/datasets/d/session/open"] = () => json(200, { dataset: "d", session: 8, base: MANIFEST, draft: DRAFT_2, handle: HANDLE_2 });
    expect((await client.open("d")).kind).toBe("answer");
    release?.(json(409, refusal("CONFLICT")));
    expect(await pending).toMatchObject({ kind: "refused", codes: ["CONFLICT"] });
    expect(client.holds("d")).toBe(true);
    routesTo["POST /operator/datasets/d/session/change"] = () => json(200, { draft: DRAFT });
    sent = [];
    await client.change("d", [{ op: "confirm", descriptor: "t" }]);
    expect(sent[0]?.body?.includes(HANDLE_2)).toBe(true);
  });

  it("holds each dataset's handle apart", async () => {
    const { client } = await withSession();
    routesTo["POST /operator/datasets/e/session/open"] = () => json(200, { dataset: "e", session: 8, base: MANIFEST, draft: DRAFT_2, handle: HANDLE_2 });
    routesTo["POST /operator/datasets/e/session/change"] = () => json(409, refusal("CONFLICT"));
    await client.open("e");
    await client.change("e", [{ op: "confirm", descriptor: "t" }]);
    expect([client.holds("d"), client.holds("e")]).toEqual([true, false]);
  });
});

describe("the edits a change carries", () => {
  const change = async (edits: unknown): Promise<{ outcome: Outcome; body: string | null }> => {
    const { client } = await withSession();
    routesTo["POST /operator/datasets/d/session/change"] = () => json(200, { draft: DRAFT_2 });
    sent = [];
    const outcome = await client.change("d", edits as readonly Edit[]);
    return { outcome, body: sent[0]?.body ?? null };
  };

  it.each([
    ["remove", { op: "remove", descriptor: "t", pointer: "/label" }],
    ["confirm", { op: "confirm", descriptor: "t" }],
    ["confirm with evidence and pointers", { op: "confirm", descriptor: "t", evidence: "seen", pointers: ["/a", "/b"] }],
    ["confirm with no pointers", { op: "confirm", descriptor: "t", pointers: [] }],
    ["remove_descriptor", { op: "remove_descriptor", descriptor: "t.r" }],
  ])("sends %s as it is", async (_case, edit) => {
    const { outcome, body } = await change([edit]);
    expect(outcome.kind).toBe("answer");
    expect(body).toBe(`{"handle":"${HANDLE}","expected":"${DRAFT}","edits":[${JSON.stringify(edit)}]}`);
  });

  it.each([
    ["set", { op: "set", descriptor: "t", pointer: "/label", value: 1 }],
    ["put", { op: "put", descriptor: { id: "t" } }],
    ["accept", { op: "accept", proposal: 1 }],
    ["an unknown kind", { op: "drop", descriptor: "t" }],
    ["no kind", { descriptor: "t" }],
    ["a number for a string", { op: "remove_descriptor", descriptor: 1 }],
    ["a number for a pointer", { op: "remove", descriptor: "t", pointer: 1 }],
    ["a box for a pointer", { op: "remove", descriptor: "t", pointer: Object.freeze({}) }],
    ["a number for the kind", { op: 1, descriptor: "t" }],
    ["a box for a string", { op: "remove_descriptor", descriptor: Object.freeze({}) }],
    ["a member of another kind", { op: "remove_descriptor", descriptor: "t", pointer: "/a" }],
    ["a member no kind has", { op: "confirm", descriptor: "t", proposal: 1 }],
    ["a number among the pointers", { op: "confirm", descriptor: "t", pointers: ["/a", 1] }],
    ["pointers that are no list", { op: "confirm", descriptor: "t", pointers: "/a" }],
    ["evidence that is no string", { op: "confirm", descriptor: "t", evidence: 1 }],
    ["a missing pointer", { op: "remove", descriptor: "t" }],
    ["a missing descriptor", { op: "confirm" }],
    ["null", null],
    ["a list", ["confirm"]],
  ])("refuses %s, sending nothing", async (_case, edit) => {
    const { outcome, body } = await change([edit]);
    expect(outcome).toEqual({ kind: "invalid", what: "edits" });
    expect(body).toBeNull();
  });

  it("refuses no edit and more than 256, and sends exactly 256", async () => {
    const edit = { op: "confirm", descriptor: "t" };
    expect(EDIT_LIMIT).toBe(256);
    expect((await change([])).outcome).toEqual({ kind: "invalid", what: "edits" });
    expect((await change(Array.from({ length: 257 }, () => edit))).outcome).toEqual({ kind: "invalid", what: "edits" });
    expect((await change("confirm")).outcome).toEqual({ kind: "invalid", what: "edits" });
    expect((await change(Array.from({ length: 256 }, () => edit))).outcome.kind).toBe("answer");
  });

  it("reads each member once, and sends the copy (a getter that changes is read once)", async () => {
    let reads = 0;
    const edit = {
      op: "remove_descriptor",
      get descriptor(): string {
        reads += 1;
        return reads === 1 ? "t" : "changed";
      },
    };
    const { body } = await change([edit]);
    expect(reads).toBe(1);
    expect(body).toContain('"descriptor":"t"');
  });
});

describe("CSRF", () => {
  it("on CSRF_REQUIRED, fetches the CSRF token again and sends the request once more with it, the same body", async () => {
    const { client } = await withSession();
    let changes = 0;
    routesTo["POST /operator/datasets/d/session/change"] = () => {
      changes += 1;
      return changes === 1 ? json(403, refusal("CSRF_REQUIRED")) : json(200, { draft: DRAFT_2 });
    };
    routesTo["GET /operator/csrf"] = () => json(200, { csrf: CSRF_2 });
    sent = [];
    expect((await client.change("d", [{ op: "confirm", descriptor: "t" }])).kind).toBe("answer");
    expect(sent.map((request) => [request.method, request.url, request.headers["aibi-csrf"] ?? null])).toEqual([
      ["POST", "/operator/datasets/d/session/change", CSRF],
      ["GET", "/operator/csrf", null],
      ["POST", "/operator/datasets/d/session/change", CSRF_2],
    ]);
    expect(sent[0]?.body).toBe(sent[2]?.body);
    sent = [];
    await client.datasets();
    expect(sent[0]?.headers["aibi-csrf"]).toBe(CSRF_2);
  });

  it("sends it once more only: a second CSRF_REQUIRED is the outcome", async () => {
    const { client } = await withSession();
    routesTo["POST /operator/datasets/d/session/change"] = () => json(403, refusal("CSRF_REQUIRED"));
    sent = [];
    expect(await client.change("d", [{ op: "confirm", descriptor: "t" }])).toMatchObject({ kind: "refused", status: 403, codes: ["CSRF_REQUIRED"] });
    expect(sent.map((request) => request.url)).toEqual(["/operator/datasets/d/session/change", "/operator/csrf", "/operator/datasets/d/session/change"]);
    expect(client.holds("d")).toBe(true);
  });

  it.each(REFUSAL_CODES.filter((code) => code !== "CSRF_REQUIRED"))("never sends again after a 403 %s", async (code) => {
    const { client } = await unlocked();
    routesTo["GET /operator/datasets"] = () => json(403, refusal(code, "CSRF_REQUIRED: send the CSRF token"));
    sent = [];
    expect(await client.datasets()).toMatchObject({ kind: "refused", status: 403, codes: [code] });
    expect(sent.length).toBe(1);
  });

  it.each([400, 401, 409, 422, 429])("does not take CSRF_REQUIRED at status %s for the refusal it names", async (status) => {
    const { client } = await unlocked();
    routesTo["GET /operator/datasets"] = () => json(status, refusal("CSRF_REQUIRED"));
    sent = [];
    await client.datasets();
    expect(sent.length).toBe(1);
  });

  it("a refused token while the CSRF token is fetched again locks, and sends nothing more", async () => {
    const { client } = await withSession();
    routesTo["POST /operator/datasets/d/session/change"] = () => json(403, refusal("CSRF_REQUIRED"));
    routesTo["GET /operator/csrf"] = () => json(401, refusal("TOKEN_REQUIRED"));
    sent = [];
    expect(await client.change("d", [{ op: "confirm", descriptor: "t" }])).toEqual({ kind: "locked", reason: "token" });
    expect(sent.length).toBe(2);
    expect(client.holds("d")).toBe(false);
  });

  it("a forget while the CSRF token is fetched again drops the retry", async () => {
    const { client } = await withSession();
    routesTo["POST /operator/datasets/d/session/change"] = () => json(403, refusal("CSRF_REQUIRED"));
    routesTo["GET /operator/csrf"] = () => {
      client.forget();
      return json(200, { csrf: CSRF_2 });
    };
    sent = [];
    expect(await client.change("d", [{ op: "confirm", descriptor: "t" }])).toEqual({ kind: "unknown", reason: "stale" });
    expect(sent.length).toBe(2);
  });

  it("is dropped on a lock: a new unlock fetches a new one", async () => {
    const { client } = await unlocked();
    client.forget();
    routesTo["GET /operator/csrf"] = () => json(200, { csrf: CSRF_2 });
    await client.unlock(TOKEN, NAME);
    sent = [];
    await client.datasets();
    expect(sent[0]?.headers["aibi-csrf"]).toBe(CSRF_2);
  });
});

describe("statuses and limits", () => {
  /** The statuses a body may come with (a 204, a 205 and a 304 have none). */
  const NO_BODY = [204, 205, 304];
  const statuses = Array.from({ length: 400 }, (_, index) => 200 + index).filter((status) => !NO_BODY.includes(status));

  it.each(NO_BODY)("an answer %s, which has no body, is an unknown outcome, not sent again", async (status) => {
    const { client } = await unlocked();
    routesTo["GET /operator/datasets"] = () => new Response(null, { status, headers: { "Content-Type": "application/json" } });
    sent = [];
    expect(await client.datasets()).toEqual({ kind: "unknown", reason: "undecodable" });
    expect(sent.length).toBe(1);
    expect(client.view().locked).toBe(false);
  });

  it("decides on every status from 200 to 599 by the status alone (and a 403's code, a 429's limit)", async () => {
    const wrong: number[] = [];
    for (const status of statuses) {
      const { client } = await unlocked();
      routesTo["GET /operator/datasets"] = () => json(status, refusal("SOMETHING", "TOKEN_REQUIRED CSRF_REQUIRED token_failures"));
      sent = [];
      const outcome = await client.datasets();
      const expected = status === 401 ? { kind: "locked", reason: "token" } : status <= 299 ? { kind: "answer", status } : { kind: "refused", status, codes: ["SOMETHING"] };
      const agrees = Object.entries(expected).every(([key, value]) => (outcome as Record<string, unknown>)[key] === value || JSON.stringify((outcome as Record<string, unknown>)[key]) === JSON.stringify(value));
      if (!agrees || sent.length !== 1 || client.view().locked !== (status === 401)) {
        wrong.push(status);
      }
    }
    expect(wrong).toEqual([]);
  });

  it.each([
    ["token_failures", true],
    ["operator_requests", false],
    ["api_requests", false],
    ["concurrent_imports", false],
    ["TOKEN_FAILURES", false],
    ["token_failure", false],
  ])("a 429 naming %s locks: %s", async (name, locks) => {
    const { client } = await withSession();
    routesTo["GET /operator/datasets"] = () => json(429, refusal("LIMIT_EXCEEDED", "token_failures", { limit: { name, max: 10 } }), { "Retry-After": "6" });
    sent = [];
    const outcome = await client.datasets();
    expect(outcome).toEqual(locks ? { kind: "locked", reason: "failures" } : expect.objectContaining({ kind: "refused", status: 429 }));
    expect(client.view().locked).toBe(locks);
    expect(client.holds("d")).toBe(!locks);
    expect(sent.length).toBe(1);
  });

  it("a 401 locks: the token, the CSRF token and the handles are gone, and nothing more is sent", async () => {
    const { client } = await withSession();
    routesTo["GET /operator/datasets"] = () => json(401, refusal("TOKEN_REQUIRED"), { "WWW-Authenticate": 'Bearer realm="aibi-operator"' });
    expect(await client.datasets()).toEqual({ kind: "locked", reason: "token" });
    expect(client.view()).toEqual({ locked: true, reason: "token" });
    expect(client.holds("d")).toBe(false);
    sent = [];
    expect(await client.dataset("d")).toEqual({ kind: "locked", reason: "token" });
    expect(await client.publish("d")).toEqual({ kind: "invalid", what: "session" });
    expect(sent).toEqual([]);
  });

  it("a 503 or a 429 of another limit is never sent again", async () => {
    for (const [status, name] of [[503, "concurrent_imports"], [429, "operator_requests"]] as const) {
      const { client } = await withSession();
      routesTo["POST /operator/datasets/d/session/publish"] = () => json(status, refusal("LIMIT_EXCEEDED", "", { limit: { name, max: 2 } }), { "Retry-After": "1" });
      sent = [];
      expect(await client.publish("d")).toMatchObject({ kind: "refused", status });
      expect(sent.length).toBe(1);
    }
  });
});

describe("unknown outcomes are never sent again", () => {
  const cases: readonly (readonly [string, () => Reply, string])[] = [
    ["a rejected fetch", () => Promise.reject(new TypeError("Failed to fetch")), "network"],
    ["a body cut off", () => new Response(new ReadableStream({ start(controller) { controller.enqueue(new TextEncoder().encode('{"a"')); controller.error(new TypeError("reset")); } }), { status: 200, headers: { "Content-Type": "application/json" } }), "interrupted"],
    ["a body over the cap", () => new Response("{}", { status: 200, headers: { "Content-Type": "application/json", "Content-Length": String(RESPONSE_CAP_BYTES + 1) } }), "tooLarge"],
    ["an answer that is not JSON", () => new Response("<html>", { status: 502, headers: { "Content-Type": "text/html" } }), "notJson"],
    ["an answer that is not UTF-8", () => new Response(new Uint8Array([0x7b, 0xff, 0x7d]), { status: 200, headers: { "Content-Type": "application/json" } }), "notUtf8"],
    ["an answer the decoder refuses", () => new Response("{", { status: 200, headers: { "Content-Type": "application/json" } }), "undecodable"],
  ];
  const operations = ["datasets", "open", "change", "publish", "discard", "withdraw"] as const;

  it.each(cases.flatMap(([name, reply, reason]) => operations.map((operation) => [name, operation, reply, reason] as const)))("%s, for %s", async (_name, operation, reply, reason) => {
    const { client } = await withSession();
    handler = (request) => (request.url === "/operator/csrf" ? json(200, { csrf: CSRF }) : reply());
    sent = [];
    const run = {
      datasets: () => client.datasets(),
      open: () => client.open("d"),
      change: () => client.change("d", [{ op: "confirm", descriptor: "t" }]),
      publish: () => client.publish("d"),
      discard: () => client.discard("d"),
      withdraw: () => client.withdraw("d", MANIFEST),
    }[operation];
    expect(await run()).toEqual({ kind: "unknown", reason });
    expect(sent.length).toBe(1);
    expect(client.view().locked).toBe(false);
    if (operation !== "open") {
      expect(client.holds("d")).toBe(true);
    }
  });

  it("a request aborted by a lock is unknown, and the signal it was sent with is aborted", async () => {
    const { client } = await withSession();
    let release: (() => void) | undefined;
    routesTo["POST /operator/datasets/d/session/publish"] = (request) =>
      new Promise<Response>((_resolve, reject) => {
        request.signal?.addEventListener("abort", () => {
          reject(new DOMException("aborted", "AbortError"));
        });
        release = () => undefined;
      });
    const pending = client.publish("d");
    await vi.waitFor(() => {
      expect(release).toBeDefined();
    });
    client.forget();
    expect(await pending).toEqual({ kind: "unknown", reason: "aborted" });
    expect(sent.at(-1)?.signal?.aborted).toBe(true);
    expect(sent.filter((request) => request.url.endsWith("/publish")).length).toBe(1);
  });
});

describe("the epoch", () => {
  /** A reply the test resolves later, ignoring any abort (a server that answers anyway). */
  function deferred(): { reply: () => Promise<Response>; resolve: (response: Response) => void; asked: () => boolean } {
    let resolve: (response: Response) => void = () => undefined;
    let asked = false;
    const promise = new Promise<Response>((done) => {
      resolve = done;
    });
    return {
      reply: () => {
        asked = true;
        return promise;
      },
      resolve: (response) => {
        resolve(response);
      },
      asked: () => asked,
    };
  }

  it("a late open, answered after a forget, does not refill the handles, now or after a new unlock", async () => {
    const { client } = await unlocked();
    const late = deferred();
    routesTo["POST /operator/datasets/d/session/open"] = late.reply;
    const pending = client.open("d");
    await vi.waitFor(() => {
      expect(late.asked()).toBe(true);
    });
    client.forget();
    late.resolve(json(200, { dataset: "d", session: 7, base: MANIFEST, draft: DRAFT, handle: HANDLE }));
    expect(await pending).toEqual({ kind: "unknown", reason: "stale" });
    expect(client.holds("d")).toBe(false);
    await client.unlock(TOKEN, NAME);
    expect(client.holds("d")).toBe(false);
  });

  it("a late open whose answer arrived before the forget and whose body arrived after does not refill them either", async () => {
    const { client } = await unlocked();
    let push: (() => void) | undefined;
    routesTo["POST /operator/datasets/d/session/open"] = () =>
      new Response(
        new ReadableStream<Uint8Array>({
          start(controller) {
            push = () => {
              controller.enqueue(new TextEncoder().encode(JSON.stringify({ dataset: "d", session: 7, base: MANIFEST, draft: DRAFT, handle: HANDLE })));
              controller.close();
            };
          },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    const pending = client.open("d");
    await vi.waitFor(() => {
      expect(push).toBeDefined();
    });
    client.forget();
    push?.();
    const outcome = await pending;
    expect(outcome.kind).toBe("unknown");
    expect(client.holds("d")).toBe(false);
  });

  it("a late CSRF answer of an unlock that was forgotten does not unlock", async () => {
    const { client } = rig();
    const late = deferred();
    routesTo["GET /operator/csrf"] = late.reply;
    const pending = client.unlock(TOKEN, NAME);
    await vi.waitFor(() => {
      expect(late.asked()).toBe(true);
    });
    client.forget();
    late.resolve(json(200, { csrf: CSRF }));
    expect(await pending).toEqual({ kind: "unknown", reason: "stale" });
    expect(client.view()).toEqual({ locked: true, reason: "forgotten" });
    sent = [];
    expect((await client.datasets()).kind).toBe("locked");
    expect(sent).toEqual([]);
  });

  it("a late 401 of an earlier epoch does not lock the client a new unlock made", async () => {
    const { client } = await unlocked();
    const late = deferred();
    routesTo["GET /operator/datasets"] = late.reply;
    const pending = client.datasets();
    await vi.waitFor(() => {
      expect(late.asked()).toBe(true);
    });
    client.forget();
    await client.unlock(TOKEN, NAME);
    late.resolve(json(401, refusal("TOKEN_REQUIRED")));
    expect(await pending).toEqual({ kind: "unknown", reason: "stale" });
    expect(client.view()).toEqual({ locked: false, reason: null });
  });

  it("a late conflict of an earlier epoch drops no handle of the new one", async () => {
    const { client } = await withSession();
    const late = deferred();
    routesTo["POST /operator/datasets/d/session/change"] = late.reply;
    const pending = client.change("d", [{ op: "confirm", descriptor: "t" }]);
    await vi.waitFor(() => {
      expect(late.asked()).toBe(true);
    });
    client.forget();
    await client.unlock(TOKEN, NAME);
    routesTo["POST /operator/datasets/d/session/open"] = () => json(200, { dataset: "d", session: 9, base: MANIFEST, draft: DRAFT_2, handle: HANDLE_2 });
    await client.open("d");
    late.resolve(json(409, refusal("CONFLICT")));
    expect(await pending).toEqual({ kind: "unknown", reason: "stale" });
    expect(client.holds("d")).toBe(true);
  });

  it("an unlock forgets what an earlier unlock held: its handles and its in-flight requests", async () => {
    const { client } = await withSession();
    await client.unlock(OTHER_TOKEN, NAME);
    expect(client.holds("d")).toBe(false);
    sent = [];
    await client.datasets();
    expect(sent[0]?.headers["authorization"] === `Bearer ${OTHER_TOKEN}`).toBe(true);
  });
});

describe("the idle lock", () => {
  const offsets = [-1, 0, 1];

  it.each(offsets)("before a request, on the wall clock: the limit %s ms locks only from the limit on, and sends nothing then", async (offset) => {
    const { client, clocks } = await unlocked();
    clocks.wall += IDLE_LIMIT_MS + offset;
    sent = [];
    const outcome = await client.datasets();
    expect(outcome.kind).toBe(offset < 0 ? "refused" : "locked");
    expect(sent.length).toBe(offset < 0 ? 1 : 0);
    expect(client.view()).toEqual(offset < 0 ? { locked: false, reason: null } : { locked: true, reason: "idle" });
  });

  it.each(offsets)("before a request, on the monotonic clock alone: the limit %s ms", async (offset) => {
    const { client, clocks } = await unlocked();
    clocks.monotonic += IDLE_LIMIT_MS + offset;
    sent = [];
    expect((await client.datasets()).kind).toBe(offset < 0 ? "refused" : "locked");
    expect(sent.length).toBe(offset < 0 ? 1 : 0);
  });

  it.each([1, 60_000])("locks when the wall clock went back %s ms", async (back) => {
    const { client, clocks } = await unlocked();
    clocks.wall -= back;
    sent = [];
    expect(await client.datasets()).toEqual({ kind: "locked", reason: "idle" });
    expect(sent).toEqual([]);
  });

  it("a suspend (the wall clock jumps, no timer runs, the monotonic clock stands) locks at the next request", async () => {
    const { client, clocks, timers } = await unlocked();
    clocks.wall += 3 * 60 * 60 * 1000;
    expect(timers.live().length).toBe(1);
    expect(await client.datasets()).toEqual({ kind: "locked", reason: "idle" });
  });

  it.each(ACTIVITY_EVENTS)("a trusted %s is activity; an untrusted one is not", async (type) => {
    for (const trusted of [true, false]) {
      const { client, clocks, window } = await unlocked();
      clocks.advance(IDLE_LIMIT_MS - 1000);
      window.fire(type, { isTrusted: trusted });
      clocks.advance(2000);
      sent = [];
      expect((await client.datasets()).kind).toBe(trusted ? "refused" : "locked");
    }
  });

  it.each(["mousemove", "scroll", "focus", "click", "input", "keyup", "message"])("%s, trusted, is no activity", async (type) => {
    const { client, clocks, window, document } = await unlocked();
    clocks.advance(IDLE_LIMIT_MS - 1000);
    window.fire(type, { isTrusted: true });
    document.fire(type, { isTrusted: true });
    clocks.advance(2000);
    expect((await client.datasets()).kind).toBe("locked");
  });

  it("a request is no activity", async () => {
    const { client, clocks } = await unlocked();
    clocks.advance(IDLE_LIMIT_MS - 1000);
    expect((await client.datasets()).kind).toBe("refused");
    clocks.advance(1000);
    expect((await client.datasets()).kind).toBe("locked");
  });

  it("an input once the limit is past locks, rather than counting", async () => {
    const { client, clocks, window } = await unlocked();
    clocks.advance(IDLE_LIMIT_MS);
    window.fire("keydown", { isTrusted: true });
    expect(client.view()).toEqual({ locked: true, reason: "idle" });
  });

  it("an input after the wall clock went back locks, rather than counting", async () => {
    const { client, clocks, window } = await unlocked();
    clocks.wall -= 1;
    window.fire("pointerdown", { isTrusted: true });
    expect(client.view()).toEqual({ locked: true, reason: "idle" });
  });

  it("the timer, set while unlocked, locks at the limit without a request, and is cleared on a lock", async () => {
    const { client, clocks, timers } = await unlocked();
    const [timer] = timers.live();
    expect(timer?.every).toBe(IDLE_CHECK_MS);
    clocks.advance(IDLE_LIMIT_MS - 1);
    timer?.run();
    expect(client.view().locked).toBe(false);
    clocks.advance(1);
    timer?.run();
    expect(client.view()).toEqual({ locked: true, reason: "idle" });
    expect(timers.live()).toEqual([]);
  });

  it.each([
    ["visibilitychange", "document", {}],
    ["pageshow", "window", { persisted: false }],
  ] as const)("%s past the limit locks without a request", async (type, where, event) => {
    const made = await unlocked();
    made.clocks.advance(IDLE_LIMIT_MS);
    made[where].fire(type, event);
    expect(made.client.view()).toEqual({ locked: true, reason: "idle" });
    expect(sent.length).toBe(1);
  });

  it("pagehide locks and forgets, and aborts what is in flight", async () => {
    const { client, window } = await withSession();
    window.fire("pagehide");
    expect(client.view()).toEqual({ locked: true, reason: "page" });
    expect(client.holds("d")).toBe(false);
    expect(sent.at(-1)?.signal?.aborted).toBe(true);
  });

  it("a pageshow that restores the page from the cache locks and forgets; one that does not keeps it", async () => {
    const restored = await withSession();
    restored.window.fire("pageshow", { persisted: false });
    expect(restored.client.view().locked).toBe(false);
    restored.window.fire("pageshow", { persisted: true });
    expect(restored.client.view()).toEqual({ locked: true, reason: "restored" });
    expect(restored.client.holds("d")).toBe(false);
  });

  it("a forget locks, drops the handles and aborts what is in flight", async () => {
    const { client } = await withSession();
    client.forget();
    expect(client.view()).toEqual({ locked: true, reason: "forgotten" });
    expect(client.holds("d")).toBe(false);
    expect(sent.at(-1)?.signal?.aborted).toBe(true);
  });

  it("tells its listeners of every change of view, and stops when asked", async () => {
    const { client } = rig();
    const seen: unknown[] = [];
    const stop = client.subscribe(() => seen.push(client.view()));
    await client.unlock(TOKEN, NAME);
    client.forget();
    stop();
    await client.unlock(TOKEN, NAME);
    expect(seen).toEqual([
      { locked: true, reason: "forgotten" },
      { locked: false, reason: null },
      { locked: true, reason: "forgotten" },
    ]);
  });
});

describe("REFUSALS", () => {
  it("states the credential refusal in fixed words", () => {
    expect(REFUSALS.notCredential).toBe("A credential the operator's client sends is not in its form; it was not sent.");
  });

  it("are words of the client's alone (the bodies the fake sent are JSON)", () => {
    expect(bodies().every((body) => body === "" || body.startsWith("{"))).toBe(true);
    const value: JsonOut = null;
    expect(value).toBeNull();
  });
});

// ---------------------------------------------------------------------------------------------
// Cold code round 1 (PR #107).

describe("a change mixing valid and invalid edits is refused whole (m1)", () => {
  const good = (index: number): Edit => ({ op: "confirm", descriptor: `t${String(index)}` });
  const INVALID: readonly (readonly [string, unknown])[] = [
    ["a set", { op: "set", descriptor: "t", pointer: "/label", value: 1 }],
    ["a put", { op: "put", descriptor: { id: "t" } }],
    ["an accept", { op: "accept", proposal: 1 }],
    ["an extra member", { op: "remove_descriptor", descriptor: "t", pointer: "/a" }],
    ["a number", 1],
    ["null", null],
    ["a number for a string", { op: "remove_descriptor", descriptor: 1 }],
  ];
  const cells = [1, 2, 3, 4].flatMap((valid) => Array.from({ length: valid + 1 }, (_, position) => [valid, position] as const));

  it.each(INVALID.flatMap(([kind, edit]) => cells.map(([valid, position]) => [kind, valid, position, edit] as const)))(
    "%s among %i valid edits, at position %i, is refused, and not even the valid ones are sent",
    async (_kind, valid, position, edit) => {
      const { client } = await withSession();
      routesTo["POST /operator/datasets/d/session/change"] = () => json(200, { draft: DRAFT_2 });
      const edits: unknown[] = Array.from({ length: valid }, (_, index) => good(index));
      edits.splice(position, 0, edit);
      sent = [];
      expect(await client.change("d", edits as readonly Edit[])).toEqual({ kind: "invalid", what: "edits" });
      expect(sent).toEqual([]);
    },
  );

  it("sends the same valid edits when none is invalid", async () => {
    const { client } = await withSession();
    routesTo["POST /operator/datasets/d/session/change"] = () => json(200, { draft: DRAFT_2 });
    sent = [];
    expect((await client.change("d", [good(0), good(1), good(2)])).kind).toBe("answer");
    expect(JSON.parse(sent[0]?.body ?? "{}")).toMatchObject({ edits: [good(0), good(1), good(2)] });
  });
});

describe("send sends to a URL under /operator/ that no parser can move (m2)", () => {
  const credentials = { token: TOKEN, operator: "Ada", csrf: null };
  const signal = new AbortController().signal;

  it.each([
    ["a relative path", "operator/datasets"],
    ["a protocol-relative URL", "//h/operator/x"],
    ["a protocol-relative URL that ends under /operator/", "//operator/x"],
    ["another origin", "https://elsewhere.example/operator/x"],
    ["another scheme", "javascript:/operator/x"],
    ["a path that climbs out", "/operator/../api/health"],
    ["a path that climbs out, encoded", "/operator/%2e%2e/api/health"],
    ["a path that climbs out, encoded in upper case", "/operator/%2E%2E/api/health"],
    ["a path that climbs out, half encoded", "/operator/.%2e/api/health"],
    ["a path that climbs out, half encoded the other way", "/operator/%2E./api/health"],
    ["a path with a dot segment", "/operator/./datasets"],
    ["a path with an encoded dot segment", "/operator/%2e/datasets"],
    ["a path that climbs at its end", "/operator/datasets/.."],
    ["a path that climbs before a query", "/operator/datasets/..?q=1"],
    ["a path that climbs before a fragment", "/operator/datasets/%2e%2e#x"],
    ["a doubled slash", "/operator//datasets"],
    ["a doubled slash in the query", "/operator/datasets?next=http://elsewhere.example"],
    ["a backslash", "/operator/\\elsewhere"],
    ["an encoded-looking backslash raw", "/operator/a\\b"],
    ["a space", "/operator/a b"],
    ["a tab, which a parser removes", "/operator/.\t./x"],
    ["a newline, which a parser removes", "/operator/.\n./x"],
    ["a non-ASCII character", "/operator/é"],
    ["a path outside /operator/", "/api/health"],
    ["the bare /operator", "/operator"],
    ["another case", "/OPERATOR/datasets"],
    ["an empty URL", ""],
  ])("refuses %s, sending nothing", async (_case, url) => {
    await expect(send(route("datasets", "GET", url), credentials, signal)).rejects.toThrow(new ClientError("notARoute"));
    expect(sent).toEqual([]);
  });

  it.each([
    ["a plain path", "/operator/datasets"],
    ["a path ending in a slash", "/operator/datasets/"],
    ["an encoded segment", "/operator/datasets/a%20b"],
    ["three dots", "/operator/datasets/..."],
    ["three encoded dots", "/operator/datasets/%2e%2e%2e"],
    ["an encoded percent before dots", "/operator/datasets/%252e%252e"],
    ["dots inside a segment", "/operator/datasets/a..b"],
    ["dots in a query", "/operator/datasets?path=../x"],
  ])("sends %s", async (_case, url) => {
    expect((await send(route("datasets", "GET", url), credentials, signal)).kind).toBe("answer");
    expect(sent.map((request) => request.url)).toEqual([url]);
  });
});

describe("an open answer's members are an allow-list, and no handle is nested in one (m6)", () => {
  const OPENED = { dataset: "d", session: 7, base: MANIFEST, draft: DRAFT };
  const opening = async (extra: Record<string, unknown>): Promise<{ client: Curator; outcome: Outcome }> => {
    routesTo["POST /operator/datasets/d/session/open"] = () => json(200, { ...OPENED, handle: HANDLE, ...extra });
    const { client } = await unlocked();
    return { client, outcome: await client.open("d") };
  };
  const keysOf = (outcome: Outcome): string[] => (outcome.kind === "answer" && typeof outcome.body === "object" && outcome.body !== null ? Object.keys(outcome.body).sort() : []);

  it.each([
    ["a nested handle", { nested: { handle: HANDLE_2 } }],
    ["a deeper nested handle", { nested: { a: [{ b: { handle: HANDLE_2 } }] } }],
    ["a handle in a list", { listed: [{ handle: HANDLE_2 }, "x"] }],
    ["a member the schema does not have", { extra: "x", other: 1 }],
    ["a handle-shaped value in another member", { token_like: HANDLE_2 }],
    ["an empty extra", {}],
  ])("gives only dataset, session, base and draft, whatever else it holds: %s", async (_case, extra) => {
    const { client, outcome } = await opening(extra);
    expect(keysOf(outcome)).toEqual(["base", "dataset", "draft", "session"]);
    expect(occurrences(outcome)).toBe(0);
    expect(client.holds("d")).toBe(true);
  });

  it.each([
    ["an object", { k: { handle: HANDLE_2, kept: "x" } }, { k: { kept: "x" } }],
    ["a list", [{ handle: HANDLE_2 }, { v: [{ handle: HANDLE_2 }] }], [{}, { v: [{}] }]],
  ])("drops a handle nested in an allowed member: %s", async (_case, base, expected) => {
    const { outcome } = await opening({ base });
    expect(outcome.kind === "answer" && typeof outcome.body === "object" && outcome.body !== null ? (outcome.body as Record<string, unknown>)["base"] : "no").toEqual(expected);
    expect(occurrences(outcome)).toBe(0);
  });

  it("gives a frozen copy", async () => {
    const { outcome } = await opening({ nested: { handle: HANDLE_2 } });
    expect(outcome.kind === "answer" && Object.isFrozen(outcome.body)).toBe(true);
  });
});

describe("the client object's members are pinned, and no member's result holds a secret (m7)", () => {
  const MEMBERS = ["change", "datasets", "dataset", "discard", "forget", "holds", "install", "open", "publish", "queue", "subscribe", "unlock", "view", "withdraw"].sort();

  it("has exactly these own members, plain methods, on a plain object", () => {
    const { client } = rig();
    expect(Reflect.ownKeys(client).sort()).toEqual(MEMBERS);
    expect(Object.getPrototypeOf(client)).toBe(Object.prototype);
    for (const [name, descriptor] of Object.entries(Object.getOwnPropertyDescriptors(client))) {
      expect([name, typeof descriptor.value, Reflect.has(descriptor, "get")]).toEqual([name, "function", false]);
    }
  });

  const STATES: readonly (readonly [string, () => Promise<Rig>])[] = [
    ["a client never unlocked", async () => Promise.resolve(rig())],
    ["an unlocked client", unlocked],
    ["a client with an open session", withSession],
    [
      "a forgotten client",
      async () => {
        const made = await withSession();
        made.client.forget();
        return made;
      },
    ],
    [
      "a client locked by a 401",
      async () => {
        const made = await withSession();
        routesTo["GET /operator/datasets"] = () => json(401, refusal("TOKEN_REQUIRED"));
        await made.client.datasets();
        return made;
      },
    ],
    [
      "a client locked when idle",
      async () => {
        const made = await withSession();
        made.clocks.advance(IDLE_LIMIT_MS);
        await made.client.datasets();
        return made;
      },
    ],
  ];
  const CALLS: readonly (readonly unknown[])[] = [
    [],
    ["d"],
    ["d", MANIFEST],
    ["d", [{ op: "confirm", descriptor: "t" }]],
    [TOKEN, NAME],
    [OTHER_TOKEN, NAME],
    [() => undefined],
    [{ window: new FakeTarget(), document: new FakeTarget() }],
  ];

  /** The names of the members a client has, found, not listed: a member added is called here too. */
  const FOUND = Reflect.ownKeys(createCurator()).filter((key): key is string => typeof key === "string");

  it.each(STATES.flatMap(([state, make]) => FOUND.map((member) => [state, member, make] as const)))("%s: %s, called every way, gives no secret", async (_state, member, make) => {
    for (const call of CALLS) {
      const { client } = await make();
      const fn: unknown = Reflect.get(client, member);
      expect(typeof fn).toBe("function");
      let result: unknown;
      try {
        result = await Reflect.apply(fn as () => unknown, client, call);
      } catch (error) {
        result = error instanceof Error ? [error.name, error.message, error.stack ?? ""] : error;
      }
      expect(occurrences([result, typeof result === "function" ? [String(result), Object.keys(result)] : []])).toBe(0);
      expect(occurrences([client.view(), JSON.stringify(client), Object.keys(client)])).toBe(0);
    }
  });
});

describe("an unlock can be cancelled, and then holds nothing (m10)", () => {
  /** A fetch that never answers, rejecting when its request is aborted (as a real one does). */
  const hanging: Handler = (request) =>
    new Promise<Response>((_resolve, reject) => {
      if (request.signal?.aborted === true) {
        reject(new DOMException("aborted", "AbortError"));
      }
      request.signal?.addEventListener("abort", () => {
        reject(new DOMException("aborted", "AbortError"));
      });
    });

  it("a hanging unlock is cancelled: unknown (aborted), locked, holding nothing, sending nothing more", async () => {
    handler = hanging;
    const { client, timers } = rig();
    const cancel = new AbortController();
    const pending = client.unlock(TOKEN, NAME, cancel.signal);
    await vi.waitFor(() => {
      expect(sent.length).toBe(1);
    });
    cancel.abort();
    expect(await pending).toEqual({ kind: "unknown", reason: "aborted" });
    expect(client.view()).toEqual({ locked: true, reason: "forgotten" });
    expect(timers.live()).toEqual([]);
    expect(sent[0]?.signal?.aborted).toBe(true);
    sent = [];
    expect(await client.datasets()).toEqual({ kind: "locked", reason: "forgotten" });
    expect(await client.open("d")).toEqual({ kind: "locked", reason: "forgotten" });
    expect(client.holds("d")).toBe(false);
    expect(sent).toEqual([]);
  });

  it("a cancelled unlock does not poison the next one", async () => {
    handler = hanging;
    const { client } = rig();
    const cancel = new AbortController();
    const pending = client.unlock(TOKEN, NAME, cancel.signal);
    await vi.waitFor(() => {
      expect(sent.length).toBe(1);
    });
    cancel.abort();
    await pending;
    handler = server;
    expect(await client.unlock(TOKEN, NAME)).toEqual({ kind: "unlocked" });
  });

  it("a signal aborted before the unlock starts cancels it", async () => {
    handler = hanging;
    const { client } = rig();
    const cancel = new AbortController();
    cancel.abort();
    expect(await client.unlock(TOKEN, NAME, cancel.signal)).toEqual({ kind: "unknown", reason: "aborted" });
    expect(client.view().locked).toBe(true);
  });

  it("an answer that comes after the cancel, from a server that ignores the abort, is not kept", async () => {
    let answer: (response: Response) => void = () => undefined;
    handler = () =>
      new Promise<Response>((done) => {
        answer = done;
      });
    const { client, timers } = rig();
    const cancel = new AbortController();
    const pending = client.unlock(TOKEN, NAME, cancel.signal);
    await vi.waitFor(() => {
      expect(sent.length).toBe(1);
    });
    cancel.abort();
    answer(json(200, { csrf: CSRF }));
    expect(await pending).toEqual({ kind: "unknown", reason: "aborted" });
    expect(client.view()).toEqual({ locked: true, reason: "forgotten" });
    expect(timers.live()).toEqual([]);
    sent = [];
    expect((await client.datasets()).kind).toBe("locked");
    expect(sent).toEqual([]);
  });

  it.each([
    ["a forget", (made: Rig) => { made.client.forget(); }],
    ["a pagehide", (made: Rig) => { made.window.fire("pagehide"); }],
    ["a pageshow from the cache", (made: Rig) => { made.window.fire("pageshow", { persisted: true }); }],
    ["a second unlock", (made: Rig) => { void made.client.unlock(OTHER_TOKEN, NAME); }],
  ])("%s during a cancellable unlock aborts its request, as it aborts every request in flight", async (_case, event) => {
    handler = hanging;
    const made = rig();
    const cancel = new AbortController();
    const pending = made.client.unlock(TOKEN, NAME, cancel.signal);
    await vi.waitFor(() => {
      expect(sent.length).toBe(1);
    });
    expect(sent[0]?.signal?.aborted).toBe(false);
    event(made);
    expect(sent[0]?.signal?.aborted).toBe(true);
    expect(cancel.signal.aborted).toBe(false);
    expect(await pending).toEqual({ kind: "unknown", reason: "aborted" });
    expect(made.client.holds("d")).toBe(false);
  });

  it("a cancel after the unlock is done changes nothing", async () => {
    const { client } = rig();
    const cancel = new AbortController();
    expect(await client.unlock(TOKEN, NAME, cancel.signal)).toEqual({ kind: "unlocked" });
    cancel.abort();
    expect(client.view()).toEqual({ locked: false, reason: null });
    expect((await client.datasets()).kind).toBe("refused");
  });
});

describe("the idle check runs before the CSRF re-fetch and before the resend (m11)", () => {
  it("a first answer that comes after the limit, with no timer run, locks: no re-fetch, no resend", async () => {
    const { client, clocks } = await unlocked();
    routesTo["GET /operator/datasets"] = () => {
      clocks.advance(IDLE_LIMIT_MS);
      return json(403, refusal("CSRF_REQUIRED"));
    };
    sent = [];
    expect(await client.datasets()).toEqual({ kind: "locked", reason: "idle" });
    expect(sent.map((request) => request.url)).toEqual(["/operator/datasets"]);
    expect(client.view()).toEqual({ locked: true, reason: "idle" });
  });

  it("a CSRF answer that comes after the limit, with no timer run, locks: no resend", async () => {
    const { client, clocks } = await unlocked();
    routesTo["GET /operator/datasets"] = () => json(403, refusal("CSRF_REQUIRED"));
    routesTo["GET /operator/csrf"] = () => {
      clocks.advance(IDLE_LIMIT_MS);
      return json(200, { csrf: CSRF_2 });
    };
    sent = [];
    expect(await client.datasets()).toEqual({ kind: "locked", reason: "idle" });
    expect(sent.map((request) => request.url)).toEqual(["/operator/datasets", "/operator/csrf"]);
    expect(client.view()).toEqual({ locked: true, reason: "idle" });
  });

  it("and sends both when the limit is one millisecond away", async () => {
    const { client, clocks } = await unlocked();
    let calls = 0;
    routesTo["GET /operator/datasets"] = () => {
      calls += 1;
      clocks.advance(calls === 1 ? IDLE_LIMIT_MS - 2 : 0);
      return calls === 1 ? json(403, refusal("CSRF_REQUIRED")) : json(200, {});
    };
    routesTo["GET /operator/csrf"] = () => json(200, { csrf: CSRF_2 });
    sent = [];
    expect((await client.datasets()).kind).toBe("answer");
    expect(sent.map((request) => request.url)).toEqual(["/operator/datasets", "/operator/csrf", "/operator/datasets"]);
  });
});

describe("an unlock a subscriber locked does not report unlocked (m12a)", () => {
  it("returns locked, and holds nothing, when a listener forgets during the unlock's own change of view", async () => {
    const { client, timers } = rig();
    client.subscribe(() => {
      if (!client.view().locked) {
        client.forget();
      }
    });
    expect(await client.unlock(TOKEN, NAME)).toEqual({ kind: "locked", reason: "forgotten" });
    expect(client.view()).toEqual({ locked: true, reason: "forgotten" });
    expect(timers.live()).toEqual([]);
    sent = [];
    expect((await client.datasets()).kind).toBe("locked");
    expect(sent).toEqual([]);
  });
});

describe("the idle constants are pinned (m12c)", () => {
  it("are ten minutes, fifteen seconds, 200 code points and the four trusted inputs", () => {
    expect(IDLE_LIMIT_MS).toBe(600_000);
    expect(IDLE_CHECK_MS).toBe(15_000);
    expect(NAME_LIMIT).toBe(200);
    expect([...ACTIVITY_EVENTS]).toEqual(["keydown", "pointerdown", "wheel", "touchstart"]);
  });
});
