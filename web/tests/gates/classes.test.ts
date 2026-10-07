/**
 * The classes of the request side's numbers (`src/api/classes.json`, D416, D419): the single
 * hand source, which the server's test reads too (`server/tests/core/api/test_openapi.py`). This
 * port of that test's `request_numbers` walks the document the same way, and the positions it
 * finds are exactly the classes' union, so the client's later checks over the positions (the
 * request half: each position's builder, M5.1c-1c and M5.3) cannot pass for a position the port
 * missed.
 */
import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { DOCUMENT } from "../../scripts/generate-types.mjs";
import { WEB } from "./program";

type Json = Record<string, unknown>;

const COMPONENTS = "#/components/schemas/";
const CONDITIONS = new Set(["if", "then", "else", "not"]);
const LISTS = new Set(["allOf", "anyOf", "oneOf", "prefixItems"]);

const isObject = (value: unknown): value is Json => typeof value === "object" && value !== null && !Array.isArray(value);

function numeric(node: Json): boolean {
  const kind = node["type"];
  const kinds = Array.isArray(kind) ? kind : [kind];
  return kinds.includes("number") || kinds.includes("integer");
}

/** Every position a request takes a number at: `path:proposal`, `query:release`, a body's
 * `Component.member`, `[]` for an item, `.*` for a pattern's member, `{}` for an additional one;
 * a component's name for a number at its top (`DocumentJson`). A port of the server test's. */
export function requestNumbers(document: Json): Set<string> {
  const found = new Set<string>();
  const schemas = (document["components"] as { schemas: Record<string, Json> }).schemas;
  const walk = (node: unknown, at: string, seen: ReadonlySet<string>): void => {
    if (!isObject(node)) {
      return;
    }
    if (numeric(node)) {
      found.add(at);
    }
    for (const [key, value] of Object.entries(node)) {
      if (CONDITIONS.has(key)) {
        continue;
      }
      if (key === "$ref" && typeof value === "string") {
        const name = value.slice(COMPONENTS.length);
        if (!seen.has(name)) {
          walk(schemas[name], name, new Set([...seen, name]));
        }
      } else if (key === "properties" && isObject(value)) {
        for (const [member, schema] of Object.entries(value)) {
          walk(schema, `${at}.${member}`, seen);
        }
      } else if (key === "patternProperties" && isObject(value)) {
        for (const schema of Object.values(value)) {
          walk(schema, `${at}.*`, seen);
        }
      } else if (key === "items" || key === "prefixItems") {
        for (const schema of Array.isArray(value) ? value : [value]) {
          walk(schema, `${at}[]`, seen);
        }
      } else if (key === "additionalProperties") {
        walk(value, `${at}{}`, seen);
      } else if (LISTS.has(key) && Array.isArray(value)) {
        for (const schema of value) {
          walk(schema, at, seen);
        }
      }
    }
  };
  const paths = document["paths"] as Record<string, Record<string, Json>>;
  for (const methods of Object.values(paths)) {
    for (const operation of Object.values(methods)) {
      for (const given of (operation["parameters"] ?? []) as Json[]) {
        walk(given["schema"], `${String(given["in"])}:${String(given["name"])}`, new Set());
      }
      const body = operation["requestBody"];
      const content = isObject(body) && isObject(body["content"]) ? body["content"] : {};
      for (const media of Object.values(content)) {
        walk(isObject(media) ? media["schema"] : undefined, "body", new Set());
      }
    }
  }
  return found;
}

const document = JSON.parse(readFileSync(DOCUMENT, "utf8")) as Json;
const classes = JSON.parse(readFileSync(path.join(WEB, "src/api/classes.json"), "utf8")) as Record<string, { why: string; positions: string[] }>;

describe("the classes of the request's numbers", () => {
  it("are four, each a reason and its positions, disjoint", () => {
    expect(Object.keys(classes)).toEqual(["adapter_fed", "typed_by_the_person", "both", "never_in_the_client"]);
    const all = Object.values(classes).flatMap((given) => given.positions);
    expect(new Set(all).size).toBe(all.length);
    for (const given of Object.values(classes)) {
      expect(Object.keys(given)).toEqual(["why", "positions"]);
      expect(given.why.length).toBeGreaterThan(30);
      expect(given.positions).toEqual([...given.positions].sort());
    }
  });

  it("are, together, exactly the positions the document's requests take a number at", () => {
    const union = new Set(Object.values(classes).flatMap((given) => given.positions));
    const found = requestNumbers(document);
    expect([...found].sort()).toEqual([...union].sort());
    expect(found.size).toBe(30);
  });

  it("the port finds a position the classes would miss", () => {
    const changed = structuredClone(document);
    const schemas = (changed["components"] as { schemas: Record<string, Json> }).schemas;
    const search = schemas["SearchCatalog"];
    expect(search).toBeDefined();
    (search?.["properties"] as Json)["extra"] = { type: "integer" };
    expect(requestNumbers(changed).has("SearchCatalog.extra")).toBe(true);
  });
});
