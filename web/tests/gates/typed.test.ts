/**
 * The typed answers' gates over the TypeScript program and the lint (D426):
 *
 * - **`Boxed` holds no raw string** (the compile-time class test): every component of the
 *   generated types, boxed, gives nothing `takers.ts`' `gives` counts (a `string`, `any`,
 *   `unknown`, `object`); and its segments are exactly the two arms (`TextSegment`, `DataSegment`
 *   alone become a `BoxedSegment`).
 * - **The typed functions** each take their operation's `RequestBody` and give
 *   `Promise<Outcome<Boxed<component>>>` (`satisfies`, compiled), and a box is no request string.
 * - **A raw string is no server text** (type level): a literal, a `string`, an object of the box's
 *   members, a `ServerNumber` and a `Choice` do not compile where a `ServerText` is required, and an
 *   `any` there is the lint's (`no-unsafe-argument`).
 * - **The gates see what they exist for** (their controls): an export that gives a raw string or an
 *   `unknown` (a door, `gives`), and a function that takes a box (`takesText`).
 * - **One document**: the table's `DOCUMENT_DIGEST` is `schemas/openapi.json`'s SHA-256, so the
 *   types and the table (each held fresh by `generate:check`) are one document's.
 */
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import path from "node:path";

import ts from "typescript";
import { beforeAll, describe, expect, it } from "vitest";

import { DOCUMENT_DIGEST, type Shape, SHAPES } from "../../src/api/generated/shapes";
import { Linter } from "./linter";
import { check, type Checked, WEB } from "./program";
import { conformance, gives, rawKeyed, takesText } from "./takers";

const eslint = new Linter({
  cwd: WEB,
  overrideConfig: { languageOptions: { parserOptions: { projectService: { allowDefaultProject: ["src/*.tsx"], defaultProject: "tsconfig.app.json" } } } },
});

async function lintIds(code: string, file: string): Promise<string[]> {
  const [result] = await eslint.lintText(code, { filePath: path.join(WEB, file) });
  const messages = result?.messages ?? [];
  expect(messages.filter((message) => message.fatal === true).map((message) => message.message)).toEqual([]);
  return messages.map((message) => message.ruleId ?? "inline-configuration");
}

/** The component names of the generated types. */
function componentNames(checked: Checked): string[] {
  const source = checked.source("src/api/generated/openapi.ts");
  const module = checked.checker.getSymbolAtLocation(source);
  const components = module === undefined ? undefined : checked.checker.getExportsOfModule(module).find((symbol) => symbol.name === "components");
  if (components === undefined) {
    throw new Error("no components");
  }
  const schemas = checked.checker.getPropertyOfType(checked.checker.getDeclaredTypeOfSymbol(components), "schemas");
  return schemas === undefined ? [] : checked.checker.getPropertiesOfType(checked.checker.getTypeOfSymbol(schemas)).map((symbol) => symbol.name);
}

/** The declared type of a probe's export. */
function exported(checked: Checked, file: string, name: string): ts.Type {
  const module = checked.checker.getSymbolAtLocation(checked.source(file));
  const symbol = module === undefined ? undefined : checked.checker.getExportsOfModule(module).find((one) => one.name === name);
  if (symbol === undefined) {
    throw new Error(`${file} exports no ${name}`);
  }
  return checked.checker.getDeclaredTypeOfSymbol(symbol);
}

