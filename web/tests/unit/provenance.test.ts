/**
 * The request half by provenance (D425): the four flows of the operator's client that send an id
 * back (`takeOver`, `rejectProposal`, `acceptProposal`, `queueAt`), against the server's own answers
 * (`tests/fixtures/operator-answers.json`, which a core test holds to the server). Whole domains:
 * every node of every answer, read every way the client reads one, at each of the four flows, is
 * either sent from exactly its feed or refused, sending nothing, with the reason the oracle (the
 * test's own walk of the raw JSON) gives; every copy of a node is refused as no node; every spelling
 * of an id the grammar does not take is refused, through the decoder and on the grammar alone.
 * Then the transport: credentials, CSRF once, unknown outcomes never sent again, a late answer
 * after a lock, the idle lock, and the handle that never reaches a caller.
 */
import { readFileSync } from "node:fs";
import path from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { box, isServerNumber, OPAQUE } from "../../src/api/box";
import { exchange } from "../../src/api/client";
import { createCurator, type Curator, IDLE_LIMIT_MS, type Outcome } from "../../src/api/curator";
import { decode } from "../../src/api/decode";
import * as routes from "../../src/api/generated/routes";
import { isRequestIdText, requestId } from "../../src/api/ids";
import type { JsonOut } from "../../src/api/json";
import { type Origin, originOf, stamp, UNSTAMPABLE } from "../../src/api/origin";
import { FEEDS, idAt, nodesAt, type Refusal, recorder } from "../../src/api/provenance";

const TOKEN = `aibi_${"T0k-_n".repeat(7)}Q`;
const CSRF = `${"CsRf-_".repeat(7)}Z`;
const CSRF_2 = `${"cSrF_-".repeat(7)}Y`;
const HANDLE = `ses_${"HaNdLe".repeat(7)}X`;
const HANDLE_OPEN = `ses_${"oPeNeD".repeat(7)}W`;
const DRAFT_2 = `sha256:${"b".repeat(64)}`;
const DRAFT_3 = `sha256:${"d".repeat(64)}`;
const NAME = "Ada";

// ---------------------------------------------------------------------------------------------
// The server's answers.

interface Fixture {
  readonly operation: string;
  readonly method: string;
  readonly path: string;
  readonly status: number;
  readonly body: string;
}

const ANSWERS = (JSON.parse(readFileSync(path.join(import.meta.dirname, "../fixtures/operator-answers.json"), "utf8")) as { answers: Fixture[] }).answers;

function fixture(method: string, at: string): Fixture {
  const found = ANSWERS.find((answer) => answer.method === method && answer.path === at);
  if (found === undefined) {
    throw new Error(`no fixture ${method} ${at}`);
  }
  return found;
}

const STATE = fixture("GET", "/operator/datasets/d");
const QUEUE = fixture("GET", "/operator/datasets/d/queue");
const QUEUE_2 = fixture("GET", "/operator/datasets/d/queue?release=2");
const WITHDRAWN = fixture("GET", "/operator/datasets/d/queue?release=1");
const DATASETS = fixture("GET", "/operator/datasets");
const TOOL = fixture("POST", "/api/tools/curation_queue");

type Raw = null | boolean | number | string | Raw[] | { [member: string]: Raw };

const RAW_STATE = JSON.parse(STATE.body) as { session: { session: number; base: string; draft: string }; labels: { label: number }[] };
const RAW_QUEUE = JSON.parse(QUEUE.body) as { proposals: { id: number }[] };
const SESSION_DRAFT = RAW_STATE.session.draft;

// ---------------------------------------------------------------------------------------------
// The fake server.

interface Sent {
  readonly url: string;
  readonly method: string;
  readonly headers: Record<string, string>;
  readonly body: string | null;
}

type Reply = Response | Promise<Response>;

let sent: Sent[] = [];
let overrides: Record<string, (request: Sent) => Reply> = {};

function text(status: number, body: string): Response {
  return new Response(body, { status, headers: { "Content-Type": "application/json" } });
}

function json(status: number, body: unknown): Response {
  return text(status, JSON.stringify(body));
}

function refusal(code: string): unknown {
  return { refusals: [{ code, path: null, message: [{ text: "words" }], alternatives: [] }] };
}

/** The default server: the fixtures at their paths (any dataset's state and queue are `d`'s), and
 * an answer of each write the four flows make. */
