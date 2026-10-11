/**
 * The one place of I/O (D419): the 8 MiB cap counted on the stream as it arrives (a
 * `Content-Length` only refuses earlier), the stream cancelled when refused, UTF-8 decoded across
 * chunks; `exchange` sends only to a route `routes.ts` made, and reads only JSON, through the cap
 * and the decoder.
 */
import { readFileSync } from "node:fs";
import path from "node:path";

import { afterEach, describe, expect, it, vi } from "vitest";

import { isServerNumber, textOf } from "../../src/api/box";
import { ClientError, exchange, readCapped, REFUSALS, RESPONSE_CAP_BYTES } from "../../src/api/client";
import * as routes from "../../src/api/generated/routes";
import type { JsonOut } from "../../src/api/json";
import { route, type Route } from "../../src/api/route";

const MiB = 1024 * 1024;

/** A decoded value with each box shown as `#` and its text (`JSON.stringify` would call the
 * box's `toJSON`, which throws, before any replacer). */
function shown(value: JsonOut): unknown {
  if (isServerNumber(value)) {
    return `#${textOf(value)}`;
  }
  if (Array.isArray(value)) {
    return (value as readonly JsonOut[]).map(shown);
  }
  if (typeof value === "object" && value !== null) {
    return Object.fromEntries(Object.entries(value).map(([key, member]) => [key, shown(member)]));
  }
  return value;
}

/** A stream of `chunks` chunks of `size` bytes each (`Infinity`: one that never ends), which
 * records how many it gave and whether it was cancelled. */
function chunked(size: number, chunks = Infinity, fill = 0x20): { stream: ReadableStream<Uint8Array>; given: () => number; cancelled: () => boolean } {
  let given = 0;
  let cancelled = false;
  const stream = new ReadableStream<Uint8Array>(
    {
      pull(controller) {
        if (given >= chunks) {
          controller.close();
          return;
        }
        given += 1;
        controller.enqueue(new Uint8Array(size).fill(fill));
      },
      cancel() {
        cancelled = true;
      },
    },
    { highWaterMark: 0 },
  );
  return { stream, given: () => given, cancelled: () => cancelled };
}

function answer(body: BodyInit | null, headers: Record<string, string> = {}, status = 200): Response {
  return new Response(body, { status, headers });
}

describe("the cap", () => {
  it("is 8 MiB", () => {
    expect(RESPONSE_CAP_BYTES).toBe(8 * MiB);
  });

  it("counts a chunked body as it arrives and cancels the stream past the cap", async () => {
    const fake = chunked(MiB);
    await expect(readCapped(answer(fake.stream))).rejects.toThrow(new ClientError(REFUSALS.tooLarge));
    expect(fake.cancelled()).toBe(true);
    expect(fake.given()).toBe(9);
  });

  it("counts a body whose Content-Length says less than it sends", async () => {
    const fake = chunked(MiB);
    await expect(readCapped(answer(fake.stream, { "Content-Length": "10" }))).rejects.toThrow(REFUSALS.tooLarge);
    expect(fake.cancelled()).toBe(true);
  });

  it("refuses a Content-Length above the cap before reading", async () => {
    const fake = chunked(MiB, 20);
    await expect(readCapped(answer(fake.stream, { "Content-Length": String(20 * MiB) }))).rejects.toThrow(REFUSALS.tooLarge);
    expect(fake.given()).toBe(0);
    expect(fake.cancelled()).toBe(true);
  });

  it("reads a body of exactly the cap, and refuses one byte more", async () => {
    const cap = 1000;
    expect(await readCapped(answer(chunked(100, 10).stream), cap)).toBe(" ".repeat(1000));
    const over = chunked(1001, 1);
    await expect(readCapped(answer(over.stream), cap)).rejects.toThrow(REFUSALS.tooLarge);
    expect(over.cancelled()).toBe(true);
  });

  it("decodes UTF-8 split across chunks", async () => {
    const bytes = new TextEncoder().encode('"é€😀"');
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        for (const byte of bytes) {
          controller.enqueue(new Uint8Array([byte]));
        }
        controller.close();
      },
    });
    expect(await readCapped(answer(stream))).toBe('"é€😀"');
  });

  it.each([
    ["a lone continuation byte", [0x22, 0x80, 0x22]],
    ["a truncated sequence at the end", [0x22, 0xe2, 0x82]],
    ["an overlong encoding", [0xc0, 0xaf]],
  ])("refuses %s", async (_case, bytes) => {
    await expect(readCapped(answer(new Uint8Array(bytes)))).rejects.toThrow(new ClientError(REFUSALS.notUtf8));
  });

  it("reports a stream that fails, a reset mid-body, in its own words, not as bad UTF-8", async () => {
    const reset = new Error("ECONNRESET 137.25");
    let pulls = 0;
    const stream = new ReadableStream<Uint8Array>({
      pull(controller) {
        pulls += 1;
        if (pulls === 1) {
          controller.enqueue(new TextEncoder().encode('{"a": 1'));
          return;
        }
        controller.error(reset);
      },
    });
    const refused = await readCapped(answer(stream)).catch((error: unknown) => error);
    expect(refused).toEqual(new ClientError(REFUSALS.interrupted));
    expect((refused as Error).message).not.toContain("137.25");
    expect((refused as Error).message).not.toContain("ECONNRESET");
    expect((refused as Error).cause).toBeUndefined();
    expect(REFUSALS.interrupted).not.toBe(REFUSALS.notUtf8);
  });

  it("reports a stream that fails before its first chunk in the same words", async () => {
    const stream = new ReadableStream<Uint8Array>({
      pull(controller) {
        controller.error(new Error("reset"));
      },
    });
    await expect(readCapped(answer(stream))).rejects.toThrow(new ClientError(REFUSALS.interrupted));
  });

  it("reports bad UTF-8 in the words of bad UTF-8, split across chunks or cut off at the end", async () => {
    const split = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new Uint8Array([0x22, 0xe2]));
        controller.enqueue(new Uint8Array([0x22]));
        controller.close();
      },
    });
    await expect(readCapped(answer(split))).rejects.toThrow(new ClientError(REFUSALS.notUtf8));
    const cut = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new Uint8Array([0x22, 0xf0, 0x9f]));
        controller.close();
      },
    });
    await expect(readCapped(answer(cut))).rejects.toThrow(new ClientError(REFUSALS.notUtf8));
  });

  it("cancels the stream after bad UTF-8, so that nothing keeps reading", async () => {
    let cancelled = false;
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new Uint8Array([0xc0, 0xaf]));
      },
      cancel() {
        cancelled = true;
      },
    });
    await expect(readCapped(answer(stream))).rejects.toThrow(REFUSALS.notUtf8);
    expect(cancelled).toBe(true);
  });

  it("reads an empty body as empty text", async () => {
    expect(await readCapped(answer(null))).toBe("");
  });

  it("holds every golden envelope, the largest response a test knows, far below the cap", () => {
    const envelopes = JSON.parse(readFileSync(path.join(import.meta.dirname, "../fixtures/envelopes.json"), "utf8")) as Record<string, unknown>;
    const largest = Math.max(...Object.values(envelopes).map((envelope) => new TextEncoder().encode(JSON.stringify(envelope)).byteLength));
    expect(Object.keys(envelopes)).toHaveLength(19);
    expect(largest * 64).toBeLessThan(RESPONSE_CAP_BYTES);
  });
});

