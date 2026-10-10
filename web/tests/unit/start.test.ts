/**
 * The client's start (D419): on an engine without what the client needs it seals nothing and
 * says so; otherwise every body reader of `Response` and `Request` but the stream throws, for
 * good, and starting again is harmless. (This file seals its own environment's prototypes.)
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { REFUSALS, SEALED_READERS, sealBodyReaders, start, supported } from "../../src/api/client";
import { freshReaders } from "./render";

describe("start", () => {
  it("seals nothing on an engine the client cannot run on", () => {
    expect(start(() => false)).toBe(false);
    for (const name of SEALED_READERS) {
      expect(Object.getOwnPropertyDescriptor(Response.prototype, name)?.configurable).toBe(true);
    }
  });

  it("finds this engine supported", () => {
    expect(supported()).toBe(true);
  });

  it("seals every body reader of Response and Request but the stream", async () => {
    expect(start()).toBe(true);
    expect(SEALED_READERS).toEqual(["json", "text", "arrayBuffer", "blob", "bytes", "formData"]);
    const response = new Response('{"a": 1}');
    const request = new Request("http://127.0.0.1/x", { method: "POST", body: "1" });
    for (const name of SEALED_READERS) {
      expect(() => (response[name] as () => unknown)()).toThrow(new TypeError(REFUSALS.sealed));
      expect(() => (request[name] as () => unknown)()).toThrow(new TypeError(REFUSALS.sealed));
      const found = Object.getOwnPropertyDescriptor(Response.prototype, name);
      expect([found?.writable, found?.configurable]).toEqual([false, false]);
    }
    expect(response.body).not.toBeNull();
    const reader = response.body?.getReader();
    expect((await reader?.read())?.done).toBe(false);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("knows only the readers it installed itself: another copy of the module does not know these", async () => {
    vi.resetModules();
    const other = await import("../../src/api/client");
    expect(other.sealBodyReaders).not.toBe(sealBodyReaders);
    expect(other.sealBodyReaders()).toBe(false);
    expect(other.start()).toBe(false);
    freshReaders();
    expect(other.start()).toBe(true);
    expect(other.start()).toBe(true);
    expect(sealBodyReaders()).toBe(false);
  });

  /** A stand-in for `Response` and `Request` whose `reader` was fixed (not writable, not
   * configurable) to `value` by code that ran before the client. */
  function fixed(reader: string, value: unknown): void {
    freshReaders();
    for (const name of ["Response", "Request"] as const) {
      Object.defineProperty(globalThis[name].prototype, reader, { value, writable: false, configurable: false });
    }
  }

  it.each(SEALED_READERS)("is not fooled by a %s that carries the old global mark, fixed before it ran", (reader) => {
    const fake = (): never => {
      throw new TypeError(REFUSALS.sealed);
    };
    Object.defineProperty(fake, Symbol.for("aibi.client.sealed-body-reader"), { value: true });
    Object.defineProperty(fake, Symbol("aibi.client.sealed-body-reader"), { value: true });
    fixed(reader, fake);
    expect(start()).toBe(false);
    expect(sealBodyReaders()).toBe(false);
  });

  it.each(SEALED_READERS)("is not fooled by a %s that is the client's own function, copied from a sealed reader", (reader) => {
    freshReaders();
    expect(start()).toBe(true);
    const real = Object.getOwnPropertyDescriptor(Response.prototype, reader)?.value as unknown;
    expect(typeof real).toBe("function");
    // The client's own reader is known to this copy alone: a copy of it is another function.
    fixed(reader, (real as () => never).bind(undefined));
    expect(start()).toBe(false);
  });

  it.each(SEALED_READERS)("is not fooled by a %s another script fixed, when WeakSet.prototype.has was patched before start", (reader) => {
    const has = vi.spyOn(WeakSet.prototype, "has").mockReturnValue(true);
    const fake = (): never => {
      throw new TypeError("OWN");
    };
    fixed(reader, fake);
    expect(start()).toBe(false);
    expect(sealBodyReaders()).toBe(false);
    expect(has).not.toHaveBeenCalled();
  });

  /** Run `body` with `Object.defineProperty` replaced by `replacement`, and put it back. */
  function patchedDefine(replacement: (target: object, key: PropertyKey, descriptor: PropertyDescriptor) => object, body: () => void): void {
    const original = Object.defineProperty;
    Object.defineProperty = replacement as typeof Object.defineProperty;
    try {
      body();
    } finally {
      Object.defineProperty = original;
    }
  }

  it("refuses to start, sealing nothing, when Object.defineProperty is a no-op", () => {
    freshReaders();
    patchedDefine((target) => target, () => {
      expect(start()).toBe(false);
    });
    for (const name of SEALED_READERS) {
      expect(Object.getOwnPropertyDescriptor(Response.prototype, name)?.configurable).toBe(true);
    }
  });

  it.each([{ configurable: true }, { writable: true }])("refuses to start when a definition took but not as asked (%j)", (change) => {
    freshReaders();
    patchedDefine(
      (target, key, descriptor) => {
        Reflect.defineProperty(target, key, { ...descriptor, ...change });
        return target;
      },
      () => {
        expect(start()).toBe(false);
      },
    );
  });

  it("refuses to start on an engine without JSON.rawJSON, and seals nothing", () => {
    freshReaders();
    const saved = Object.getOwnPropertyDescriptor(JSON, "rawJSON");
    expect(saved).toBeDefined();
    try {
      Reflect.deleteProperty(JSON, "rawJSON");
      expect("rawJSON" in JSON).toBe(false);
      expect(supported()).toBe(false);
      expect(start()).toBe(false);
      for (const name of SEALED_READERS) {
        expect(Object.getOwnPropertyDescriptor(Response.prototype, name)?.configurable).toBe(true);
      }
    } finally {
      if (saved !== undefined) {
        Object.defineProperty(JSON, "rawJSON", saved);
      }
    }
    expect(supported()).toBe(true);
  });

  it("refuses an engine whose JSON.rawJSON is no function", () => {
    freshReaders();
    const saved = Object.getOwnPropertyDescriptor(JSON, "rawJSON");
    try {
      Object.defineProperty(JSON, "rawJSON", { value: 1, configurable: true, writable: true });
      expect(supported()).toBe(false);
      expect(start()).toBe(false);
    } finally {
      if (saved !== undefined) {
        Object.defineProperty(JSON, "rawJSON", saved);
      }
    }
  });

  it("refuses to start where a reader is fixed to something else", () => {
    function Fixed(): void {
      // a stand-in for Response whose json reader something else fixed
    }
    Object.defineProperty(Fixed.prototype, "json", { value: () => 1, configurable: false });
    const saved = globalThis.Response;
    globalThis.Response = Fixed as unknown as typeof Response;
    try {
      expect(start()).toBe(false);
    } finally {
      globalThis.Response = saved;
    }
  });

  it("is harmless twice, and the seal cannot be undone", () => {
    expect(start()).toBe(true);
    expect(() => {
      Object.defineProperty(Response.prototype, "json", { value: () => 1 });
    }).toThrow(TypeError);
    expect(() => new Response("1").json()).toThrow(REFUSALS.sealed);
  });
});
