/**
 * The epoch against every timing (D423; n1 of the review of the request half's plan): a brute
 * force over where a lock falls relative to a late answer, for each operation that writes to the
 * client's closure (`unlock`, `open`, `change`, `publish`, `discard`) and one that does not
 * (`withdraw`). The answer is late and ignores the abort (a server that answers anyway); the lock
 * is a forget, a `pagehide`, the idle timer past the limit, or a 401 to another request of the
 * same epoch (for `unlock`, a forget or a `pagehide`: the client is locked while it unlocks, so
 * neither the idle timer nor another request of its epoch exists); it falls before the answer arrives, or after it, at every microtask from 0 to 80,
 * or after a macrotask. Whatever the timing, once the lock has happened the client is locked and
 * holds nothing (no handle, nothing it would send). A `commit` run in a later step than the epoch
 * check (an `await` between them) fails here.
 *
 * Also (n8): a 2xx answer's body given to a caller is the decoded root itself (identity: a later
 * provenance tag keyed on it holds), but `open`'s, given without its handle; and the idle lock
 * before each operation sends nothing.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createCurator, type Curator, IDLE_LIMIT_MS, type Outcome } from "../../src/api/curator";

const TOKEN = `aibi_${"T0k-_n".repeat(7)}Q`;
const CSRF = `${"CsRf-_".repeat(7)}Z`;
const HANDLE = `ses_${"HaNdLe".repeat(7)}X`;
const DRAFT = `sha256:${"a".repeat(64)}`;
const DRAFT_2 = `sha256:${"b".repeat(64)}`;
const MANIFEST = `sha256:${"c".repeat(64)}`;

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

interface Request {
  readonly url: string;
  readonly method: string;
}

/** The fake server: each request is answered by `answer` at once, or held (`hold` names it by
 * `METHOD url`) until the test releases it with a response, whatever the abort says. */
let requests: Request[] = [];
let held = new Map<string, (response: Response) => void>();
let holding = new Set<string>();
let answer: (request: Request) => Response = () => json(404, {});