describe("Boxed holds no raw string, at every component (the compile-time class test)", () => {
  let checked: Checked;
  let names: string[];
  const PROBE = "src/probe-boxed.ts";

  beforeAll(() => {
    const first = check({ "src/probe-names.ts": 'export type { components } from "./api";\n' });
    names = componentNames(first);
    checked = check({
      [PROBE]: `import type { Boxed, BoxedMap, BoxedSegment, components, ServerNumber, ServerText } from "./api";
type S = components["schemas"];
${names.map((name, index) => `export type B${String(index)} = Boxed<S[${JSON.stringify(name)}]>;\nexport type G${String(index)} = [B${String(index)}] extends [BoxedSegment] ? true : false;`).join("\n")}
export type Raw = Boxed<string> | { readonly label: string };
export type Plain = { readonly byKey: { readonly [key: string]: string } };
export type Mapped = { readonly byKey: BoxedMap<number> };
export type Near = [Boxed<{ readonly text: string; readonly more?: string }>] extends [BoxedSegment] ? true : false;
export type NearData = [Boxed<{ readonly data: string }>] extends [BoxedSegment] ? true : false;
export type ConfGood = { readonly a: ServerText; readonly b?: BoxedMap<ServerNumber>; readonly c: readonly ServerText[]; readonly d: ServerText | null; readonly e: BoxedSegment };
export type ConfMapAsRecord = { readonly NOT_ASSESSED?: ServerNumber };
export type ConfRaw = { readonly a: string; readonly b?: BoxedMap<ServerNumber>; readonly c: readonly ServerText[]; readonly d: ServerText | null; readonly e: BoxedSegment };
export type ConfRequired = { readonly a: ServerText; readonly b: BoxedMap<ServerNumber>; readonly c: readonly ServerText[]; readonly d: ServerText | null; readonly e: BoxedSegment };
export type ConfOptional = { readonly a?: ServerText; readonly b?: BoxedMap<ServerNumber>; readonly c: readonly ServerText[]; readonly d: ServerText | null; readonly e: BoxedSegment };
export type ConfExtra = ConfGood & { readonly z: ServerText };
export type ConfMissing = { readonly a: ServerText; readonly b?: BoxedMap<ServerNumber>; readonly c: readonly ServerText[]; readonly e: BoxedSegment };
export type ConfNotNull = { readonly a: ServerText; readonly b?: BoxedMap<ServerNumber>; readonly c: readonly ServerText[]; readonly d: ServerText; readonly e: BoxedSegment };
export type ConfListOfNumbers = { readonly a: ServerText; readonly b?: BoxedMap<ServerNumber>; readonly c: readonly ServerNumber[]; readonly d: ServerText | null; readonly e: BoxedSegment };
export type ConfMapOfText = { readonly a: ServerText; readonly b?: BoxedMap<ServerText>; readonly c: readonly ServerText[]; readonly d: ServerText | null; readonly e: BoxedSegment };
`,
    });
  }, 120_000);

  it("compiles, over every component of the document", () => {
    expect(names.length).toBeGreaterThan(150);
    expect(checked.codes(PROBE)).toEqual([]);
  });

  it("no component, boxed, gives a string, any, unknown or object", () => {
    const giving = gives(checked.program, checked.checker);
    const found = names.flatMap((name, index) => giving(exported(checked, PROBE, `B${String(index)}`), name));
    expect(found).toEqual([]);
  });

  it("no component, boxed, holds a map of raw keys: every data-keyed map is a BoxedMap, every BoxedJson object too (m5, G16, G17)", () => {
    const keyed = rawKeyed(checked.checker);
    expect(names.flatMap((name, index) => keyed(exported(checked, PROBE, `B${String(index)}`), name))).toEqual([]);
    // The gate's controls: a record of raw keys is seen, nested too, and a BoxedMap is not.
    expect(keyed(exported(checked, PROBE, "Plain"), "Plain")).toEqual(["Plain.byKey[key]: a raw key"]);
    expect(keyed(exported(checked, PROBE, "Mapped"), "Mapped")).toEqual([]);
    const json = names.findIndex((name) => name === "OutputJson");
    expect(json).toBeGreaterThanOrEqual(0);
    expect(checked.checker.typeToString(exported(checked, PROBE, `B${String(json)}`))).toBe("BoxedJson");
  });

  it("the table and the types conform: each component's boxed type has the kinds, members and optionality of its table node (m2)", () => {
    const conforms = conformance(checked.checker, SHAPES);
    expect(Object.keys(SHAPES).filter((name) => !names.includes(name))).toEqual([]);
    const found = names.flatMap((name, index) => {
      const shape = SHAPES[name];
      return shape === undefined ? [] : conforms(exported(checked, PROBE, `B${String(index)}`), shape, name);
    });
    expect(found).toEqual([]);
    // Most of the table's components are checked (those no answer reaches are no part of it).
    expect(Object.keys(SHAPES).length).toBeGreaterThan(50);
  });

  it("the conformance gate sees what it exists for (its controls): a map as named members, a raw string, a member's optionality, a member more or fewer, a null lost, a list's or a map's items of another kind", () => {
    const conforms = conformance(checked.checker, {});
    const table: Shape = {
      t: "rec",
      p: { a: { t: "str" }, b: { t: "map", v: { t: "num" } }, c: { t: "arr", i: { t: "enum", v: ["x"] } }, d: { t: "or", o: [{ t: "str" }, { t: "null" }] }, e: { t: "seg" } },
      r: ["a", "c", "d", "e"],
    };
    const against = (name: string, shape: Shape = table): string[] => conforms(exported(checked, PROBE, name), shape, "T");
    expect(against("ConfGood")).toEqual([]);
    // The review's case: an enum-keyed map typed as named optional members (it compiles `x.NO_ROWS`).
    expect(against("ConfMapAsRecord", { t: "map", v: { t: "num" }, r: ["NOT_ASSESSED"], k: { t: "enum", v: ["NOT_ASSESSED"] } })).toEqual(["T: the table has [map], the type [record]"]);
    expect(against("ConfRaw")).toEqual(["T.a: the table has [text], the type [rawstring]"]);
    expect(against("ConfRequired")).toEqual(["T.b: the table does not require it, the type requires it"]);
    expect(against("ConfOptional")).toEqual(["T.a: the table requires it, the type makes it optional"]);
    expect(against("ConfExtra").join("|")).toMatch(/no record of the members \[a,b,c,d,e\] in the type \(it has \[a,b,c,d,e,z\]\)/u);
    expect(against("ConfMissing").join("|")).toMatch(/no record of the members \[a,b,c,d,e\]/u);
    expect(against("ConfNotNull")).toEqual(["T.d: the table has [null, text], the type [text]"]);
    expect(against("ConfListOfNumbers")).toEqual(["T.c[]: the table has [text], the type [number]"]);
    expect(against("ConfMapOfText")).toEqual(["T.b{}: the table has [number], the type [text]"]);
  });

  it("the segments are exactly TextSegment and DataSegment, boxed", () => {
    const segments = names.filter((_name, index) => checked.checker.typeToString(exported(checked, PROBE, `G${String(index)}`)) === "true");
    expect(segments.sort()).toEqual(["DataSegment", "TextSegment"]);
    // A record with a segment's member and an optional one more, or `Data`'s one member, is no
    // segment: both are assignable to a segment's arm, so the rule is the exact key set.
    expect(checked.checker.typeToString(exported(checked, PROBE, "Near"))).toBe("false");
    expect(checked.checker.typeToString(exported(checked, PROBE, "NearData"))).toBe("false");
  });

  it("the check sees a raw string (its control)", () => {
    expect(gives(checked.program, checked.checker)(exported(checked, PROBE, "Raw"), "Raw")).toEqual(["Raw.label: string"]);
  });
});

