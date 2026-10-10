/**
 * The generated API types and their gates (D416, D419), each asked of the type checker on the
 * real generated files, the real document and the golden envelopes:
 *
 * (a) the generated files compile under the app's options, with no `skipLibCheck`;
 * (b) an `unknown` stands only where openapi-typescript renders an `allOf` conjunct it cannot
 *     type (a later member of an intersection) or a response's headers, and every property of
 *     every component resolves to a type (never `unknown` or `any`), the named ones included;
 * (c) the golden envelopes compile as `ResultEnvelope` literals, every number a `ServerNumber`,
 *     and each envelope with an extra member or a number written as text does not;
 * (d) a corpus of the document's keyword combinations renders faithfully (an instance accepted,
 *     one refused, each), and without the transform each `x-aibi-json` component is TS2502;
 * (e) the transform reaches every mark (the census read from the document, never pinned), no
 *     numeric literal type survives (the six numeric consts under `cohorts`' `allOf` conjunct are
 *     erased), and the instance location `ResultEnvelope["cohorts"][number]["position"]` is a
 *     `ServerNumber`;
 * (f) a request integer takes a JS number and no `ServerNumber`; a `ServerNumber` is no
 *     `number`, `string`, `JsonIn` or `JsonInNull`, and arithmetic on two is an error.
 *
 * (g) no JS number is reachable from any operation's responses (the type checker's resolved types,
 *     walked through unions, intersections, arrays, tuples, index signatures and every property,
 *     the request side, which holds the integers, as the control): a position typed `number |
 *     ServerNumber` passes (a), (c) and (e), and fails here.
 *
 * Beside them, the generator's own refusals and `--check`.
 */
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";

import ts from "typescript";
import { beforeAll, describe, expect, it } from "vitest";

import {
  census,
  DOCUMENT,
  generate,
  GenerationError,
  main,
  OUTPUTS,
  renderRoutes,
  renderTypes,
  templateParts,
} from "../../scripts/generate-types.mjs";
import { appOptions, check, type Checked, WEB } from "./program";

type Json = Record<string, unknown>;

const document = JSON.parse(readFileSync(DOCUMENT, "utf8")) as Json;
const GENERATED = "src/api/generated/openapi.ts";

const HEAD = `import type { components, JsonIn, JsonInNull, JsonOut, ServerNumber } from "./api";
type S = components["schemas"];
declare const n: ServerNumber;
declare const m: ServerNumber;
declare const hits: S["CatalogHits"];
`;

let real: Checked;

beforeAll(() => {
  real = check({ "src/probe-real.ts": `export * from "./api";\n` });
}, 120_000);

/** The resolved type of `components["schemas"][name]`. */
function component(checked: Checked, name: string): ts.Type {
  const { checker } = checked;
  const file = checked.source(GENERATED);
  const symbol = checker.getSymbolAtLocation(file);
  const components = symbol === undefined ? undefined : checker.getExportsOfModule(symbol).find((found) => found.name === "components");
  if (components === undefined) {
    throw new Error("no components");
  }
  const schemas = checker.getTypeOfSymbol(components).getProperty("schemas") ?? checker.getDeclaredTypeOfSymbol(components).getProperty("schemas");
  if (schemas === undefined) {
    throw new Error("no schemas");
  }
  const found = checker.getTypeOfSymbol(schemas).getProperty(name);
  if (found === undefined) {
    throw new Error(`no component ${name}`);
  }
  return checker.getTypeOfSymbol(found);
}

function property(checked: Checked, type: ts.Type, name: string): ts.Type {
  const found = type.getProperty(name);
  if (found === undefined) {
    throw new Error(`no property ${name}`);
  }
  return checked.checker.getTypeOfSymbol(found);
}

describe("(a) the generated files compile under the app's options", () => {
  it("with no skipLibCheck, and no error in any file of the client", () => {
    expect(appOptions().skipLibCheck).not.toBe(true);
    const errors = ts.getPreEmitDiagnostics(real.program).map((diagnostic) => ts.flattenDiagnosticMessageText(diagnostic.messageText, " "));
    expect(errors).toEqual([]);
    expect(real.program.getSourceFile(path.join(WEB, GENERATED))).toBeDefined();
    expect(real.program.getSourceFile(path.join(WEB, "src/api/generated/routes.ts"))).toBeDefined();
  });
});