beforeEach(() => {
  requests = [];
  held = new Map();
  holding = new Set();
  vi.stubGlobal("fetch", (url: string, init?: RequestInit): Promise<Response> => {
    const request = { url, method: init?.method ?? "GET" };
    requests.push(request);
    const key = `${request.method} ${url}`;
    if (holding.has(key)) {
      holding.delete(key);
      return new Promise<Response>((resolve) => {
        held.set(key, resolve);
      });
    }
    return Promise.resolve(answer(request));
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function server(request: Request): Response {
  switch (`${request.method} ${request.url}`) {
    case "GET /operator/csrf":
      return json(200, { csrf: CSRF });
    case "POST /operator/datasets/d/session/open":
      return json(200, { dataset: "d", session: 7, base: MANIFEST, draft: DRAFT, handle: HANDLE });
    case "POST /operator/datasets/d/session/change":
      return json(200, { dataset: "d", draft: DRAFT_2 });
    case "POST /operator/datasets/d/session/publish":
      return json(200, { dataset: "d", label: 2 });
    case "POST /operator/datasets/d/session/discard":
      return json(200, { dataset: "d", outcome: "discarded" });
    case "POST /operator/datasets/d/withdraw":
      return json(200, { dataset: "d", labels: [] });
    default:
      return json(200, { datasets: [] });
  }
}

class Target {
  readonly listeners = new Map<string, ((event: unknown) => void)[]>();
  addEventListener(type: string, listener: (event: unknown) => void): void {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }
  removeEventListener(): void {
    // never called
  }
  dispatchEvent(): boolean {
    return true;
  }
  fire(type: string): void {
    for (const listener of this.listeners.get(type) ?? []) {
      listener({ type, isTrusted: false });
    }
  }
}

interface Rig {
  readonly client: Curator;
  readonly window: Target;
  readonly idle: () => void;
}

function rig(): Rig {
  let wall = 1_800_000_000_000;
  let monotonic = 1000;
  const timers: (() => void)[] = [];
  const client = createCurator(
    { wall: () => wall, monotonic: () => monotonic },
    {
      set: (run) => {
        timers.push(run);
        return timers.length;
      },
      clear: () => undefined,
    },
  );
  const window = new Target();
  client.install({ window, document: new Target() });
  return {
    client,
    window,
    idle: () => {
      wall += IDLE_LIMIT_MS;
      monotonic += IDLE_LIMIT_MS;
      for (const run of [...timers]) {
        run();
      }
    },
  };
}

type Operation = "unlock" | "open" | "change" | "publish" | "discard" | "withdraw";
type Lock = "forget" | "pagehide" | "idle" | "401";

const OPERATIONS: Readonly<Record<Operation, { readonly key: string; readonly session: boolean; readonly run: (client: Curator) => Promise<unknown> }>> = {
  unlock: { key: "GET /operator/csrf", session: false, run: (client) => client.unlock(TOKEN, "Ada") },
  open: { key: "POST /operator/datasets/d/session/open", session: false, run: (client) => client.open("d") },
  change: { key: "POST /operator/datasets/d/session/change", session: true, run: (client) => client.change("d", [{ op: "confirm", descriptor: "t" }]) },
  publish: { key: "POST /operator/datasets/d/session/publish", session: true, run: (client) => client.publish("d") },
  discard: { key: "POST /operator/datasets/d/session/discard", session: true, run: (client) => client.discard("d") },
  withdraw: { key: "POST /operator/datasets/d/withdraw", session: false, run: (client) => client.withdraw("d", MANIFEST) },
};

const LOCKS: readonly Lock[] = ["forget", "pagehide", "idle", "401"];

/** Where the lock falls: before the late answer, after it at a microtask, or after a macrotask. */
const TIMINGS: readonly (number | "before" | "macrotask")[] = ["before", ...Array.from({ length: 81 }, (_, index) => index), "macrotask"];

async function microtasks(count: number): Promise<void> {
  for (let step = 0; step < count; step += 1) {
    await Promise.resolve();
  }
}

/** One case: what the client holds once the operation and the lock are both done. */
async function trial(operation: Operation, lock: Lock, timing: number | "before" | "macrotask"): Promise<{ locked: boolean; holds: boolean; sent: number }> {
  answer = server;
  const { client, window, idle } = rig();
  const plan = OPERATIONS[operation];
  if (operation !== "unlock") {
    await client.unlock(TOKEN, "Ada");
  }
  if (plan.session) {
    await client.open("d");
  }
  let other: Promise<unknown> = Promise.resolve();
  if (lock === "401") {
    holding.add("GET /operator/datasets");
    other = client.datasets();
  }
  holding.add(plan.key);
  const running = plan.run(client);
  await vi.waitFor(() => {
    expect(held.has(plan.key)).toBe(true);
  });
  const fire = (): void => {
    switch (lock) {
      case "forget":
        client.forget();
        break;
      case "pagehide":
        window.fire("pagehide");
        break;
      case "idle":
        idle();
        break;
      case "401":
        held.get("GET /operator/datasets")?.(json(401, { refusals: [{ code: "TOKEN_REQUIRED", path: null, message: [], alternatives: [] }] }));
        break;
    }
  };
  if (timing === "before") {
    fire();
  }
  held.get(plan.key)?.(server({ url: plan.key.split(" ")[1] ?? "", method: plan.key.split(" ")[0] ?? "" }));
  if (timing === "macrotask") {
    await new Promise((resolve) => setTimeout(resolve, 0));
    fire();
  } else if (timing !== "before") {
    await microtasks(timing);
    fire();
  }
  await running;
  await other;
  await new Promise((resolve) => setTimeout(resolve, 0));
  const before = requests.length;
  const after = await client.datasets();
  return { locked: client.view().locked && after.kind === "locked", holds: client.holds("d"), sent: requests.length - before };
}

describe("a lock against a late answer, at every timing", () => {
  it.each((Object.keys(OPERATIONS) as Operation[]).flatMap((operation) => LOCKS.filter((lock) => operation !== "unlock" || lock === "forget" || lock === "pagehide").map((lock) => [operation, lock] as const)))(
    "%s, and a lock by %s: once both are done, locked and holding nothing",
    async (operation, lock) => {
      const wrong: string[] = [];
      for (const timing of TIMINGS) {
        const found = await trial(operation, lock, timing);
        if (!found.locked || found.holds || found.sent !== 0) {
          wrong.push(`${String(timing)}: ${JSON.stringify(found)}`);
        }
      }
      expect(wrong).toEqual([]);
    },
    60_000,
  );

  it("would see a write after the check: with no lock at all, the operations do hold (the trial's control)", async () => {
    answer = server;
    const { client } = rig();
    await client.unlock(TOKEN, "Ada");
    expect((await client.open("d")).kind).toBe("answer");
    expect([client.view().locked, client.holds("d")]).toEqual([false, true]);
  });
});

describe("what a caller is given (n8)", () => {
  it("a 2xx body is the decoded root itself; open's is that root without its handle", async () => {
    vi.resetModules();
    const parse = vi.spyOn(JSON, "parse");
    try {
      const fresh = await import("../../src/api/curator");
      answer = server;
      const client = fresh.createCurator();
      await client.unlock(TOKEN, "Ada");
      const roots = (): unknown[] => parse.mock.results.map((result) => result.value as unknown);
      for (const [name, run] of [
        ["datasets", () => client.datasets()],
        ["dataset", () => client.dataset("d")],
        ["queue", () => client.queue("d")],
        ["withdraw", () => client.withdraw("d", MANIFEST)],
      ] as const) {
        const outcome: Outcome = await run();
        expect(outcome.kind, name).toBe("answer");
        expect(outcome.kind === "answer" && outcome.body === roots().at(-1), name).toBe(true);
      }
      const opened = await client.open("d");
      const root = roots().at(-1);
      expect(opened.kind === "answer" && opened.body !== root && Object.isFrozen(opened.body)).toBe(true);
      expect(opened.kind === "answer" ? Object.keys(opened.body ?? {}) : []).toEqual(["dataset", "session", "base", "draft"]);
      const changed = await client.change("d", [{ op: "confirm", descriptor: "t" }]);
      expect(changed.kind === "answer" && changed.body === roots().at(-1)).toBe(true);
    } finally {
      parse.mockRestore();
    }
  });

  it.each(Object.keys(OPERATIONS).filter((name) => name !== "unlock"))("the idle lock before %s sends nothing", async (operation) => {
    answer = server;
    const { client, idle } = rig();
    await client.unlock(TOKEN, "Ada");
    if (OPERATIONS[operation as Operation].session) {
      await client.open("d");
    }
    idle();
    const before = requests.length;
    const outcome = (await OPERATIONS[operation as Operation].run(client)) as Outcome;
    expect(outcome.kind === "locked" || (outcome.kind === "invalid" && outcome.what === "session")).toBe(true);
    expect(requests.length).toBe(before);
    expect(client.view()).toEqual({ locked: true, reason: "idle" });
  });

  it.each(["datasets", "dataset", "queue", "open", "withdraw"] as const)("the idle lock checked before %s itself: past the limit with no timer run, nothing is sent", async (operation) => {
    answer = server;
    let wall = 1_800_000_000_000;
    const client = createCurator({ wall: () => wall, monotonic: () => 0 }, { set: () => 0, clear: () => undefined });
    await client.unlock(TOKEN, "Ada");
    wall += IDLE_LIMIT_MS;
    const before = requests.length;
    const run = { datasets: () => client.datasets(), dataset: () => client.dataset("d"), queue: () => client.queue("d"), open: () => client.open("d"), withdraw: () => client.withdraw("d", MANIFEST) }[operation];
    expect(await run()).toEqual({ kind: "locked", reason: "idle" });
    expect(requests.length).toBe(before);
  });
});