describe("the typed functions: a request body of the generated types in, a boxed outcome out", () => {
  it("each gives its operation's Boxed component, and takes its RequestBody (compiled)", () => {
    // The three that take a document are typed.ts's alone (D432): the probe imports them there.
    const code = `import { describeDataset, explain, listAnalyses, searchCatalog, type Boxed, type Outcome, type components } from "./api";
import { countCohort, runAnalysis, validateDocument } from "./api/typed";
type S = components["schemas"];
export const all = async () => [
  (await searchCatalog({ limit: 5 })) satisfies Outcome<Boxed<S["CatalogHits"]>>,
  (await describeDataset({ dataset: "d" })) satisfies Outcome<Boxed<S["DatasetDescription"]>>,
  (await listAnalyses({})) satisfies Outcome<Boxed<S["AnalysisListing"]>>,
  (await validateDocument({ document: {} })) satisfies Outcome<Boxed<S["DocumentValidation"]>>,
  (await countCohort({ document: { aibi: "1", dataset: "d", unit: "u", cohorts: { a: { all: [] } } } })) satisfies Outcome<Boxed<S["CohortCounts"]>>,
  (await runAnalysis({ document: { aibi: "1", dataset: "d", unit: "u", cohorts: { a: { all: [] } }, views: [] } })) satisfies Outcome<Boxed<S["AnalysisResults"]>>,
  (await explain({ id: "drv:x" })) satisfies Outcome<Boxed<S["Explanation"]>>,
];
`;
    expect(check({ "src/probe-typed.ts": code }).codes("src/probe-typed.ts")).toEqual([]);
  });

  it.each([
    ["a box as a request's dataset", "describeDataset({ dataset: t })", 2322],
    ["a box map as a request's member", "searchCatalog({ concepts: m })", 2740],
    ["a member the request does not have", "searchCatalog({ dataset: 'd' })", 2353],
    ["the answer read as a string", "searchCatalog({}).then((o) => (o.kind === 'ok' ? o.value.hits[0]?.dataset.length : 0))", 2339],
  ])("%s does not compile", (_case, call, code) => {
    const text = `import { describeDataset, searchCatalog, type BoxedMap, type ServerText } from "./api";\ndeclare const t: ServerText;\ndeclare const m: BoxedMap<unknown>;\nexport const f = () => ${call};\n`;
    expect(check({ "src/probe-typed-refused.ts": text }).codes("src/probe-typed-refused.ts")).toContain(code);
  });
});