describe("(b) where an unknown may stand, and every property typed", () => {
  it("an unknown is a later member of an intersection (an allOf conjunct) or a response's headers", () => {
    const file = real.source(GENERATED);
    const placed = { conjunct: 0, headers: 0 };
    const elsewhere: string[] = [];
    const visit = (node: ts.Node): void => {
      if (node.kind === ts.SyntaxKind.UnknownKeyword) {
        const parent = node.parent;
        if (ts.isIndexSignatureDeclaration(parent) && ts.isTypeLiteralNode(parent.parent)) {
          const holder = parent.parent.parent;
          if (ts.isPropertySignature(holder) && holder.name.getText(file) === "headers") {
            placed.headers += 1;
            return;
          }
        }
        for (let child: ts.Node = node, up = node.parent; !ts.isSourceFile(up); child = up, up = up.parent) {
          if (ts.isIntersectionTypeNode(up) && up.types.indexOf(child as ts.TypeNode) > 0) {
            placed.conjunct += 1;
            return;
          }
        }
        elsewhere.push(`line ${String(file.getLineAndCharacterOfPosition(node.getStart(file)).line + 1)}`);
      }
      ts.forEachChild(node, visit);
    };
    visit(file);
    expect(elsewhere).toEqual([]);
    expect(placed.conjunct).toBeGreaterThan(0);
    expect(placed.headers).toBeGreaterThan(0);
  });

  it("every property of every component resolves to a type, never unknown or any", () => {
    const { checker } = real;
    const untyped: string[] = [];
    const seen = new Set<ts.Type>();
    const walk = (type: ts.Type, at: string, depth: number): void => {
      if (seen.has(type) || depth > 12) {
        return;
      }
      seen.add(type);
      // A union's members each; an intersection as one object, whose properties the checker
      // merges (a conjunct's `not_estimable?: unknown` beside the typed one is `X & unknown`).
      if (type.isUnion()) {
        type.types.forEach((member) => {
          walk(member, at, depth + 1);
        });
        return;
      }
      if (checker.isArrayType(type) || checker.isTupleType(type)) {
        checker.getTypeArguments(type as ts.TypeReference).forEach((member) => {
          walk(member, `${at}[]`, depth + 1);
        });
        return;
      }
      if ((type.flags & (ts.TypeFlags.Object | ts.TypeFlags.Intersection)) === 0) {
        return;
      }
      for (const symbol of checker.getPropertiesOfType(type)) {
        const member = checker.getTypeOfSymbol(symbol);
        if ((member.flags & (ts.TypeFlags.Unknown | ts.TypeFlags.Any)) !== 0) {
          untyped.push(`${at}.${symbol.name}`);
        }
        walk(member, `${at}.${symbol.name}`, depth + 1);
      }
      const index = type.getStringIndexType();
      if (index !== undefined) {
        if ((index.flags & (ts.TypeFlags.Unknown | ts.TypeFlags.Any)) !== 0) {
          untyped.push(`${at}{}`);
        }
        walk(index, `${at}{}`, depth + 1);
      }
    };
    const names = Object.keys((document["components"] as { schemas: Json }).schemas);
    for (const name of names) {
      const type = component(real, name);
      expect((type.flags & (ts.TypeFlags.Unknown | ts.TypeFlags.Any)) === 0, name).toBe(true);
      walk(type, name, 0);
    }
    expect(untyped.join("\n")).toEqual("");
    expect(names.length).toBeGreaterThan(100);
    expect(seen.size).toBeGreaterThan(names.length);
  });

  it("types the properties the conjuncts would have erased, by the checker on the resolved property", () => {
    const { checker } = real;
    const show = (type: ts.Type): string => checker.typeToString(type, undefined, ts.TypeFormatFlags.NoTruncation);
    expect(show(property(real, component(real, "Analysed"), "not_estimable"))).toBe('{ readonly [key: string]: "suppressed"; } | undefined');
    expect(show(property(real, component(real, "AnalysedVariable"), "not_estimable"))).toBe('{ readonly [key: string]: "suppressed"; } | undefined');
    const code = property(real, component(real, "Caveat"), "code");
    expect((code.flags & ts.TypeFlags.StringLike) !== 0 || (code.isUnion() && code.types.every((member) => (member.flags & ts.TypeFlags.StringLike) !== 0))).toBe(true);
    const leaf = component(real, "ValueLeaf");
    expect(leaf.getProperties().map((symbol) => symbol.name)).toEqual(expect.arrayContaining(["column", "op"]));
  });

  it("types every recursive component of the document (its $ref cycles, read from it)", () => {
    const schemas = (document["components"] as { schemas: Record<string, unknown> }).schemas;
    const edges = new Map(Object.entries(schemas).map(([name, schema]) => [name, [...JSON.stringify(schema).matchAll(/"#\/components\/schemas\/([^"]+)"/gu)].map((found) => found[1] ?? "")]));
    const reaches = (from: string, to: string): boolean => {
      const seen = new Set<string>();
      const stack = [...(edges.get(from) ?? [])];
      while (stack.length > 0) {
        const next = stack.pop() ?? "";
        if (next === to) {
          return true;
        }
        if (!seen.has(next)) {
          seen.add(next);
          stack.push(...(edges.get(next) ?? []));
        }
      }
      return false;
    };
    const json = new Set([...census(document).json.keys()].map((at) => at.replace("#/components/schemas/", "")));
    const recursive = Object.keys(schemas).filter((name) => reaches(name, name) && !json.has(name));
    expect(recursive.length).toBeGreaterThanOrEqual(4);
    for (const name of recursive) {
      const type = component(real, name);
      expect((type.flags & (ts.TypeFlags.Unknown | ts.TypeFlags.Any)) === 0, name).toBe(true);
      expect(type.getProperties().length > 0 || type.isUnion(), name).toBe(true);
    }
  });
});

const MARK = "@@server-number@@";

/** An envelope as a TS literal, every number `n` (or `number`, given). */
function literal(value: unknown, number = "n"): string {
  return JSON.stringify(value, (_key, member: unknown) => (typeof member === "number" ? MARK : member)).replaceAll(`"${MARK}"`, number);
}

describe("(c) the golden envelopes are ResultEnvelope literals", () => {
  const envelopes = JSON.parse(readFileSync(path.join(WEB, "tests/fixtures/envelopes.json"), "utf8")) as Json;
  const entries = Object.entries(envelopes);
  let checked: Checked;

  beforeAll(() => {
    const declare = (mutate: (envelope: Json) => string): string =>
      `${HEAD}${entries.map(([, envelope], index) => `export const e${String(index)}: S["ResultEnvelope"] = ${mutate(envelope as Json)};`).join("\n")}\n`;
    checked = check({
      "src/probe-envelopes.ts": declare((envelope) => literal(envelope)),
      "src/probe-extra.ts": declare((envelope) => literal({ ...envelope, aibi_extra: "x" })),
      "src/probe-text.ts": declare((envelope) => literal(envelope, '"1"')),
      "src/probe-plain.ts": declare((envelope) => literal(envelope, "1")),
    });
  }, 120_000);

  it("are the 19 golden envelopes", () => {
    expect(entries).toHaveLength(19);
  });

  it("all compile, every number a ServerNumber", () => {
    expect(checked.codes("src/probe-envelopes.ts")).toEqual([]);
  });

  it("each refuses an extra member (TS2353)", () => {
    expect(checked.codes("src/probe-extra.ts")).toEqual(entries.map(() => 2353));
  });

  it("each refuses its numbers written as text, or as JS numbers (TS2322)", () => {
    for (const file of ["src/probe-text.ts", "src/probe-plain.ts"]) {
      const source = checked.source(file);
      const found = checked.diagnostics(file).filter((diagnostic) => diagnostic.code !== 6196);
      expect(new Set(found.map((diagnostic) => diagnostic.code))).toEqual(new Set([2322]));
      const declarations = new Set(found.map((diagnostic) => source.getLineAndCharacterOfPosition(diagnostic.start ?? 0).line));
      expect(declarations.size, file).toBe(entries.length);
    }
  });
});

/** A document of one response and one request, the given components besides. */
function synthetic(schemas: Json, response: string, request?: string): Json {
  const ok = { description: "ok", content: { "application/json": { schema: { $ref: `#/components/schemas/${response}` } } } };
  return {
    openapi: "3.1.0",
    info: { title: "t", version: "1" },
    paths: {
      "/api/x": {
        post: {
          operationId: "x",
          ...(request === undefined ? {} : { requestBody: { content: { "application/json": { schema: { $ref: `#/components/schemas/${request}` } } } } }),
          responses: { "200": ok },
        },
      },
    },
    components: { schemas },
  };
}

const num = (extra: Json = {}): Json => ({ type: "integer", "x-aibi-server-number": true, ...extra });

/** The keyword corpus: a component, an instance it accepts and instances it refuses. */
const KEYWORDS: readonly { readonly name: string; readonly schema: Json; readonly accepted: readonly string[]; readonly refused: readonly string[] }[] = [
  { name: "a required marked integer", schema: { type: "object", properties: { a: num() }, required: ["a"] }, accepted: ["{ a: n }"], refused: ["{ a: 1 }", "{}"] },
  { name: "a marked integer or null", schema: { type: "object", properties: { a: { anyOf: [num(), { type: "null" }] } }, required: ["a"] }, accepted: ["{ a: n }", "{ a: null }"], refused: ["{ a: 1 }", "{ a: 'n' }"] },
  { name: "a type list with null", schema: { type: "object", properties: { a: { type: ["string", "null"] } }, required: ["a"] }, accepted: ["{ a: 'x' }", "{ a: null }"], refused: ["{ a: 1 }"] },
  { name: "additionalProperties of marked numbers", schema: { type: "object", additionalProperties: { type: "number", "x-aibi-server-number": true } }, accepted: ["{ k: n }", "{}"], refused: ["{ k: 1 }"] },
  { name: "additionalProperties false", schema: { type: "object", properties: { a: { type: "string" } }, additionalProperties: false }, accepted: ["{ a: 'x' }", "{}"], refused: ["{ a: 'x', b: 1 }"] },
  { name: "a string const", schema: { type: "object", properties: { a: { const: "x" } }, required: ["a"] }, accepted: ["{ a: 'x' }"], refused: ["{ a: 'y' }"] },
  { name: "a string enum", schema: { type: "object", properties: { a: { enum: ["p", "q"], type: "string" } }, required: ["a"] }, accepted: ["{ a: 'q' }"], refused: ["{ a: 'r' }"] },
  { name: "an array of marked integers", schema: { type: "array", items: num() }, accepted: ["[n, m]", "[]"], refused: ["[1]", "[n, 'x']"] },
  {
    name: "numeric consts under an allOf conjunct's prefixItems",
    schema: { type: "array", items: { $ref: "#/components/schemas/Ref" }, allOf: [{ prefixItems: [{ properties: { p: { const: 0 } } }] }] },
    accepted: ["[{ p: n }]"],
    refused: ["[{ p: 0 }]"],
  },
  { name: "an optional member with a default", schema: { type: "object", properties: { a: { type: "string", default: "x" } } }, accepted: ["{}", "{ a: 'y' }"], refused: ["{ a: null }"] },
  {
    name: "allOf of a reference and own members",
    schema: { type: "object", allOf: [{ $ref: "#/components/schemas/Ref" }], properties: { b: { type: "string" } }, required: ["b"] },
    accepted: ["{ p: n, b: 'x' }"],
    refused: ["{ b: 'x' }", "{ p: n }"],
  },
  {
    name: "oneOf of two objects",
    schema: { oneOf: [{ type: "object", properties: { a: { type: "string" } }, required: ["a"] }, { type: "object", properties: { b: num() }, required: ["b"] }] },
    accepted: ["{ a: 'x' }", "{ b: n }"],
    refused: ["{ b: 1 }", "{ c: 1 }"],
  },
  { name: "patternProperties", schema: { type: "object", patternProperties: { "^x": num() } }, accepted: ["{ x1: n }"], refused: ["{ x1: 1 }"] },
  {
    name: "a response JSON value",
    schema: { anyOf: [{ type: ["string", "boolean", "null"] }, { type: "number" }], "x-aibi-json": { direction: "response", null: true } },
    accepted: ["n", "null", "'x'", "{ a: [n, null, 'x', { b: true }] }"],
    refused: ["1", "{ a: [1] }"],
  },
];

describe("(d) the keyword combinations render faithfully, and the transform is needed", () => {
  let checked: Checked;
  const files: Record<string, string> = {};

  beforeAll(async () => {
    const schemas: Json = { Ref: { type: "object", properties: { p: num() }, required: ["p"] } };
    KEYWORDS.forEach((entry, index) => {
      schemas[`K${String(index)}`] = entry.schema;
    });
    const holder: Json = { type: "object", properties: Object.fromEntries(KEYWORDS.map((_, index) => [`k${String(index)}`, { $ref: `#/components/schemas/K${String(index)}` }])) };
    schemas["Holder"] = holder;
    schemas["Asked"] = { type: "object", properties: { offset: { type: "integer", minimum: 0 } } };
    files["src/api/generated/probe-keywords.ts"] = await renderTypes(synthetic(schemas, "Holder", "Asked"));
    const head = `import type { components } from "./probe-keywords";\nimport type { ServerNumber } from "../box";\ntype S = components["schemas"];\ndeclare const n: ServerNumber;\ndeclare const m: ServerNumber;\n`;
    KEYWORDS.forEach((entry, index) => {
      files[`src/api/generated/probe-k${String(index)}-ok.ts`] = `${head}${entry.accepted.map((given, at) => `export const a${String(at)}: S["K${String(index)}"] = ${given};`).join("\n")}\n`;
      entry.refused.forEach((given, at) => {
        files[`src/api/generated/probe-k${String(index)}-no${String(at)}.ts`] = `${head}export const r: S["K${String(index)}"] = ${given};\n`;
      });
    });
    files["src/api/generated/probe-asked.ts"] = `${head}export const a: S["Asked"] = { offset: 50 };\nexport const r: S["Asked"] = { offset: n };\n`;
    checked = check(files);
  }, 120_000);

  it.each(KEYWORDS.map((entry, index) => [entry.name, entry, index] as const))("%s", (_name, entry, index) => {
    expect(checked.codes(`src/api/generated/probe-k${String(index)}-ok.ts`)).toEqual([]);
    entry.refused.forEach((_given, at) => {
      expect(checked.codes(`src/api/generated/probe-k${String(index)}-no${String(at)}.ts`).length, `refused ${String(at)}`).toBeGreaterThan(0);
    });
  });

  it("leaves a request integer a JS number, which takes no ServerNumber", () => {
    expect(checked.codes("src/api/generated/probe-asked.ts")).toEqual([2322]);
  });

  it("renders each x-aibi-json component, without the transform, as TS2502 and nothing else", async () => {
    const plain = await renderTypes(document, { marks: false });
    expect(plain).not.toContain("ServerNumber");
    const without = check({ "src/api/generated/probe-plain.ts": plain });
    const found = without.diagnostics("src/api/generated/probe-plain.ts");
    const names = [...census(document).json.keys()].map((at) => at.replace("#/components/schemas/", "")).sort();
    expect(names).toHaveLength(6);
    expect(found.map((diagnostic) => diagnostic.code)).toEqual(names.map(() => 2502));
    expect(found.map((diagnostic) => /'([^']+)'/u.exec(ts.flattenDiagnosticMessageText(diagnostic.messageText, " "))?.[1]).sort()).toEqual(names);
  }, 120_000);
});

describe("(e) every mark reached, no numeric literal left", () => {
  it("renders a ServerNumber for each mark of the document's census", () => {
    const file = real.source(GENERATED);
    let references = 0;
    let numericLiterals = 0;
    const visit = (node: ts.Node): void => {
      if (ts.isTypeReferenceNode(node) && node.typeName.getText(file) === "ServerNumber") {
        references += 1;
      }
      if (ts.isLiteralTypeNode(node) && (ts.isNumericLiteral(node.literal) || ts.isPrefixUnaryExpression(node.literal) || ts.isBigIntLiteral(node.literal))) {
        numericLiterals += 1;
      }
      ts.forEachChild(node, visit);
    };
    visit(file);
    const marks = census(document).numbers.size;
    expect(marks).toBeGreaterThan(0);
    expect(references).toBe(marks);
    expect(numericLiterals).toBe(0);
  });

  it("erases the document's numeric consts, which it does hold", () => {
    const consts: string[] = [];
    const walk = (node: unknown, at: string): void => {
      if (Array.isArray(node)) {
        node.forEach((item, index) => {
          walk(item, `${at}/${String(index)}`);
        });
      } else if (typeof node === "object" && node !== null) {
        const object = node as Json;
        if (typeof object["const"] === "number" || (Array.isArray(object["enum"]) && object["enum"].some((value) => typeof value === "number"))) {
          consts.push(at);
        }
        Object.entries(object).forEach(([key, value]) => {
          walk(value, `${at}/${key}`);
        });
      }
    };
    walk(document, "#");
    expect(consts.length).toBeGreaterThan(0);
    expect(consts.every((at) => at.includes("/allOf/") && at.includes("/prefixItems/"))).toBe(true);
  });

  it("types ResultEnvelope['cohorts'][number]['position'] as a ServerNumber, at the instance location", () => {
    const probe = check({ "src/probe-position.ts": `${HEAD}export type P = S["ResultEnvelope"]["cohorts"][number]["position"];\nexport const p: P = n;\nexport const q: P = 0;\n` });
    expect(probe.codes("src/probe-position.ts")).toEqual([2322]);
    const alias = probe.source("src/probe-position.ts").statements.find((statement) => ts.isTypeAliasDeclaration(statement) && statement.name.text === "P");
    expect(alias === undefined ? "" : probe.checker.typeToString(probe.checker.getTypeAtLocation(alias))).toBe("ServerNumber");
  });

  it("fails the generation for a mark the renderer never reaches (under a conjunct's prefixItems)", async () => {
    const lost = synthetic({ L: { type: "array", items: { type: "string" }, allOf: [{ prefixItems: [num()] }] } }, "L");
    await expect(renderTypes(lost)).rejects.toThrow(/reached no x-aibi-server-number at .*prefixItems/u);
  });
});

describe("(f) request and response numbers", () => {
  it("compiles a JS number into a request integer, and refuses a ServerNumber anywhere it is not one", () => {
    const probe = check({
      "src/probe-ok.ts": `${HEAD}export const a: S["SearchCatalog"] = { offset: 50, limit: 5 };\nexport const b: JsonOut = n;\n`,
      "src/probe-offset.ts": `${HEAD}export const a: S["SearchCatalog"] = { offset: hits.next_offset };\n`,
      "src/probe-number.ts": `${HEAD}export const a: number = n;\n`,
      "src/probe-string.ts": `${HEAD}export const a: string = n;\n`,
      "src/probe-in.ts": `${HEAD}export const a: JsonIn = n;\n`,
      "src/probe-in-null.ts": `${HEAD}export const a: JsonInNull = { k: [n] };\n`,
      "src/probe-out-in.ts": `${HEAD}declare const o: JsonOut;\nexport const a: JsonInNull = o;\n`,
      "src/probe-minus.ts": `${HEAD}export const a = n - m;\n`,
    });
    expect(probe.codes("src/probe-ok.ts")).toEqual([]);
    expect([[2322], [2412]]).toContainEqual(probe.codes("src/probe-offset.ts"));
    for (const file of ["number", "string", "in", "in-null", "out-in"]) {
      expect(probe.codes(`src/probe-${file}.ts`), file).toEqual([2322]);
    }
    expect(probe.codes("src/probe-minus.ts").sort()).toEqual([2362, 2363]);
  });
});

/** The `operations` interface of a checked program's generated file. */
function operationsOf(checked: Checked): ts.Type {
  const { checker } = checked;
  const file = checked.source(GENERATED);
  const symbol = checker.getSymbolAtLocation(file);
  const found = symbol === undefined ? undefined : checker.getExportsOfModule(symbol).find((member) => member.name === "operations");
  if (found === undefined) {
    throw new Error("no operations");
  }
  return checker.getDeclaredTypeOfSymbol(found);
}

/** Where a JS number (`number`, a numeric literal, a numeric enum) is reachable from `type`, by the
 * names of the properties on the way (`[]` for an element, `{}` for an index signature). */
function numbersIn(checked: Checked, type: ts.Type, at: string, seen: Set<ts.Type>, found: string[]): void {
  const { checker } = checked;
  if ((type.flags & ts.TypeFlags.NumberLike) !== 0) {
    found.push(at);
    return;
  }
  if (type.isUnionOrIntersection()) {
    type.types.forEach((member) => {
      numbersIn(checked, member, at, seen, found);
    });
    return;
  }
  // Only an object type can hold the walk in a cycle (the recursive components): it is walked once.
  if ((type.flags & ts.TypeFlags.Object) === 0 || seen.has(type)) {
    return;
  }
  seen.add(type);
  if (checker.isArrayType(type) || checker.isTupleType(type)) {
    checker.getTypeArguments(type as ts.TypeReference).forEach((member) => {
      numbersIn(checked, member, `${at}[]`, seen, found);
    });
    return;
  }
  for (const symbol of checker.getPropertiesOfType(type)) {
    numbersIn(checked, checker.getTypeOfSymbol(symbol), `${at}.${symbol.name}`, seen, found);
  }
  for (const info of checker.getIndexInfosOfType(type)) {
    numbersIn(checked, info.type, `${at}{}`, seen, found);
  }
}

/** The JS numbers reachable from one side of every operation: `responses`, or `requestBody` and
 * `parameters`. */
function numbersOfSide(checked: Checked, side: readonly string[]): string[] {
  const { checker } = checked;
  const found: string[] = [];
  for (const operation of checker.getPropertiesOfType(operationsOf(checked))) {
    const type = checker.getTypeOfSymbol(operation);
    for (const name of side) {
      const member = type.getProperty(name);
      if (member !== undefined) {
        // A seen-set for each position: a component is walked wherever it is reached.
        numbersIn(checked, checker.getTypeOfSymbol(member), `${operation.name}.${name}`, new Set(), found);
      }
    }
  }
  return found;
}

describe("(g) no JS number is reachable from a response, whatever else the position holds", () => {
  const operations = (): number => real.checker.getPropertiesOfType(operationsOf(real)).length;

  it("holds on the real generated types: every response number is a ServerNumber", () => {
    expect(operations()).toBeGreaterThan(30);
    expect(numbersOfSide(real, ["responses"])).toEqual([]);
  });

  it("finds the request integers, so that the walk sees a number where one is (the control)", () => {
    const found = numbersOfSide(real, ["requestBody", "parameters"]);
    expect(found.length).toBeGreaterThan(20);
    expect(found).toContainEqual(expect.stringMatching(/^search_catalog\.requestBody.*\.offset$/u));
    expect(found).toContainEqual(expect.stringMatching(/^search_catalog\.requestBody.*\.limit$/u));
    expect(found).toContainEqual(expect.stringMatching(/^reject\.parameters.*\.proposal$/u));
    expect(found).toContainEqual(expect.stringMatching(/^queue\.parameters.*\.release$/u));
  });

  const original = readFileSync(path.join(WEB, GENERATED), "utf8");

  it.each([
    ["a position typed number | ServerNumber", "readonly next_offset?: ServerNumber;", "readonly next_offset?: number | ServerNumber;", /next_offset/u],
    ["a position typed number", "readonly next_offset?: ServerNumber;", "readonly next_offset?: number;", /next_offset/u],
    ["a position typed with a numeric literal too", "readonly next_offset?: ServerNumber;", "readonly next_offset?: ServerNumber | 0;", /next_offset/u],
    ["an index signature typed number", "readonly [key: string]: ServerNumber;", "readonly [key: string]: number;", /excluded\{\}/u],
    ["an index signature typed number | ServerNumber", "readonly [key: string]: ServerNumber;", "readonly [key: string]: number | ServerNumber;", /excluded\{\}/u],
    ["a position typed number in an array", "readonly next_offset?: ServerNumber;", "readonly next_offset?: (ServerNumber | number)[];", /next_offset/u],
  ])("fails for %s, which (a), (c) and (e) alone may pass", (_case, from, to, where) => {
    expect(original).toContain(from);
    const mutated = check({ "src/probe-mutant.ts": `export * from "./api";\n`, [GENERATED]: original.replace(from, to) });
    expect(numbersOfSide(mutated, ["responses"]).some((at) => where.test(at))).toBe(true);
  });
});

describe("the generator's refusals", () => {
  it.each([
    ["a mark on a string", { components: { schemas: { A: { type: "string", "x-aibi-server-number": true } } } }, /only true, on a plain number/u],
    ["a mark that is not true", { components: { schemas: { A: { type: "integer", "x-aibi-server-number": 1 } } } }, /only true/u],
    ["a mark on a type list", { components: { schemas: { A: { type: ["integer", "null"], "x-aibi-server-number": true } } } }, /only true/u],
    ["x-aibi-json below a component's top level", { components: { schemas: { A: { properties: { b: { "x-aibi-json": { direction: "request", null: true } } } } } } }, /top level alone/u],
    ["x-aibi-json of no hand-written type", { components: { schemas: { A: { "x-aibi-json": { direction: "response", null: false } } } } }, /no hand-written type/u],
    ["x-aibi-json with another member", { components: { schemas: { A: { "x-aibi-json": { direction: "request", null: true, extra: 1 } } } } }, /exactly \{direction, null\}/u],
    ["x-aibi-json whose null is not a boolean", { components: { schemas: { A: { "x-aibi-json": { direction: "request", null: "true" } } } } }, /exactly \{direction, null\}/u],
  ])("refuses %s", (_case, given, words) => {
    expect(() => census(given)).toThrow(GenerationError);
    expect(() => census(given)).toThrow(words);
  });

  it.each(["/api/../x", "/api/./x", "/api//x", "/api/a{b}", "/api/{a}b", "api/x", "/api/x/"])("refuses the template %s", (template) => {
    expect(() => templateParts(template)).toThrow(GenerationError);
  });

  it("percent-encodes a literal segment", () => {
    expect(templateParts("/api/a b/{c}")).toEqual([{ literal: "api" }, { literal: "a%20b" }, { parameter: "c" }]);
  });

  const route = (template: string, method: string, operation: Json): Json => ({ paths: { [template]: { [method]: operation } } });

  it.each([
    ["a method other than GET and POST", route("/api/x", "put", { operationId: "x" }), /not GET or POST/u],
    ["a reserved word", route("/api/x", "post", { operationId: "delete" }), /no plain name/u],
    ["an id that is no plain name", route("/api/x", "post", { operationId: "x-y" }), /no plain name/u],
    ["a missing id", route("/api/x", "post", {}), /no plain name/u],
    ["a path parameter the template does not name", route("/api/x", "get", { operationId: "x", parameters: [{ in: "path", name: "a" }] }), /path parameters/u],
    ["a template parameter the operation does not have", route("/api/{a}", "get", { operationId: "x" }), /path parameters/u],
    ["a query parameter that is no plain name", route("/api/x", "get", { operationId: "x", parameters: [{ in: "query", name: "a-b" }] }), /no plain name/u],
  ])("refuses %s", (_case, given, words) => {
    expect(() => renderRoutes(given)).toThrow(words);
  });

  it("refuses an operation id twice", () => {
    expect(() => renderRoutes({ paths: { "/api/a": { get: { operationId: "x" } }, "/api/b": { get: { operationId: "x" } } } })).toThrow(/twice/u);
  });
});

describe("the generated files are current", () => {
  it("--check passes on the committed tree, and the generation is deterministic", async () => {
    const said: string[] = [];
    expect(await main(["--check"], (line) => said.push(line))).toBe(0);
    expect(said).toEqual([]);
    const again = await generate(document);
    expect(again.openapi).toBe(readFileSync(OUTPUTS.openapi, "utf8"));
    expect(again.routes).toBe(readFileSync(OUTPUTS.routes, "utf8"));
  });

  it("--check fails, writing nothing, while a file differs from the document; a run writes it", async () => {
    const scratch = mkdtempSync(path.join(os.tmpdir(), "aibi-generate-"));
    try {
      const changed = structuredClone(document);
      const paths = changed["paths"] as Json;
      paths["/api/extra"] = { get: { operationId: "extra", responses: {} } };
      const files = {
        document: path.join(scratch, "openapi.json"),
        outputs: { openapi: path.join(scratch, "openapi.ts"), routes: path.join(scratch, "routes.ts") },
      };
      writeFileSync(files.document, JSON.stringify(changed));
      writeFileSync(files.outputs.openapi, readFileSync(OUTPUTS.openapi, "utf8"));
      writeFileSync(files.outputs.routes, readFileSync(OUTPUTS.routes, "utf8"));
      const said: string[] = [];
      expect(await main(["--check"], (line) => said.push(line), files)).toBe(1);
      expect(said).toEqual([expect.stringContaining("openapi.ts is stale"), expect.stringContaining("routes.ts is stale")]);
      expect(readFileSync(files.outputs.routes, "utf8")).toBe(readFileSync(OUTPUTS.routes, "utf8"));
      expect(await main([], () => undefined, files)).toBe(0);
      expect(readFileSync(files.outputs.routes, "utf8")).toContain("export function extra(): Route<\"extra\">");
      expect(await main(["--check"], () => undefined, files)).toBe(0);
    } finally {
      rmSync(scratch, { recursive: true, force: true });
    }
  }, 120_000);

  it("exits 2 on a usage error", async () => {
    expect(await main(["--write"], () => undefined)).toBe(2);
    expect(await main(["--check", "x"], () => undefined)).toBe(2);
  });
});