function server(request: Sent): Reply {
  const key = `${request.method} ${request.url}`;
  const found = overrides[key];
  if (found !== undefined) {
    return found(request);
  }
  if (key === "GET /operator/csrf") {
    return json(200, { csrf: CSRF });
  }
  for (const answer of ANSWERS) {
    if (answer.method === request.method && answer.path === request.url) {
      return text(answer.status, answer.body);
    }
  }
  let match = /^GET \/operator\/datasets\/([a-z]+)$/u.exec(key);
  if (match !== null) {
    return text(200, STATE.body);
  }
  match = /^GET \/operator\/datasets\/([a-z]+)\/queue(?:\?release=[0-9]+)?$/u.exec(key);
  if (match !== null) {
    return text(200, QUEUE.body);
  }
  match = /^POST \/operator\/datasets\/([a-z]+)\/session\/take-over$/u.exec(key);
  if (match?.[1] !== undefined) {
    return json(200, { dataset: match[1], session: 2, base: RAW_STATE.session.base, draft: SESSION_DRAFT, handle: HANDLE });
  }
  match = /^POST \/operator\/datasets\/([a-z]+)\/session\/open$/u.exec(key);
  if (match?.[1] !== undefined) {
    return json(200, { dataset: match[1], session: 2, base: RAW_STATE.session.base, draft: SESSION_DRAFT, handle: HANDLE_OPEN });
  }
  match = /^POST \/operator\/datasets\/([a-z]+)\/session\/change$/u.exec(key);
  if (match?.[1] !== undefined) {
    return json(200, { dataset: match[1], draft: DRAFT_2 });
  }
  match = /^POST \/operator\/datasets\/([a-z]+)\/proposals\/([0-9]+)\/reject$/u.exec(key);
  if (match?.[1] !== undefined && match[2] !== undefined) {
    return text(200, `{"dataset":"${match[1]}","proposal":${match[2]}}`);
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
  overrides = {};
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit): Promise<Response> => {
    const request: Sent = { url, method: init?.method ?? "GET", headers: headersOf(init), body: typeof init?.body === "string" ? init.body : null };
    sent.push(request);
    return server(request);
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

// ---------------------------------------------------------------------------------------------
// Clients.

interface Rig {
  readonly client: Curator;
  readonly clock: { wall: number; monotonic: number };
}

function rig(): Rig {
  const clock = { wall: 1_800_000_000_000, monotonic: 5_000 };
  const client = createCurator({ wall: () => clock.wall, monotonic: () => clock.monotonic }, { set: () => 0, clear: () => undefined });
  return { client, clock };
}

async function unlocked(): Promise<Rig> {
  const made = rig();
  expect(await made.client.unlock(TOKEN, NAME)).toEqual({ kind: "unlocked" });
  return made;
}

/** The body of an outcome that must be an answer. */
function body(outcome: Outcome): JsonOut {
  if (outcome.kind !== "answer" && outcome.kind !== "refused") {
    throw new Error(`no body: ${outcome.kind}`);
  }
  return outcome.body;
}

/** A member of a decoded record, or an element of a list, by a path of keys. */
function pick(value: JsonOut, ...keys: (string | number)[]): JsonOut {
  let current: JsonOut = value;
  for (const key of keys) {
    if (typeof key === "number") {
      current = Array.isArray(current) ? ((current as readonly JsonOut[])[key] ?? null) : null;
    } else {
      current = typeof current === "object" && current !== null && !Array.isArray(current) && !isServerNumber(current) && Object.hasOwn(current, key) ? ((current as Record<string, JsonOut>)[key] ?? null) : null;
    }
  }
  return current;
}

const INVALID = (what: string): Outcome => ({ kind: "invalid", what }) as Outcome;

type Method = "takeOver" | "rejectProposal" | "acceptProposal" | "queueAt";
const METHODS: readonly Method[] = ["takeOver", "rejectProposal", "acceptProposal", "queueAt"];

/** Each flow's feed, as the plan names it (`classes.json`'s `from`, which a gate holds equal to
 * `FEEDS`): the oracle the grid compares with. */
const FEED_OF: Readonly<Record<Method, { readonly operation: string; readonly path: string; readonly read: string }>> = {
  takeOver: { operation: "dataset_state", path: "session", read: "session" },
  rejectProposal: { operation: "queue", path: "proposals[]", read: "id" },
  acceptProposal: { operation: "queue", path: "proposals[]", read: "id" },
  queueAt: { operation: "dataset_state", path: "labels[]", read: "label" },
};

/** Every path a flow's feed records for an operation, by the oracle. */
const RECORDED: Readonly<Record<string, readonly string[]>> = { dataset_state: ["session", "labels[]"], queue: ["proposals[]"] };

/** Every object a decoded value holds (records, lists and boxes, the root included), each with its
 * path (`a.b[]`) and the raw JSON at the same place (the oracle's own parse). */
function walk(value: JsonOut, raw: Raw, at = ""): { readonly path: string; readonly node: JsonOut; readonly raw: Raw }[] {
  const found: { path: string; node: JsonOut; raw: Raw }[] = [];
  if (typeof value !== "object" || value === null) {
    return found;
  }
  found.push({ path: at, node: value, raw });
  if (isServerNumber(value)) {
    return found;
  }
  if (Array.isArray(value)) {
    const items = value as readonly JsonOut[];
    items.forEach((item, index) => {
      found.push(...walk(item, (raw as Raw[])[index] ?? null, `${at}[]`));
    });
    return found;
  }
  for (const [key, member] of Object.entries(value as Record<string, JsonOut>)) {
    found.push(...walk(member, (raw as Record<string, Raw>)[key] ?? null, at === "" ? key : `${at}.${key}`));
  }
  return found;
}

/** What a flow must send for a node it takes: its method, URL and body, from the raw JSON. */
function expected(method: Method, raw: Raw, dataset: string): { readonly method: string; readonly url: string; readonly body: (found: string | null) => boolean } {
  const record = raw as Record<string, Raw>;
  const scalar = (key: string): string => {
    const found = record[key];
    return typeof found === "number" || typeof found === "string" ? String(found) : "?";
  };
  switch (method) {
    case "takeOver":
      return {
        method: "POST",
        url: `/operator/datasets/${dataset}/session/take-over`,
        body: (found) => found === `{"session":${scalar("session")},"expected":"${scalar("draft")}"}`,
      };
    case "rejectProposal":
      return { method: "POST", url: `/operator/datasets/${dataset}/proposals/${scalar("id")}/reject`, body: (found) => found === "{}" };
    case "acceptProposal":
      return {
        method: "POST",
        url: `/operator/datasets/${dataset}/session/change`,
        body: (found) => {
          const parsed = JSON.parse(found ?? "null") as { handle: string; edits: unknown };
          return [HANDLE, HANDLE_OPEN].includes(parsed.handle) && JSON.stringify(parsed.edits) === `[{"op":"accept","proposal":${scalar("id")}}]` && found?.endsWith(`"edits":[{"op":"accept","proposal":${scalar("id")}}]}`) === true;
        },
      };
    case "queueAt":
      return { method: "GET", url: `/operator/datasets/${dataset}/queue?release=${scalar("label")}`, body: (found) => found === null };
  }
}

// ---------------------------------------------------------------------------------------------
// The grid: every node of every answer, every way it is read, at each flow.

/** The ways an answer reaches the test: the reader, the fixture it serves, the operation the
 * client reads it as, whether `send` read it (else `exchange`), and its status. */
interface Reading {
  readonly name: string;
  readonly raw: string;
  readonly operation: string;
  readonly bySend: boolean;
  readonly status: number;
  /** How many (node, flow) pairs the grid sends, by hand: the oracle's count of feeds met. */
  readonly accepts: number;
  readonly read: (client: Curator) => Promise<JsonOut>;
}

const READINGS: readonly Reading[] = [
  { name: "the dataset state, by dataset()", accepts: 3, raw: STATE.body, operation: "dataset_state", bySend: true, status: 200, read: async (client) => body(await client.dataset("d")) },
  { name: "the queue, by queue()", accepts: 4, raw: QUEUE.body, operation: "queue", bySend: true, status: 200, read: async (client) => body(await client.queue("d")) },
  {
    name: "the queue as of label 2, by queueAt()", accepts: 4,
    raw: QUEUE_2.body,
    operation: "queue",
    bySend: true,
    status: 200,
    read: async (client) => body(await client.queueAt(pick(body(await client.dataset("d")), "labels", 1))),
  },
  {
    name: "the withdrawn label's 409, by queueAt()", accepts: 0,
    raw: WITHDRAWN.body,
    operation: "queue",
    bySend: true,
    status: 409,
    read: async (client) => body(await client.queueAt(pick(body(await client.dataset("d")), "labels", 0))),
  },
  { name: "the datasets list, by datasets()", accepts: 0, raw: DATASETS.body, operation: "datasets", bySend: true, status: 200, read: async (client) => body(await client.datasets()) },
  {
    name: "the public tool's queue, by exchange", accepts: 0,
    raw: TOOL.body,
    operation: "curation_queue",
    bySend: false,
    status: 200,
    read: async () => (await exchange(routes.curation_queue(), { dataset: "d" })).body,
  },
  {
    name: "the dataset state, by exchange (no origin for an answer exchange read)", accepts: 0,
    raw: STATE.body,
    operation: "dataset_state",
    bySend: false,
    status: 200,
    read: async () => (await exchange(routes.dataset_state({ dataset: "d" }))).body,
  },
  {
    name: "the dataset state's text answered with a 409", accepts: 0,
    raw: STATE.body,
    operation: "dataset_state",
    bySend: true,
    status: 409,
    read: async (client) => {
      overrides["GET /operator/datasets/d"] = () => text(409, STATE.body);
      return body(await client.dataset("d"));
    },
  },
  {
    name: "the queue's text answered with a 404", accepts: 0,
    raw: QUEUE.body,
    operation: "queue",
    bySend: true,
    status: 404,
    read: async (client) => {
      overrides["GET /operator/datasets/d/queue"] = () => text(404, QUEUE.body);
      return body(await client.queue("d"));
    },
  },
  {
    name: "the dataset state's text at the queue's route (another operation)", accepts: 0,
    raw: STATE.body,
    operation: "queue",
    bySend: true,
    status: 200,
    read: async (client) => {
      overrides["GET /operator/datasets/d/queue"] = () => text(200, STATE.body);
      return body(await client.queue("d"));
    },
  },
  {
    name: "the queue's text at the dataset state's route (another operation)", accepts: 0,
    raw: QUEUE.body,
    operation: "dataset_state",
    bySend: true,
    status: 200,
    read: async (client) => {
      overrides["GET /operator/datasets/d"] = () => text(200, QUEUE.body);
      return body(await client.dataset("d"));
    },
  },
];

describe("every node of every answer, at each flow: sent from its feed alone (D425, m8)", () => {
  it.each(READINGS.map((reading) => [reading.name, reading] as const))("%s", async (_name, reading) => {
    const { client } = await unlocked();
    expect((await client.open("d")).kind).toBe("answer");
    const root = await reading.read(client);
    overrides = {};
    const nodes = walk(root, JSON.parse(reading.raw) as Raw);
    expect(nodes.length).toBeGreaterThan(3);
    const wrong: string[] = [];
    let accepted = 0;
    for (const { path: at, node, raw } of nodes) {
      const recorded = reading.bySend && reading.status >= 200 && reading.status <= 299 && (RECORDED[reading.operation] ?? []).includes(at);
      expect([at, originOf(node) !== null], "placement").toEqual([at, recorded]);
      for (const method of METHODS) {
        const feed = FEED_OF[method];
        const takes = recorded && feed.operation === reading.operation && feed.path === at;
        const before = sent.length;
        const outcome = await client[method](node);
        const fresh = sent.slice(before);
        if (takes) {
          accepted += 1;
          const want = expected(method, raw, "d");
          // Sent: the server's answer is the outcome (the withdrawn label's queue is its 409).
          if ((outcome.kind !== "answer" && outcome.kind !== "refused") || fresh.length !== 1 || fresh[0]?.method !== want.method || fresh[0].url !== want.url || !want.body(fresh[0].body)) {
            wrong.push(`${method}(${at}): ${JSON.stringify([outcome.kind, fresh.map((one) => [one.method, one.url, one.body])])}`);
          }
        } else {
          const why = recorded ? "origin" : "node";
          if (JSON.stringify(outcome) !== JSON.stringify(INVALID(why)) || fresh.length !== 0) {
            wrong.push(`${method}(${at}): ${JSON.stringify(outcome)}, ${String(fresh.length)} sent`);
          }
        }
      }
    }
    expect(wrong).toEqual([]);
    expect(accepted).toBe(reading.accepts);
  });

  it("the grid's oracle names the feeds FEEDS records, and the flows' feeds are FEEDS' (the oracle is no copy of a mutant)", () => {
    expect(FEED_OF).toEqual({ takeOver: FEEDS["TakeOver.session"], rejectProposal: FEEDS["path:proposal"], acceptProposal: FEEDS["Accept.proposal"], queueAt: FEEDS["query:release"] });
  });
});

// ---------------------------------------------------------------------------------------------
// The sanctioned flows, exactly.

describe("the four flows send what the plan says, and nothing of the caller's", () => {
  it("takeOver sends the session's id and its draft (not its base), stores the handle, and gives the answer without it (m12, MB1)", async () => {
    const { client } = await unlocked();
    expect(RAW_STATE.session.base).not.toBe(RAW_STATE.session.draft);
    const state = body(await client.dataset("d"));
    const before = sent.length;
    const outcome = await client.takeOver(pick(state, "session"));
    expect(sent.slice(before).map((one) => [one.method, one.url, one.body])).toEqual([
      ["POST", "/operator/datasets/d/session/take-over", `{"session":${String(RAW_STATE.session.session)},"expected":"${SESSION_DRAFT}"}`],
    ]);
    expect(sent.at(-1)?.headers).toMatchObject({ authorization: `Bearer ${TOKEN}`, "aibi-operator": NAME, "aibi-csrf": CSRF, "content-type": "application/json" });
    expect(outcome.kind).toBe("answer");
    expect(Object.keys(body(outcome) as object)).toEqual(["dataset", "session", "base", "draft"]);
    expect(JSON.stringify(Object.entries(body(outcome) as object).filter(([key]) => key !== "session"))).not.toContain("ses_");
    expect(client.holds("d")).toBe(true);
    // The handle and the draft are the take-over's: a change sends them.
    expect((await client.change("d", [{ op: "confirm", descriptor: "t" }])).kind).toBe("answer");
    expect(sent.at(-1)?.body).toBe(`{"handle":"${HANDLE}","expected":"${SESSION_DRAFT}","edits":[{"op":"confirm","descriptor":"t"}]}`);
  });

  it("takeOver's dataset is the route's the node was read from, never the answer's member or an argument (m2)", async () => {
    const { client } = await unlocked();
    const state = body(await client.dataset("e"));
    expect(pick(state, "dataset")).toBe("d");
    await client.takeOver(pick(state, "session"));
    expect(sent.at(-1)?.url).toBe("/operator/datasets/e/session/take-over");
    expect([client.holds("e"), client.holds("d")]).toEqual([true, false]);
    const queue = body(await client.queue("e"));
    await client.rejectProposal(pick(queue, "proposals", 0));
    expect(sent.at(-1)?.url).toBe(`/operator/datasets/e/proposals/${String(RAW_QUEUE.proposals[0]?.id)}/reject`);
    expect((await client.acceptProposal(pick(queue, "proposals", 1))).kind).toBe("answer");
    expect(sent.at(-1)?.url).toBe("/operator/datasets/e/session/change");
    await client.queueAt(pick(body(await client.dataset("e")), "labels", 1));
    expect(sent.at(-1)?.url).toBe(`/operator/datasets/e/queue?release=${String(RAW_STATE.labels[1]?.label)}`);
  });

  it("a take-over's 409 is the outcome: never sent again, no handle held (m15)", async () => {
    const { client } = await unlocked();
    overrides["POST /operator/datasets/d/session/take-over"] = () => json(409, refusal("CONFLICT"));
    const outcome = await client.takeOver(pick(body(await client.dataset("d")), "session"));
    expect(outcome).toMatchObject({ kind: "refused", status: 409, codes: ["CONFLICT"] });
    expect(sent.filter((one) => one.url.endsWith("/take-over")).length).toBe(1);
    expect(client.holds("d")).toBe(false);
  });

  it.each([
    ["no handle", { dataset: "d", session: 2, base: RAW_STATE.session.base, draft: SESSION_DRAFT }],
    ["a handle not in its form", { dataset: "d", session: 2, base: RAW_STATE.session.base, draft: SESSION_DRAFT, handle: "ses_x" }],
    ["no draft", { dataset: "d", session: 2, base: RAW_STATE.session.base, handle: HANDLE }],
  ])("a take-over's 200 with %s is an unknown outcome, and nothing is held", async (_case, answer) => {
    const { client } = await unlocked();
    overrides["POST /operator/datasets/d/session/take-over"] = () => json(200, answer);
    expect(await client.takeOver(pick(body(await client.dataset("d")), "session"))).toEqual({ kind: "unknown", reason: "undecodable" });
    expect(client.holds("d")).toBe(false);
  });

  it("acceptProposal sends the closure's handle and expected, and its next expected is the answer's draft (MB1)", async () => {
    const { client } = await unlocked();
    expect((await client.open("d")).kind).toBe("answer");
    const queue = body(await client.queue("d"));
    const [first, second] = RAW_QUEUE.proposals;
    await client.acceptProposal(pick(queue, "proposals", 0));
    expect(sent.at(-1)?.body).toBe(`{"handle":"${HANDLE_OPEN}","expected":"${SESSION_DRAFT}","edits":[{"op":"accept","proposal":${String(first?.id)}}]}`);
    overrides["POST /operator/datasets/d/session/change"] = () => json(200, { dataset: "d", draft: DRAFT_3 });
    await client.acceptProposal(pick(queue, "proposals", 1));
    expect(sent.at(-1)?.body).toBe(`{"handle":"${HANDLE_OPEN}","expected":"${DRAFT_2}","edits":[{"op":"accept","proposal":${String(second?.id)}}]}`);
    await client.change("d", [{ op: "remove_descriptor", descriptor: "x" }]);
    expect(sent.at(-1)?.body).toContain(`"expected":"${DRAFT_3}"`);
  });

  it("acceptProposal without a session of the node's dataset sends nothing", async () => {
    const { client } = await unlocked();
    const queue = body(await client.queue("d"));
    const before = sent.length;
    expect(await client.acceptProposal(pick(queue, "proposals", 0))).toEqual(INVALID("session"));
    expect(sent.length).toBe(before);
  });

  it("acceptProposal binds the closure's session to the node's dataset: a session held on another dataset is no session (m3)", async () => {
    const { client } = await unlocked();
    expect((await client.open("x")).kind).toBe("answer");
    expect([client.holds("x"), client.holds("d")]).toEqual([true, false]);
    const queue = body(await client.queue("d"));
    const before = sent.length;
    expect(await client.acceptProposal(pick(queue, "proposals", 0))).toEqual(INVALID("session"));
    expect(sent.length).toBe(before);
    expect(sent.filter((one) => one.url.includes("/session/change")).length).toBe(0);
    expect([client.holds("x"), client.holds("d")]).toEqual([true, false]);
  });

  it("a take-over while another dataset's session is held keeps that session, and holds its own (m3)", async () => {
    const { client } = await unlocked();
    await client.open("x");
    const state = body(await client.dataset("d"));
    expect((await client.takeOver(pick(state, "session"))).kind).toBe("answer");
    expect([client.holds("x"), client.holds("d")]).toEqual([true, true]);
    const queue = body(await client.queue("x"));
    expect((await client.acceptProposal(pick(queue, "proposals", 0))).kind).toBe("answer");
    expect(sent.at(-1)?.url).toBe("/operator/datasets/x/session/change");
  });

  it.each(["CONFLICT", "NO_SESSION"])("acceptProposal's %s drops the handle", async (code) => {
    const { client } = await unlocked();
    await client.open("d");
    overrides["POST /operator/datasets/d/session/change"] = () => json(409, refusal(code));
    expect(await client.acceptProposal(pick(body(await client.queue("d")), "proposals", 0))).toMatchObject({ kind: "refused", codes: [code] });
    expect(client.holds("d")).toBe(false);
  });

  it("a proposal from queueAt's answer is accepted and rejected like one from queue's (n6)", async () => {
    const { client } = await unlocked();
    await client.open("d");
    const at = body(await client.queueAt(pick(body(await client.dataset("d")), "labels", 1)));
    expect(sent.at(-1)?.url).toBe("/operator/datasets/d/queue?release=2");
    expect((await client.acceptProposal(pick(at, "proposals", 0))).kind).toBe("answer");
    expect((await client.rejectProposal(pick(at, "proposals", 1))).kind).toBe("answer");
    expect(sent.at(-1)?.url).toBe(`/operator/datasets/d/proposals/${String(RAW_QUEUE.proposals[1]?.id)}/reject`);
  });

  it("change still refuses an accept edit: the id goes back through acceptProposal alone", async () => {
    const { client } = await unlocked();
    await client.open("d");
    const before = sent.length;
    expect(await client.change("d", [{ op: "accept", proposal: 2 } as never])).toEqual(INVALID("edits"));
    expect(sent.length).toBe(before);
  });
});

// ---------------------------------------------------------------------------------------------
// Copies of a node: no node.

describe("a copy of a node is no node: refused as such, sending nothing (m8, n6)", () => {
  /** Each flow's own node, fresh. */
  async function own(client: Curator, method: Method): Promise<JsonOut> {
    switch (method) {
      case "takeOver":
        return pick(body(await client.dataset("d")), "session");
      case "queueAt":
        return pick(body(await client.dataset("d")), "labels", 1);
      default:
        return pick(body(await client.queue("d")), "proposals", 0);
    }
  }

  class Sub extends Object {}

  const COPIES: readonly (readonly [string, (node: JsonOut) => unknown])[] = [
    ["a spread", (node) => ({ ...(node as object) })],
    ["Object.assign", (node) => Object.assign({}, node)],
    ["Object.fromEntries of its entries", (node) => Object.fromEntries(Object.entries(node as object))],
    ["Object.create of it", (node) => Object.create(node as object) as unknown],
    ["a Proxy around it", (node) => new Proxy(node as object, {})],
    ["structuredClone", (node) => structuredClone(node)],
    ["a subclass's instance with its members", (node) => Object.assign(new Sub(), node)],
    ["Reflect.construct with its members", (node) => Object.assign(Reflect.construct(Object, []) as object, node)],
    ["its members copied one by one, boxes and all", (node) => Object.fromEntries(Object.keys(node as object).map((key) => [key, pick(node, key)]))],
    ["a list holding it", (node) => [node]],
    ["a box", () => box("2")],
    ["null", () => null],
    ["a string of digits", () => "2"],
    ["a number", () => 2],
  ];

  it.each(METHODS.flatMap((method) => COPIES.map(([name, copy]) => [method, name, copy] as const)))("%s refuses %s", async (method, _name, copy) => {
    const { client } = await unlocked();
    await client.open("d");
    const node = await own(client, method);
    expect(originOf(node)).not.toBe(null);
    const before = sent.length;
    expect(await client[method](copy(node) as JsonOut)).toEqual(INVALID("node"));
    expect(sent.length).toBe(before);
    // The control: the node itself is taken.
    expect((await client[method](node)).kind).toBe("answer");
  });

  it("a node cannot be thawed, re-prototyped or turned to JSON (the engine refuses, before any flow)", async () => {
    const { client } = await unlocked();
    const node = pick(body(await client.dataset("d")), "session") as object;
    expect(Object.isFrozen(node)).toBe(true);
    expect(() => {
      Object.setPrototypeOf(node, {});
    }).toThrow(TypeError);
    expect(() => Object.defineProperty(node, "session", { value: 1 })).toThrow(TypeError);
    expect(() => JSON.stringify(node)).toThrow(OPAQUE);
  });
});

// ---------------------------------------------------------------------------------------------
// The origin: brand, operation, path, era, dataset, member, id.

describe("the origin binds the operation, the path, the era and the dataset (m2, m3)", () => {
  /** A fresh object stamped as the client would stamp it, with `origin` changed: the brand is
   * real, so only the check of each part can refuse it. */
  async function forged(change: Partial<Origin>, members: Record<string, JsonOut>): Promise<{ client: Curator; node: JsonOut }> {
    const { client } = await unlocked();
    await client.open("d");
    const real = originOf(pick(body(await client.dataset("d")), "session"));
    if (real === null) {
      throw new Error("no origin");
    }
    const node = { ...members };
    stamp(node, { ...real, ...change });
    Object.freeze(node);
    return { client, node };
  }

  const ID = { id: box("3"), session: box("2"), label: box("2"), draft: SESSION_DRAFT };

  it.each([
    ["takeOver", "a node of the same operation at another path that holds its member (op-only)", { path: "labels[]" }, "origin"],
    ["takeOver", "a node of another operation at the same path (path-only)", { operation: "datasets" }, "origin"],
    ["takeOver", "another operation, the same path", { operation: "queue", path: "session" }, "origin"],
    ["queueAt", "a nested look-alike, the path as a suffix", { path: "datasets[].labels[]" }, "origin"],
    ["rejectProposal", "the public tool's operation", { operation: "curation_queue", path: "proposals[]" }, "origin"],
    ["rejectProposal", "a nested look-alike of proposals[]", { path: "queue.proposals[]" }, "origin"],
    ["acceptProposal", "a proposal path under the dataset state", { operation: "dataset_state" }, "origin"],
    ["rejectProposal", "another era", { era: {} }, "stale"],
    ["takeOver", "another era, frozen", { era: Object.freeze({}) }, "stale"],
  ] as const)("%s refuses %s", async (method, _name, change, why) => {
    const base: Partial<Origin> = { operation: FEED_OF[method].operation as Origin["operation"], path: FEED_OF[method].path };
    const { client, node } = await forged({ ...base, ...change }, ID);
    const before = sent.length;
    expect(await client[method](node)).toEqual(INVALID(why));
    expect(sent.length).toBe(before);
  });

  it.each(METHODS)("%s takes such a node at its own feed, and sends under the origin's dataset (the forge's control)", async (method) => {
    const { client, node } = await forged({ operation: FEED_OF[method].operation as Origin["operation"], path: FEED_OF[method].path, dataset: "x" }, ID);
    if (method === "acceptProposal") {
      await client.open("x");
    }
    expect((await client[method](node)).kind).toBe("answer");
    expect(sent.at(-1)?.url).toContain("/operator/datasets/x/");
  });

  it.each([
    ["takeOver", "a session that is a string", { session: "2", draft: SESSION_DRAFT }, "member"],
    ["takeOver", "no session", { draft: SESSION_DRAFT }, "member"],
    ["takeOver", "no draft", { session: box("2") }, "member"],
    ["takeOver", "a draft not in its form", { session: box("2"), draft: "sha256:x" }, "member"],
    ["takeOver", "a base and no draft (expected is never the base)", { session: box("2"), base: SESSION_DRAFT }, "member"],
    ["rejectProposal", "an id that is a string", { id: "3" }, "member"],
    ["rejectProposal", "the id under another member", { proposal: box("3") }, "member"],
    ["rejectProposal", "a null id", { id: null }, "member"],
    ["queueAt", "a label in a list", { label: [box("2")] }, "member"],
    ["acceptProposal", "an id that is no request id", { id: box("03") }, "id"],
  ] as const)("%s refuses %s", async (method, _name, members, why) => {
    const { client, node } = await forged({ operation: FEED_OF[method].operation as Origin["operation"], path: FEED_OF[method].path }, members);
    const before = sent.length;
    expect(await client[method](node)).toEqual(INVALID(why));
    expect(sent.length).toBe(before);
  });

  /** `Object.prototype` given a member for the length of `run` (deliberate pollution, which D419
   * does not defend against: what is read is read from the node's own members alone). */
  async function polluted(name: string, value: unknown, run: () => Promise<void> | void): Promise<void> {
    Object.defineProperty(Object.prototype, name, { value, configurable: true });
    try {
      await run();
    } finally {
      Reflect.deleteProperty(Object.prototype, name);
    }
  }

  it.each([
    ["takeOver", "session", { draft: SESSION_DRAFT }, box("2")],
    ["takeOver", "draft", { session: box("2") }, SESSION_DRAFT],
    ["rejectProposal", "id", { kind: "x" }, box("3")],
    ["acceptProposal", "id", { kind: "x" }, box("3")],
    ["queueAt", "label", { kind: "x" }, box("2")],
  ] as const)("%s reads an own member alone: a node with no own %s, which Object.prototype carries, is refused (Object.prototype polluted)", async (method, name, members, value) => {
    const { client, node } = await forged({ operation: FEED_OF[method].operation as Origin["operation"], path: FEED_OF[method].path }, members);
    const before = sent.length;
    await polluted(name, value, async () => {
      expect(await client[method](node)).toEqual(INVALID("member"));
    });
    expect(sent.length).toBe(before);
  });

  /** The distinct steps of every feed's path, each as `[path, step name, each?]`. */
  const STEPS = [...new Set(Object.values(FEEDS).map((feed) => feed.path))].flatMap((feedPath) =>
    feedPath.split(".").map((step) => [feedPath, step.replace(/\[\]$/u, ""), step.endsWith("[]")] as const),
  );

  it.each(STEPS)("nodesAt(%s) reads an own member alone: a %s on the prototype chain is not at the path (Object.prototype polluted)", async (feedPath, name, each) => {
    expect(STEPS.length).toBeGreaterThanOrEqual(3);
    const root: JsonOut = Object.freeze({});
    await polluted(name, each ? [{ kind: "x" }] : { kind: "x" }, () => {
      expect(nodesAt(root, feedPath)).toEqual([]);
    });
    expect(nodesAt(root, feedPath)).toEqual([]);
  });

  it("an answer decoded while Object.prototype carries a feed's step is stamped at its own nodes alone", async () => {
    const { client } = await unlocked();
    await polluted("labels", [{ label: box("9") }], async () => {
      const state = body(await client.dataset("d"));
      const originals = nodesAt(state, "labels[]");
      expect(originals.length).toBe(RAW_STATE.labels.length);
      expect(originals.every((node) => originOf(node)?.path === "labels[]")).toBe(true);
    });
  });

  it.each([
    ["a forget and a new unlock", async (client: Curator) => {
      client.forget();
      await client.unlock(TOKEN, NAME);
    }],
    ["a new unlock alone", async (client: Curator) => {
      await client.unlock(TOKEN, NAME);
    }],
    ["a 401 and a new unlock", async (client: Curator) => {
      overrides["GET /operator/datasets"] = () => json(401, refusal("TOKEN_REQUIRED"));
      expect(await client.datasets()).toEqual({ kind: "locked", reason: "token" });
      overrides = {};
      await client.unlock(TOKEN, NAME);
    }],
  ])("a node of an earlier epoch is stale after %s: nothing sent", async (_case, between) => {
    const { client } = await unlocked();
    const state = body(await client.dataset("d"));
    const queue = body(await client.queue("d"));
    await between(client);
    await client.open("d");
    const nodes: Record<Method, JsonOut> = { takeOver: pick(state, "session"), queueAt: pick(state, "labels", 1), rejectProposal: pick(queue, "proposals", 0), acceptProposal: pick(queue, "proposals", 0) };
    for (const method of METHODS) {
      const before = sent.length;
      expect(await client[method](nodes[method])).toEqual(INVALID("stale"));
      expect(sent.length).toBe(before);
    }
  });

  it("a node another client decoded is of another era", async () => {
    const one = await unlocked();
    const two = await unlocked();
    const node = pick(body(await one.client.dataset("d")), "session");
    const before = sent.length;
    expect(await two.client.takeOver(node)).toEqual(INVALID("stale"));
    expect(sent.length).toBe(before);
    expect((await one.client.takeOver(node)).kind).toBe("answer");
  });
});

// ---------------------------------------------------------------------------------------------
// The grammar.

const MAX = 9007199254740991n;

/** The oracle of the grammar: digits, no leading zero, from 1 to 2^53 - 1 (BigInt, the test's). */
function canonical(text: string): boolean {
  return /^[1-9][0-9]*$/u.test(text) && BigInt(text) <= MAX;
}

describe("the grammar of a request id (m11)", () => {
  it("is the oracle's on every string of up to four characters over the characters that matter", () => {
    const alphabet = ["0", "1", "9", " ", "+", "-", ".", "e", "E", "x", "\n", "１", "٣"];
    const wrong: string[] = [];
    let count = 0;
    const visit = (prefix: string, depth: number): void => {
      count += 1;
      if (isRequestIdText(prefix) !== canonical(prefix)) {
        wrong.push(JSON.stringify(prefix));
      }
      if (depth === 4) {
        return;
      }
      for (const character of alphabet) {
        visit(prefix + character, depth + 1);
      }
    };
    visit("", 0);
    expect(wrong).toEqual([]);
    expect(count).toBeGreaterThan(30_000);
  });

  it("is the oracle's for every whole number within 2000 of 2^53 - 1, and of each power of ten", () => {
    const wrong: string[] = [];
    const centres = [MAX, ...Array.from({ length: 18 }, (_, power) => 10n ** BigInt(power))];
    for (const centre of centres) {
      for (let step = -2000n; step <= 2000n; step += 1n) {
        const value = centre + step;
        const spelled = value.toString();
        if (isRequestIdText(spelled) !== canonical(spelled) || (canonical(spelled) && String(requestId(spelled)) !== spelled)) {
          wrong.push(spelled);
        }
      }
    }
    expect(wrong).toEqual([]);
  });

  it.each(["0", "00", "01", " 1", "1 ", "1\n", "+1", "-1", "-0", "１", "1.0", "1e2", "1e400", "9007199254740992", "9007199254740993", "9999999999999999", "10000000000000000", "", "0x1", "1_000", "٣"])("refuses %j", (spelled) => {
    expect([isRequestIdText(spelled), requestId(spelled)]).toEqual([false, null]);
  });

  it.each(["1", "2", "10", "9007199254740991"])("takes %j, as that number exactly", (spelled) => {
    expect(String(requestId(spelled))).toBe(spelled);
  });

  it.each([
    ["0", null],
    ["-0", null],
    ["1.0", null],
    ["1e0", null],
    ["1E2", null],
    ["2.5", null],
    ["-3", null],
    ["1e400", null],
    ["9007199254740992", null],
    ["9007199254740993", null],
    ["90071992547409910", null],
    ["1", "/operator/datasets/d/proposals/1/reject"],
    ["9007199254740991", "/operator/datasets/d/proposals/9007199254740991/reject"],
  ])("through the decoder, a proposal's id spelled %s: %s", async (spelled, url) => {
    const { client } = await unlocked();
    overrides["GET /operator/datasets/d/queue"] = () => text(200, QUEUE.body.replace(`{"id":${String(RAW_QUEUE.proposals[0]?.id)},`, `{"id":${spelled},`));
    const node = pick(body(await client.queue("d")), "proposals", 0);
    const before = sent.length;
    const outcome = await client.rejectProposal(node);
    if (url === null) {
      expect(outcome).toEqual(INVALID("id"));
      expect(sent.length).toBe(before);
    } else {
      expect(sent.slice(before).map((one) => one.url)).toEqual([url]);
    }
  });
});

// ---------------------------------------------------------------------------------------------
// Where the brand is, and the stamp's own rules.

describe("the brand is on the feeds' nodes alone (n6: placement)", () => {
  it("the dataset state's root, lists and boxes carry none; its session and labels do", async () => {
    const { client } = await unlocked();
    const state = body(await client.dataset("d"));
    expect(originOf(state)).toBe(null);
    expect(originOf(pick(state, "labels"))).toBe(null);
    expect(originOf(pick(state, "latest"))).toBe(null);
    expect(originOf(pick(state, "session", "session"))).toBe(null);
    const session = originOf(pick(state, "session"));
    expect(session === null ? null : { operation: session.operation, path: session.path, dataset: session.dataset, frozen: Object.isFrozen(session) }).toEqual({ operation: "dataset_state", path: "session", dataset: "d", frozen: true });
    expect(originOf(pick(state, "labels", 0))?.path).toBe("labels[]");
    const queue = body(await client.queue("d"));
    expect(originOf(queue)).toBe(null);
    for (const name of ["fields", "undeclared", "notes"]) {
      for (const node of nodesAt(queue, `${name}[]`)) {
        expect(originOf(node)).toBe(null);
      }
    }
    expect(nodesAt(queue, "proposals[]").every((node) => originOf(node)?.path === "proposals[]")).toBe(true);
  });

  it("stamp refuses a frozen node and a node stamped already, in fixed words (n3)", () => {
    const era = {};
    expect(() => {
      stamp(Object.freeze({}), { operation: "queue", path: "proposals[]", dataset: "d", era });
    }).toThrow(new TypeError(UNSTAMPABLE));
    const node = {};
    stamp(node, { operation: "queue", path: "proposals[]", dataset: "d", era });
    expect(() => {
      stamp(node, { operation: "queue", path: "proposals[]", dataset: "d", era });
    }).toThrow(new TypeError(UNSTAMPABLE));
    expect(Reflect.ownKeys(node)).toEqual([]);
    expect(originOf(node)).toEqual({ operation: "queue", path: "proposals[]", dataset: "d", era });
  });

  it("decode stamps before it freezes, and a stamp after it would throw (the order is an invariant, n3)", () => {
    const hook = recorder("dataset_state", { dataset: "d", era: {} });
    if (hook === null) {
      throw new Error("no hook");
    }
    const decoded = decode(STATE.body, hook);
    expect(Object.isFrozen(decoded)).toBe(true);
    expect(originOf(pick(decoded, "session"))).not.toBe(null);
    const plain = decode(STATE.body);
    expect(() => {
      hook(plain);
    }).toThrow(new TypeError(UNSTAMPABLE));
    expect(recorder("datasets", { dataset: "d", era: {} })).toBe(null);
    expect(recorder("curation_queue", { dataset: "d", era: {} })).toBe(null);
  });

  it("nodesAt is rooted: a node of the same name deeper down is not at the path", () => {
    const value = decode('{"proposals":[{"id":1,"value":{"proposals":[{"id":2}]}}],"queue":{"proposals":[{"id":3}]}}');
    expect(nodesAt(value, "proposals[]").map((node) => originOf(node))).toEqual([null]);
    expect(nodesAt(value, "proposals[]").length).toBe(1);
    expect(nodesAt(value, "queue.proposals[]").length).toBe(1);
    expect(nodesAt(value, "proposals[].value.proposals[]").length).toBe(1);
    expect(nodesAt(value, "session")).toEqual([]);
  });
});

// ---------------------------------------------------------------------------------------------
// The transport (MB1): credentials, CSRF once, unknown outcomes, the idle lock, a late answer.

/** Each flow run against the client's own fresh node. */
async function flow(client: Curator, method: Method): Promise<Outcome> {
  switch (method) {
    case "takeOver":
      return client.takeOver(pick(body(await client.dataset("d")), "session"));
    case "queueAt":
      return client.queueAt(pick(body(await client.dataset("d")), "labels", 1));
    default:
      return client[method](pick(body(await client.queue("d")), "proposals", 0));
  }
}

const KEY: Readonly<Record<Method, string>> = {
  takeOver: "POST /operator/datasets/d/session/take-over",
  rejectProposal: `POST /operator/datasets/d/proposals/${String(RAW_QUEUE.proposals[0]?.id)}/reject`,
  acceptProposal: "POST /operator/datasets/d/session/change",
  queueAt: "GET /operator/datasets/d/queue?release=2",
};

describe("the four flows go through the operator client's send (MB1)", () => {
  it.each(METHODS)("%s: CSRF_REQUIRED fetches the CSRF token again and sends once more, the same body; a second is the outcome", async (method) => {
    const { client } = await unlocked();
    await client.open("d");
    let calls = 0;
    overrides[KEY[method]] = (request) => {
      calls += 1;
      if (calls === 1) {
        return json(403, refusal("CSRF_REQUIRED"));
      }
      overrides = {};
      return server(request);
    };
    overrides["GET /operator/csrf"] = () => json(200, { csrf: CSRF_2 });
    const before = sent.length;
    expect((await flow(client, method)).kind).toBe("answer");
    const mine = sent.slice(before).filter((one) => `${one.method} ${one.url}` === KEY[method]);
    expect(mine.map((one) => [one.body, one.headers["aibi-csrf"]])).toEqual([
      [mine[0]?.body ?? null, CSRF],
      [mine[0]?.body ?? null, CSRF_2],
    ]);
    // A server that asks for CSRF every time, for a bounded number of times (a client that sent
    // again without end would otherwise never yield to the test's timeout).
    let asked = 0;
    overrides[KEY[method]] = () => {
      asked += 1;
      return asked > 5 ? json(418, refusal("NOT_FOUND")) : json(403, refusal("CSRF_REQUIRED"));
    };
    const again = sent.length;
    expect(await flow(client, method)).toMatchObject({ kind: "refused", codes: ["CSRF_REQUIRED"] });
    expect(sent.slice(again).filter((one) => `${one.method} ${one.url}` === KEY[method]).length).toBe(2);
  });

  it.each(METHODS)("%s: a fetch that fails is an unknown outcome, sent once and never again", async (method) => {
    const { client } = await unlocked();
    await client.open("d");
    const held = client.holds("d");
    overrides[KEY[method]] = () => Promise.reject(new TypeError("Failed to fetch"));
    expect(await flow(client, method)).toEqual({ kind: "unknown", reason: "network" });
    expect(sent.filter((one) => `${one.method} ${one.url}` === KEY[method]).length).toBe(1);
    expect(client.holds("d")).toBe(held);
  });

  it.each(METHODS)("%s: a 401 locks, and the client holds nothing", async (method) => {
    const { client } = await unlocked();
    await client.open("d");
    overrides[KEY[method]] = () => json(401, refusal("TOKEN_REQUIRED"));
    expect(await flow(client, method)).toEqual({ kind: "locked", reason: "token" });
    expect([client.view().locked, client.holds("d")]).toEqual([true, false]);
  });

  it.each(METHODS)("%s: the idle limit past before it, with no timer run, locks: nothing is sent (n6)", async (method) => {
    const { client, clock } = await unlocked();
    await client.open("d");
    const nodes: Record<Method, JsonOut> = {
      takeOver: pick(body(await client.dataset("d")), "session"),
      queueAt: pick(body(await client.dataset("d")), "labels", 1),
      rejectProposal: pick(body(await client.queue("d")), "proposals", 0),
      acceptProposal: pick(body(await client.queue("d")), "proposals", 0),
    };
    clock.wall += IDLE_LIMIT_MS;
    const before = sent.length;
    expect(await client[method](nodes[method])).toEqual({ kind: "locked", reason: "idle" });
    expect(sent.length).toBe(before);
    expect(client.holds("d")).toBe(false);
  });

  it.each(["takeOver", "acceptProposal"] as const)("%s: a late answer, after a forget, from a server that ignores the abort, is unknown and holds nothing (n6)", async (method) => {
    const { client } = await unlocked();
    await client.open("d");
    let release: (response: Response) => void = () => undefined;
    let arrived: () => void = () => undefined;
    const reached = new Promise<void>((resolve) => {
      arrived = resolve;
    });
    overrides[KEY[method]] = () =>
      new Promise<Response>((resolve) => {
        release = resolve;
        arrived();
      });
    const running = flow(client, method);
    await reached;
    client.forget();
    const answer = method === "takeOver" ? json(200, { dataset: "d", session: 2, base: RAW_STATE.session.base, draft: SESSION_DRAFT, handle: HANDLE }) : json(200, { dataset: "d", draft: DRAFT_3 });
    release(answer);
    const outcome = await running;
    expect(outcome.kind).toBe("unknown");
    expect(client.holds("d")).toBe(false);
    await client.unlock(TOKEN, NAME);
    expect(client.holds("d")).toBe(false);
    expect(JSON.stringify(outcome)).not.toContain("ses_");
  });

  it("no outcome, view or error of the four holds a handle (the take-over's answer included)", async () => {
    const { client } = await unlocked();
    await client.open("d");
    const outcomes: unknown[] = [];
    for (const method of METHODS) {
      outcomes.push(await flow(client, method));
    }
    const texts = (value: unknown): string[] =>
      typeof value === "string" ? [value] : typeof value === "object" && value !== null && !isServerNumber(value) ? Object.entries(value).flatMap(([key, member]) => [key, ...texts(member)]) : [];
    const seen = texts([outcomes, client.view()]).join("\n");
    expect(seen).toContain("dataset");
    expect([seen.includes(HANDLE), seen.includes(HANDLE_OPEN), seen.includes(CSRF), seen.includes(TOKEN)]).toEqual([false, false, false, false]);
  });
});

describe("idAt, the one function that reads a box's text, refuses everything but a stamped node at its feed in its era (MA3 of round 3)", () => {
  const ERA = Object.freeze({});
  const FEED = "path:proposal";
  const origin = (over: Partial<Origin> = {}): Origin => ({ operation: "queue", path: "proposals[]", dataset: "d", era: ERA, ...over });
  /** A node as a decoded answer gives it: frozen. */
  type Rec = { readonly [member: string]: JsonOut };
  const seal = (node: object): Rec => {
    const given: unknown = Object.freeze(node);
    return given as Rec;
  };
  /** A decoded node `{ id: <box of text> }`, stamped before it is frozen, as the recorder does. */
  const stamped = (text: string, over: Partial<Origin> = {}, members: Record<string, unknown> = {}): Rec => {
    const node = { id: box(text), ...members };
    stamp(node, origin(over));
    return seal(node);
  };
  const refusal = (found: ReturnType<typeof idAt>): Refusal | "fed" => (typeof found === "string" ? found : "fed");

  it("gives the dataset, the id and the node of a stamped node at its feed, in its era", () => {
    const node = stamped("7");
    expect(idAt(node, FEED, ERA)).toEqual({ dataset: "d", id: 7, node });
  });

  const CASES: readonly (readonly [string, () => JsonOut, Refusal])[] = [
    ["a node of another operation", () => stamped("7", { operation: "dataset_state" }), "origin"],
    ["a node of another path", () => stamped("7", { path: "labels[]" }), "origin"],
    ["a node of another era", () => stamped("7", { era: Object.freeze({}) }), "stale"],
    ["a node of no origin", () => seal({ id: box("7") }), "node"],
    ["a copy of a stamped node", () => seal({ ...stamped("7") }), "node"],
    ["a copy through the prototype", () => seal(Object.create(stamped("7")) as object), "node"],
    ["a box", () => box("7"), "node"],
    ["a string", () => "7", "node"],
    ["null", () => null, "node"],
    ["a list", () => Object.freeze([box("7")]), "node"],
    ["a node whose member is no box", () => stamped("7", {}, { id: "7" }), "member"],
    ["a node whose member is a plain number", () => stamped("7", {}, { id: 7 }), "member"],
    [
      "a node without the member",
      () => {
        const node = {};
        stamp(node, origin());
        return seal(node);
      },
      "member",
    ],
    [
      "a node whose member the prototype gives, not the node",
      () => {
        const node = Object.create({ id: box("7") }) as object;
        stamp(node, origin());
        return seal(node);
      },
      "member",
    ],
    ["a node whose box is not a request id", () => stamped("01"), "id"],
    ["a node whose box is a count's spelling", () => stamped("1.5"), "id"],
    ["a node whose box is past 2^53 - 1", () => stamped("9007199254740993"), "id"],
    ["a node whose box is zero", () => stamped("0"), "id"],
  ];

  it.each(CASES)("refuses %s", (_case, make, why) => {
    expect(refusal(idAt(make(), FEED, ERA))).toBe(why);
  });

  it("checks the origin before the era, and the era before the member: the first refusal is the reason given", () => {
    expect(refusal(idAt(stamped("01", { path: "labels[]", era: Object.freeze({}) }), FEED, ERA))).toBe("origin");
    expect(refusal(idAt(stamped("01", { era: Object.freeze({}) }), FEED, ERA))).toBe("stale");
    expect(refusal(idAt(stamped("01", {}, { id: "x" }), FEED, ERA))).toBe("member");
  });

  it("reads the member the feed names: a session's id is read from `session`, a label's from `label`", () => {
    const session = { session: box("12"), id: box("3") };
    stamp(session, origin({ operation: "dataset_state", path: "session" }));
    expect(idAt(seal(session), "TakeOver.session", ERA)).toMatchObject({ id: 12 });
    const label = { label: box("5"), id: box("3") };
    stamp(label, origin({ operation: "dataset_state", path: "labels[]" }));
    expect(idAt(seal(label), "query:release", ERA)).toMatchObject({ id: 5 });
  });

  it("takes a position's name and looks its feed up itself: a proposal's `value` is never read, whatever feed a caller builds (m14 of round 4)", () => {
    const proposal = { id: box("7"), value: box("4217") };
    stamp(proposal, origin());
    const node = seal(proposal);
    expect(idAt(node, "path:proposal", ERA)).toMatchObject({ id: 7 });
    // A feed handed in place of the name is no position: the lookup finds nothing, and nothing is read.
    const built: unknown = { ...FEEDS["path:proposal"], read: "value" };
    const forged = idAt as (node: JsonOut, position: unknown, era: object) => unknown;
    expect(() => forged(node, built, ERA)).toThrow(TypeError);
    expect(() => forged(node, "value", ERA)).toThrow(TypeError);
  });
});

describe("FEEDS is frozen through and through: no alias writes a feed (m14 of round 4: the second instance of m10's class)", () => {
  it("the table and each feed are frozen, and a write through an alias throws", () => {
    expect(Object.isFrozen(FEEDS)).toBe(true);
    for (const feed of Object.values(FEEDS)) {
      expect(Object.isFrozen(feed)).toBe(true);
    }
    const loose: { read: string } = FEEDS["path:proposal"];
    expect(() => {
      loose.read = "value";
    }).toThrow(TypeError);
    expect(FEEDS["path:proposal"].read).toBe("id");
    const table: Record<string, unknown> = FEEDS;
    expect(() => {
      table["path:proposal"] = FEEDS["query:release"];
    }).toThrow(TypeError);
    expect(() => {
      table["extra"] = FEEDS["query:release"];
    }).toThrow(TypeError);
  });
});