describe("exchange", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  /** A fake `fetch` that answers `body` with `type`, recording what it was asked. */
  function fake(body: string, type = "application/json", status = 200) {
    const asked: { url: string; init: RequestInit }[] = [];
    const fetch = vi.fn((url: string, init: RequestInit) => {
      asked.push({ url, init });
      return Promise.resolve(answer(body, { "Content-Type": type }, status));
    });
    vi.stubGlobal("fetch", fetch);
    return { asked, fetch };
  }

  it("sends a JSON body to the route's URL, with no ambient credential, never following a redirect", async () => {
    const { asked } = fake('{"hits": [], "total": 0}');
    const got = await exchange(routes.search_catalog(), { domain_tags: ["x"], limit: 5 });
    expect(asked).toHaveLength(1);
    expect(asked[0]?.url).toBe("/api/tools/search_catalog");
    const init = asked[0]?.init;
    expect(init?.method).toBe("POST");
    expect(init?.body).toBe('{"domain_tags":["x"],"limit":5}');
    expect(init?.credentials).toBe("omit");
    expect(init?.redirect).toBe("error");
    expect(init?.cache).toBe("no-store");
    expect(new Headers(init?.headers).get("Content-Type")).toBe("application/json");
    expect(got.status).toBe(200);
    expect(got.operation).toBe("search_catalog");
    const total = (got.body as Readonly<Record<string, JsonOut>>)["total"];
    expect(isServerNumber(total) && textOf(total)).toBe("0");
  });

  it("decodes a refusal's body too", async () => {
    fake('{"refusals": [{"code": "NOT_FOUND", "limit": 3}]}', "application/json; charset=utf-8", 404);
    const got = await exchange(routes.dataset_state({ dataset: "shop" }));
    expect(got.status).toBe(404);
    expect(shown(got.body)).toEqual({ refusals: [{ code: "NOT_FOUND", limit: "#3" }] });
  });

  it("refuses an answer that is not JSON, unread", async () => {
    fake("<html>", "text/html");
    await expect(exchange(routes.health())).rejects.toThrow(new ClientError(REFUSALS.notJson));
    fake("{}", "application/jsonx");
    await expect(exchange(routes.health())).rejects.toThrow(REFUSALS.notJson);
  });

  const lookalike: Route<"health"> = { operation: "health", method: "GET", url: "/api/health" };

  it.each([
    ["an object that only looks like a route", lookalike],
    ["a route to another origin", route("health", "GET", "https://elsewhere.example/api/health")],
    ["a route to another host by a protocol-relative URL", route("health", "GET", "//elsewhere.example/api/health")],
    ["a route outside the API", route("health", "GET", "/assets/x.js")],
    ["a route with a doubled slash", route("health", "GET", "/api//health")],
    ["a route with a backslash", route("health", "GET", "/api/\\elsewhere")],
  ])("refuses %s, sending nothing", async (_case, given) => {
    const { fetch } = fake("{}");
    await expect(exchange(given)).rejects.toThrow(new ClientError(REFUSALS.notARoute));
    expect(fetch).not.toHaveBeenCalled();
  });

  it.each([Number.NaN, Infinity, -Infinity])("refuses a body with %s, sending nothing", async (value) => {
    const { fetch } = fake("{}");
    await expect(exchange(routes.search_catalog(), { limit: value })).rejects.toThrow(REFUSALS.notFinite);
    expect(fetch).not.toHaveBeenCalled();
  });
});