describe("a raw string is no server text (type level)", () => {
  const HEAD = `import { compareText, idOf, kindOf, sameText, entriesOf, type BoxedMap, type Choice, type ServerNumber, type ServerText } from "./api";
declare const t: ServerText;
declare const s: string;
declare const n: ServerNumber;
declare const c: Choice<"leaf">;
`;

  it.each([
    ["a literal", 'export const f = (): ServerText => "x";'],
    ["a string", "export const f = (): ServerText => s;"],
    ["an object of the box's members", "export const f = (): ServerText => ({ valueOf: () => { throw 0; }, toString: () => { throw 0; }, toLocaleString: () => { throw 0; }, toJSON: () => { throw 0; }, [Symbol.toPrimitive]: () => { throw 0; } });"],
    ["a ServerNumber", "export const f = (): ServerText => n;"],
    ["a Choice", "export const f = (): ServerText => c;"],
    ["a string to sameText", "export const f = () => sameText(s, t);"],
    ["a string to compareText", "export const f = () => compareText(t, s);"],
    ["a string to kindOf", 'export const f = () => kindOf(s, "Severity");'],
    ["a string to idOf", "export const f = () => idOf(s);"],
    ["a vocabulary the table has not", 'export const f = () => kindOf(t, "Mine");'],
    ["a plain object to entriesOf", "export const f = () => entriesOf({ a: 1 });"],
    ["a box as a string", "export const f = (): string => t;"],
    ["a map as a box", "export const f = (m: BoxedMap<unknown>): ServerText => m;"],
  ])("%s does not compile", (_case, line) => {
    const file = "src/probe-text.ts";
    expect(check({ [file]: `${HEAD}${line}\n` }).codes(file).length).toBeGreaterThan(0);
  });

  it("the control: the takers compile with boxes", () => {
    const file = "src/probe-text-control.ts";
    expect(check({ [file]: `${HEAD}export const f = (m: BoxedMap<number>) => [sameText(t, t), compareText(t, t), kindOf(t, "Severity"), idOf(t), entriesOf(m)];\n` }).codes(file)).toEqual([]);
  });

  it("an any where a box is required is the lint's", async () => {
    const code = "import { sameText, type ServerText } from './api';\ndeclare const t: ServerText;\nexport const f = (o: object) => sameText(t, Reflect.get(o, 'k'));\n";
    expect(await lintIds(code, "src/probe.tsx")).toContain("@typescript-eslint/no-unsafe-argument");
  });

  it("a type import past the index is the lint's (m14 of round 4)", async () => {
    expect(await lintIds('export type J = import("./api/json").JsonOut;\n', "src/probe.tsx")).toContain("no-restricted-syntax");
    expect(await lintIds('export type C = typeof import("./api/client");\n', "src/probe.tsx")).toContain("no-restricted-syntax");
    expect(await lintIds('export type C = typeof import("./api");\n', "src/probe.tsx")).not.toContain("no-restricted-syntax");
  });
});

describe("the gates see what they exist for (their controls)", () => {
  let checked: Checked;
  const PROBE = "src/probe-doors.ts";

  beforeAll(() => {
    checked = check({
      [PROBE]: `import type { ServerText, BoxedMap, Boxed, components } from "./api";
export declare function unwalked(): Promise<unknown>;
export declare function label(): { readonly label: string };
export declare function listener(on: (text: string) => void): void;
export declare function peek(text: ServerText): number;
export declare function keys(map: BoxedMap<unknown>): number;
export declare function loose(value: object): number;
export declare function walked(): Promise<Boxed<components["schemas"]["CatalogHits"]>>;
export declare function plain(value: number): boolean;
`,
    });
  }, 120_000);

  const type = (name: string): ts.Type => {
    const module = checked.checker.getSymbolAtLocation(checked.source(PROBE));
    const symbol = module === undefined ? undefined : checked.checker.getExportsOfModule(module).find((one) => one.name === name);
    if (symbol === undefined) {
      throw new Error(`no ${name}`);
    }
    return checked.checker.getTypeOfSymbol(symbol);
  };

  it("gives: an unwalked answer (unknown), a raw member, a listener handed a string; nothing of a walked answer", () => {
    const giving = gives(checked.program, checked.checker);
    expect(giving(type("unwalked"), "unwalked")).toEqual(["unwalked()<0>: unknown"]);
    expect(giving(type("label"), "label")).toEqual(["label().label: string"]);
    expect(giving(type("listener"), "listener")).toEqual(["listener(on(text)): string"]);
    expect(giving(type("walked"), "walked")).toEqual([]);
    expect(giving(type("plain"), "plain")).toEqual([]);
  });

  it("takesText: a box, a map of boxes, an object; not a number", () => {
    const taking = takesText(checked.program, checked.checker);
    const takes = (name: string): boolean => type(name).getCallSignatures().some((signature) => signature.getParameters().some((parameter) => taking(checked.checker.getTypeOfSymbol(parameter))));
    expect([takes("peek"), takes("keys"), takes("loose"), takes("plain")]).toEqual([true, true, true, false]);
  });
});

describe("the table and the types are one document's", () => {
  it("DOCUMENT_DIGEST is schemas/openapi.json's SHA-256", () => {
    expect(DOCUMENT_DIGEST).toBe(createHash("sha256").update(readFileSync(path.join(WEB, "../schemas/openapi.json"), "utf8"), "utf8").digest("hex"));
  });
});
