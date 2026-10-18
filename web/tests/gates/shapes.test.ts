/**
 * The typed answers' table generator (`scripts/generate-shapes.mjs`, D426): the keyword census by
 * position and the refusals that keep the walk honest. Each case changes the real document in one
 * place and expects the generation to fail naming it; the real document generates, and
 * `generate:check` holds the committed table to it (`generated.test.ts`).
 *
 * **The census, by position** (MA1 of the code review's round 1): every node builder declares the
 * keywords it consumes (`CONSUMES`), and a keyword of a schema that its builder did not consume, that
 * is no annotation and no residual fails the generation. The test is derived from the table, not
 * listed: for each builder, every keyword the walk interprets somewhere and the builder does not
 * consume fails at that builder's node (`censusOf`, called directly on a synthetic schema), and every
 * keyword it does consume, every annotation and every residual passes. The residuals are a fixture
 * here (`RESIDUAL_FIXTURE`) that the generator's own list is compared to, so a keyword added to the
 * generator's list, or taken off it, fails here until it is reviewed.
 *
 * **`CONSUMES` is true by construction** (MA3 of round 2): the declarations are held to behaviour.
 * Each builder is handed its schema through a `Proxy` that records the keys read (`watching`), over
 * every node of the real document and over a synthetic node of each builder but `failClosed` (whose
 * one schema, `Severity`, only the real document gives) that carries every keyword the builder declares: at each node the keys read are exactly the keys the node has that
 * its builder declares (both directions), and over all nodes of a builder the keys read are exactly
 * the keys it declares (a declared keyword no node ever makes the builder read, or a keyword read
 * that is not declared, fails). A delegating builder (`DELEGATING`: an `allOf`'s base, a type list's
 * arms) hands the rest on whole to `node`, whose census reads each key. `ANNOTATIONS`, `CONSUMES`
 * and `DELEGATING` are pinned by a reviewed fixture (`tests/fixtures/census.json`) as the residuals
 * are, so a word added to any of them is a change to the fixture, visible in review.
 */
import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { ANNOTATIONS, builderOf, censusOf, CONSUMES, DELEGATING, FAIL_CLOSED, INTERPRETED, RESIDUAL, RESIDUALS, renderShapes, ShapeError, shapesOf, TYPED_OPERATIONS } from "../../scripts/generate-shapes.mjs";
import { OPERATIONS, SHAPES, VOCABULARIES } from "../../src/api/generated/shapes";
import { WEB } from "./program";

type Json = Record<string, unknown>;

const TEXT = readFileSync(path.join(WEB, "../schemas/openapi.json"), "utf8");
const real = (): Json => JSON.parse(TEXT) as Json;

/** A member of a JSON object by a path of keys, an object (or a failure). */
function at(value: unknown, ...path: readonly string[]): Json {
  let found: unknown = value;
  for (const step of path) {
    found = typeof found === "object" && found !== null ? Reflect.get(found, step) : undefined;
  }
  if (typeof found !== "object" || found === null) {
    throw new Error(`nothing at ${path.join("/")}`);
  }
  return found as Json;
}

const schemas = (document: Json): Json => at(document, "components", "schemas");

describe("the table of the real document", () => {
  it("states the required keys of the four maps of counts by reason (the reasons: a count dropped is a miss)", () => {
    const required = (component: string, member: string): readonly string[] => {
      const shape = SHAPES[component];
      const record = shape?.t === "all" ? shape.o[0] : shape;
      const found = record?.t === "rec" ? record.p[member] : undefined;
      const map = found?.t === "or" ? found.o.find((arm) => arm.t === "map") : found;
      return map?.t === "map" ? (map.r ?? []) : [];
    };
    expect(required("Population", "unknown_by_reason")).toEqual(["NOT_ASSESSED", "NOT_COVERED", "NO_INFORMATION", "NO_PARENT", "OUT_OF_SCOPE", "NO_ROWS"]);
    for (const [component, member] of [["Analysed", "excluded"], ["AnalysedVariable", "excluded"], ["Proportion", "excluded"]] as const) {
      expect(required(component, member), `${component}.${member}`).toEqual(["NOT_ASSESSED", "NOT_COVERED", "NO_INFORMATION", "NO_PARENT", "OUT_OF_SCOPE", "NO_ROWS", "NOT_APPLICABLE", "INVALID_VALUE"]);
    }
  });

  it("is the committed one, and covers the seven operations the web calls", () => {
    expect(renderShapes(real(), TEXT)).toBe(readFileSync(path.join(WEB, "src/api/generated/shapes.ts"), "utf8"));
    expect(Object.keys(OPERATIONS).sort()).toEqual([...TYPED_OPERATIONS].sort());
    expect(Object.keys(SHAPES).length).toBeGreaterThan(50);
  });

  it("does not enumerate Severity (an unknown one is read as block), and lists its vocabulary", () => {
    expect(SHAPES["Severity"]).toEqual({ t: "str" });
    expect(VOCABULARIES.Severity).toEqual(["info", "warn", "block"]);
    expect([...FAIL_CLOSED]).toEqual([["Severity", "block"]]);
  });

  it("the census's three lists are apart, and name every keyword the answers reach", () => {
    const all = [...INTERPRETED, ...RESIDUAL, ...ANNOTATIONS];
    expect(new Set(all).size).toBe(all.length);
    expect(RESIDUAL).toEqual(expect.arrayContaining(["if", "then", "else", "not", "contains", "minContains", "maxContains", "pattern", "minLength", "maxLength", "minItems", "maxItems", "minProperties", "maxProperties", "minimum", "maximum"]));
  });
});

/** The residuals, reviewed: the keywords the walk does not check, whichever node they are on. */
const RESIDUAL_FIXTURE = [
  "if",
  "then",
  "else",
  "not",
  "contains",
  "minContains",
  "maxContains",
  "pattern",
  "minLength",
  "maxLength",
  "minItems",
  "maxItems",
  "minProperties",
  "maxProperties",
  "minimum",
  "maximum",
  "exclusiveMinimum",
  "exclusiveMaximum",
];

describe("the census by position (MA1): a keyword is consumed where it stands, or it is named", () => {
  it("the residuals are the reviewed list, each with its reason", () => {
    expect([...RESIDUAL].sort()).toEqual([...RESIDUAL_FIXTURE].sort());
    for (const [keyword, reason] of RESIDUALS) {
      expect(reason.length, keyword).toBeGreaterThan(10);
    }
  });

  it("the builders are the reviewed set, and every interpreted keyword is some builder's", () => {
    expect(Object.keys(CONSUMES).sort()).toEqual(["allOf", "alternatives", "any", "boolean", "const", "enum", "failClosed", "json", "list", "map", "null", "number", "record", "ref", "segments", "string", "typeList"]);
    expect([...INTERPRETED].sort()).toEqual(["$ref", "additionalProperties", "allOf", "anyOf", "const", "enum", "items", "oneOf", "patternProperties", "prefixItems", "properties", "propertyNames", "required", "type", "x-aibi-json"]);
  });

  const builders = Object.entries(CONSUMES);

  it.each(builders)("%s: each keyword it consumes passes; each other interpreted keyword fails at its node (a delegating builder hands it on)", (builder, consumed) => {
    for (const keyword of INTERPRETED) {
      const check = () => {
        censusOf({ [keyword]: 1 }, "T", builder);
      };
      if (consumed.includes(keyword) || DELEGATING.includes(builder)) {
        expect(check, `${builder} consumes ${keyword}`).not.toThrow();
      } else {
        expect(check, `${builder} drops ${keyword}`).toThrow(new RegExp(`the keyword ${keyword.replace("$", "\\$")} is neither consumed by the ${builder} node, an annotation, nor a named residual`, "u"));
      }
    }
  });

  it.each(builders)("%s: an annotation and a residual pass; one no list names fails (a delegating builder's is its next node's)", (builder) => {
    for (const keyword of [...(ANNOTATIONS), ...(RESIDUAL)]) {
      expect(() => {
        censusOf({ [keyword]: 1 }, "T", builder);
      }, keyword).not.toThrow();
    }
    const unknown = () => {
      censusOf({ uniqueItems: true }, "T", builder);
    };
    if (DELEGATING.includes(builder)) {
      expect(unknown).not.toThrow();
    } else {
      expect(unknown).toThrow(ShapeError);
    }
  });

  it("json consumes the mark and anyOf alone (the inside of an x-aibi-json value is read as any JSON: a named residual; a sibling is blind to the walk and the oracle: m3)", () => {
    expect(CONSUMES["json"]).toEqual(["x-aibi-json", "anyOf"]);
    expect(() => {
      censusOf({ "x-aibi-json": true, anyOf: [{ type: "string" }], description: "d" }, "T", "json");
    }).not.toThrow();
    for (const keyword of [...INTERPRETED.filter((one) => one !== "anyOf" && one !== "x-aibi-json"), "uniqueItems", "dependentRequired", "nullable"]) {
      expect(() => {
        censusOf({ "x-aibi-json": true, [keyword]: {} }, "T", "json");
      }, keyword).toThrow(new RegExp(`the keyword ${keyword.replace("$", "\\$")} is neither consumed by the json node`, "u"));
    }
  });

  it("a builder the table does not declare is refused", () => {
    expect(() => {
      censusOf({}, "T", "nothing");
    }).toThrow(/declares no keywords/u);
  });

  it("a synthetic table of consumed keywords (the census reads the table it is given)", () => {
    const only = { thing: ["alpha"] } as const;
    expect(() => {
      censusOf({ alpha: 1 }, "T", "thing", only);
    }).not.toThrow();
    expect(() => {
      censusOf({ beta: 1 }, "T", "thing", only);
    }).toThrow(/the keyword beta is neither consumed by the thing node/u);
    expect(() => {
      censusOf({ beta: 1 }, "T", "thing", only, ["thing"]);
    }).not.toThrow();
  });
});

/** The census's three lists as the generator states them, in the fixture's form (sorted). */
const censusOfGenerator = (): Json => ({
  annotations: [...ANNOTATIONS].sort(),
  consumes: Object.fromEntries(Object.keys(CONSUMES).sort().map((builder) => [builder, [...(CONSUMES[builder] ?? [])].sort()])),
  delegating: [...DELEGATING].sort(),
});

describe("ANNOTATIONS, CONSUMES and DELEGATING are the reviewed fixture's (MA3)", () => {
  const fixture = JSON.parse(readFileSync(path.join(WEB, "tests/fixtures/census.json"), "utf8")) as Json;

  it("the generator's three lists are the fixture's: a word added to any is a change to the fixture, in review", () => {
    expect(censusOfGenerator()).toEqual(fixture);
  });

  it("the fixture is the reviewed shape: sorted, no annotation among what a builder consumes", () => {
    expect(Object.keys(fixture)).toEqual(["annotations", "consumes", "delegating"]);
    const consumed = Object.values(fixture["consumes"] as Record<string, readonly string[]>).flat();
    for (const annotation of fixture["annotations"] as string[]) {
      expect(consumed, annotation).not.toContain(annotation);
    }
  });

  it("a keyword added to any of the three lists is seen (the comparison's control)", () => {
    const widened = censusOfGenerator();
    (widened["annotations"] as string[]).push("dependentRequired");
    expect(widened).not.toEqual(fixture);
    const consumes = censusOfGenerator();
    ((consumes["consumes"] as Record<string, string[]>)["map"] as string[]).push("items");
    expect(consumes).not.toEqual(fixture);
    const delegating = censusOfGenerator();
    (delegating["delegating"] as string[]).push("record");
    expect(delegating).not.toEqual(fixture);
  });
});

/** What watching a builder gives: the keys a node had, the keys read of it, the builder that built it. */
interface Visit {
  builder: string | undefined;
  readonly present: readonly string[];
  readonly reads: Set<string>;
}

/** An observer that hands each node's schema through a `Proxy` recording every key read of it (a
 * `get`, a `has`, an own-property lookup: the census's own `Reflect.ownKeys` is no read). */
function watching(): { readonly observer: { visit: (schema: Json) => Json; built: (schema: Json, builder: string) => void }; readonly visits: Visit[] } {
  const visits: Visit[] = [];
  const byProxy = new WeakMap<object, Visit>();
  const observer = {
    visit(schema: Json): Json {
      const visit: Visit = { builder: undefined, present: Reflect.ownKeys(schema).map(String), reads: new Set() };
      const note = (key: string | symbol): void => {
        if (typeof key === "string") {
          visit.reads.add(key);
        }
      };
      const proxy = new Proxy(schema, {
        get(target, key, receiver): unknown {
          note(key);
          return Reflect.get(target, key, receiver) as unknown;
        },
        has(target, key): boolean {
          note(key);
          return Reflect.has(target, key);
        },
        getOwnPropertyDescriptor(target, key): PropertyDescriptor | undefined {
          note(key);
          return Reflect.getOwnPropertyDescriptor(target, key);
        },
      });
      visits.push(visit);
      byProxy.set(proxy, visit);
      return proxy;
    },
    built(schema: Json, builder: string): void {
      const visit = byProxy.get(schema);
      if (visit !== undefined) {
        visit.builder = builder;
      }
    },
  };
  return { observer, visits };
}

/** A synthetic node of each of the 16 builders (`failClosed` has none: `Severity` only the real document gives) that carries every
 * keyword the builder declares (the ones a builder reads when it has them), each with the keyword that no one node can have beside another. */
const JSON_ARMS: readonly Json[] = [
  { type: ["string", "boolean", "null"] },
  { type: "number", minimum: -1, maximum: 1 },
  { type: "array", items: { $ref: "#/components/schemas/OutputJson" } },
  { type: "object", additionalProperties: { $ref: "#/components/schemas/OutputJson" } },
];
const TEXT_SEGMENT = { $ref: "#/components/schemas/TextSegment" };
const DATA_SEGMENT = { $ref: "#/components/schemas/DataSegment" };
const SYNTHETIC: Readonly<Record<string, readonly Json[]>> = {
  json: [{ "x-aibi-json": { direction: "response", null: true }, anyOf: JSON_ARMS }],
  allOf: [{ allOf: [{ type: "string" }], type: "string" }],
  ref: [{ $ref: "#/components/schemas/Refusals" }],
  segments: [{ anyOf: [TEXT_SEGMENT, DATA_SEGMENT] }, { oneOf: [TEXT_SEGMENT, DATA_SEGMENT] }],
  alternatives: [{ anyOf: [{ type: "null" }, { type: "string" }], type: "string" }, { oneOf: [{ type: "null" }, { type: "string" }] }],
  const: [{ const: "x", type: "string" }],
  enum: [{ enum: ["a"], type: "string" }],
  typeList: [{ type: ["string", "null"] }],
  string: [{ type: "string" }],
  number: [{ type: "integer" }],
  boolean: [{ type: "boolean" }],
  null: [{ type: "null" }],
  list: [{ type: "array", items: { type: "string" }, prefixItems: [{ type: "string" }] }],
  record: [{ type: "object", properties: { a: { type: "string" } }, required: ["a"], additionalProperties: false }],
  map: [{ type: "object", required: ["a"], additionalProperties: { type: "string" }, patternProperties: { "^a$": { type: "string" } }, propertyNames: { type: "string" } }],
  any: [{}],
};

describe("CONSUMES is true by construction: the keys a builder reads at a node are the keys it declares (MA3)", () => {
  /** Every visit of the generation of the real document, and of the synthetic nodes. */
  const realVisits = (): Visit[] => {
    const { observer, visits } = watching();
    shapesOf(real(), observer);
    return visits;
  };
  const syntheticVisits = (): Visit[] => {
    const { observer, visits } = watching();
    const built = builderOf(real(), observer);
    Object.values(SYNTHETIC)
      .flat()
      .forEach((schema, index) => {
        built.node(schema, `T${String(index)}`, false);
      });
    return visits;
  };

  const readOf = (visit: Visit): string[] => visit.present.filter((key) => visit.reads.has(key)).sort();

  it("the synthetic nodes are those of the 16 builders (failClosed has none), and carry every keyword the builder declares", () => {
    expect(Object.keys(SYNTHETIC).sort()).toEqual(Object.keys(CONSUMES).filter((builder) => builder !== "failClosed").sort());
    for (const [builder, schemas] of Object.entries(SYNTHETIC)) {
      const declared = CONSUMES[builder];
      const carried = new Set(schemas.flatMap((schema) => Object.keys(schema)));
      for (const keyword of declared ?? []) {
        expect(carried.has(keyword), `${builder} carries ${keyword}`).toBe(true);
      }
    }
  });

  it.each([
    ["every node of the real document", realVisits],
    ["a synthetic node of each of the 16 builders", syntheticVisits],
  ])("at every node of %s, the builder reads exactly the declared keys the node has", (_where, visitsOf) => {
    const visits = visitsOf();
    expect(visits.length).toBeGreaterThan(10);
    for (const visit of visits) {
      expect(visit.builder, `a node of keys ${visit.present.join(",")} no builder named`).toBeDefined();
      const builder = visit.builder ?? "";
      const declared = CONSUMES[builder];
      if (declared === undefined) {
        continue;
      }
      const has = visit.present.filter((key) => declared.includes(key)).sort();
      if (DELEGATING.includes(builder)) {
        // It reads every key (it hands them on, whole, to the node that censuses each).
        expect(readOf(visit), `${builder} at ${visit.present.join(",")}`).toEqual(expect.arrayContaining(has));
      } else {
        expect(readOf(visit), `${builder} at ${visit.present.join(",")}`).toEqual(has);
      }
    }
  });

  it("over all nodes of a builder, the keys read are exactly the keys it declares", () => {
    const visits = [...realVisits(), ...syntheticVisits()];
    for (const [builder, declared] of Object.entries(CONSUMES)) {
      const read = new Set(visits.filter((visit) => visit.builder === builder).flatMap(readOf));
      const wanted = new Set(declared);
      if (DELEGATING.includes(builder)) {
        for (const keyword of wanted) {
          expect(read.has(keyword), `${builder} reads ${keyword}`).toBe(true);
        }
      } else {
        expect([...read].sort(), builder).toEqual([...wanted].sort());
      }
    }
  });

  it("the observer's own controls: a key read is seen, a key present and not read is not, a builder gets named", () => {
    const { observer, visits } = watching();
    const watched = observer.visit({ type: "string", title: "T" });
    expect(watched["type"]).toBe("string");
    expect(Object.hasOwn(watched, "const")).toBe(false);
    observer.built(watched, "string");
    expect(visits).toHaveLength(1);
    expect([...(visits[0]?.reads ?? [])].sort()).toEqual(["const", "type"]);
    expect(visits[0]?.present).toEqual(["type", "title"]);
    expect(visits[0]?.builder).toBe("string");
    // The census's own look at the keys is no read.
    const other = observer.visit({ type: "string", title: "T" });
    censusOf(other, "T", "string");
    expect(visits[1]?.reads.size).toBe(0);
  });
});

/** The keywords the generator names annotations, held to the JSON Schema 2020-12 vocabularies (m3): the
 * annotation vocabularies' keywords (`meta-data`, `format-annotation`) or one of the named `x-aibi-`
 * marks, never an assertion or an applicator (a joint blind spot of the walk and the oracle, which both drop them). */
describe("ANNOTATIONS are annotation keywords of JSON Schema 2020-12 or named marks, never assertions (m3)", () => {
  const vocabulary = (JSON.parse(readFileSync(path.join(WEB, "../schemas/vocabulary.json"), "utf8")) as { vocabularies: Record<string, { rendered: string[]; refused: string[] }> }).vocabularies;
  const wordsOf = (names: readonly string[]): Set<string> => new Set(names.flatMap((name) => [...(vocabulary[name]?.rendered ?? []), ...(vocabulary[name]?.refused ?? [])]));
  const annotating = wordsOf(["meta-data", "format-annotation"]);
  const MARKS = ["x-aibi-computed", "x-aibi-data", "x-aibi-server-number"];
  const isAnnotation = (keyword: string): boolean => annotating.has(keyword) || MARKS.includes(keyword);

  it("every annotation of the generator is one, and none is an assertion or an applicator of any vocabulary", () => {
    const assertions = wordsOf(Object.keys(vocabulary).filter((name) => name !== "meta-data" && name !== "format-annotation"));
    expect(annotating.size).toBeGreaterThan(5);
    expect(assertions.size).toBeGreaterThan(30);
    for (const keyword of ANNOTATIONS) {
      expect(isAnnotation(keyword), `${keyword} is a 2020-12 annotation keyword or a named x- mark`).toBe(true);
      expect(assertions.has(keyword), `${keyword} is no assertion or applicator`).toBe(false);
    }
  });

  it("the control: an assertion, an applicator and an unnamed mark are none (the check has teeth)", () => {
    for (const keyword of ["dependentRequired", "required", "maxLength", "pattern", "properties", "const", "x-aibi-other"]) {
      expect(isAnnotation(keyword), keyword).toBe(false);
    }
    for (const keyword of ["title", "description", "default", "x-aibi-computed"]) {
      expect(isAnnotation(keyword), keyword).toBe(true);
    }
  });
});

describe("a segment pair's arms are censused as references (m1: D426's `a keyword in none of the three lists fails the generation`)", () => {
  /** The arm of `Caveat.message`'s segment pair that is a reference to `name`. */
  const arm = (document: Json, name: "TextSegment" | "DataSegment"): Json => {
    const arms = at(schemas(document), "Caveat", "properties", "message", "items")["anyOf"] as Json[];
    const found = arms.find((one) => one["$ref"] === `#/components/schemas/${name}`);
    if (found === undefined) {
      throw new Error(`no ${name} arm`);
    }
    return found;
  };
  const beside: readonly (readonly [string, unknown])[] = [
    ["required", ["bogus"]],
    ["nullable", true],
    ["uniqueItems", true],
    ["properties", { text: { enum: ["only"] } }],
    ["type", "string"],
    ["dependentRequired", { text: ["bogus"] }],
    ["items", { type: "string" }],
    ["enum", ["x"]],
  ];

  it.each(beside.flatMap(([keyword, value]) => (["TextSegment", "DataSegment"] as const).map((name) => [keyword, value, name] as const)))("%s beside the %s arm's $ref fails the generation naming the keyword", (keyword, value, name) => {
    const document = real();
    arm(document, name)[keyword] = value;
    expect(() => shapesOf(document)).toThrow(ShapeError);
    expect(() => shapesOf(document)).toThrow(new RegExp(`Caveat\\.message\\[\\]: the keyword ${keyword} is neither consumed by the ref node, an annotation, nor a named residual`, "u"));
  });

  it("an annotation and a residual beside the $ref pass (a residual is named, an annotation checks nothing)", () => {
    const document = real();
    Object.assign(arm(document, "TextSegment"), { description: "d", "x-aibi-computed": true, maxLength: 5 });
    expect(shapesOf(document).components["Caveat"]).toEqual(shapesOf(real()).components["Caveat"]);
  });

  it("the arms are visited through the observer, by position, and built as ref nodes (the Proxy test sees them: n3, S2)", () => {
    // `visit` hands back a copy and notes it: a node built from anything else was not visited.
    const visited = new Set<Json>();
    const visits: Json[] = [];
    const built: { readonly schema: Json; readonly builder: string }[] = [];
    shapesOf(real(), {
      visit: (schema: Json) => {
        const copy = { ...schema };
        visited.add(copy);
        visits.push(copy);
        return copy;
      },
      built: (schema: Json, builder: string) => {
        built.push({ schema, builder });
      },
    });
    const isArm = (schema: Json): boolean => schema["$ref"] === "#/components/schemas/TextSegment" || schema["$ref"] === "#/components/schemas/DataSegment";
    // Every arm of a segment pair is visited (once for each arm of each pair)...
    const arms = visits.filter(isArm);
    expect(arms.filter((arm) => arm["$ref"] === "#/components/schemas/TextSegment").length).toBeGreaterThan(0);
    expect(arms.filter((arm) => arm["$ref"] === "#/components/schemas/DataSegment").length).toBe(arms.filter((arm) => arm["$ref"] === "#/components/schemas/TextSegment").length);
    // ...and every node built from one is a visited node, built as a ref.
    const fromArms = built.filter(({ schema }) => isArm(schema));
    expect(fromArms.length).toBe(arms.length);
    for (const { schema, builder } of fromArms) {
      expect(visited.has(schema), "a segment arm built from a node the observer did not visit").toBe(true);
      expect(builder).toBe("ref");
    }
    // The control: a node built without its visit is told apart.
    expect(visited.has({ $ref: "#/components/schemas/TextSegment" })).toBe(false);
  });

  it("an x-aibi-json mark's siblings: anyOf (a list) and annotations alone; any other keyword fails (m3)", () => {
    const document = real();
    at(schemas(document), "OutputJson")["properties"] = {};
    expect(() => shapesOf(document)).toThrow(/OutputJson: the keyword properties is neither consumed by the json node/u);
    const open = real();
    at(schemas(open), "OutputJson")["anyOf"] = { type: "string" };
    expect(() => shapesOf(open)).toThrow(/OutputJson: beside x-aibi-json, anyOf lists the JSON types/u);
  });
});

describe("the arms beside an x-aibi-json mark are held to the JSON types (n2 of round 4: DJ1-DJ3)", () => {
  /** The arms of `OutputJson`, the one component of the answers with the mark. */
  const arms = (document: Json): Json[] => at(schemas(document), "OutputJson")["anyOf"] as Json[];
  const refused = (change: (document: Json) => void, words: RegExp): void => {
    const document = real();
    change(document);
    expect(() => shapesOf(document)).toThrow(ShapeError);
    expect(() => shapesOf(document)).toThrow(words);
  };

  it("the real document's arms pass, and the synthetic node's are the same", () => {
    expect(() => shapesOf(real())).not.toThrow();
    expect(arms(real()).flatMap((arm) => (Array.isArray(arm["type"]) ? (arm["type"] as string[]) : [arm["type"] as string])).sort()).toEqual(["array", "boolean", "null", "number", "object", "string"]);
  });

  it.each([
    ["DJ1: `null` dropped from the arms", (d: Json) => { (arms(d)[0] as Json)["type"] = ["string", "boolean"]; }, /OutputJson: beside x-aibi-json, anyOf lists each of the JSON types .* once/u],
    ["DJ2: `required` on the object arm", (d: Json) => { (arms(d)[3] as Json)["required"] = ["must"]; }, /OutputJson: the keyword required is not allowed in an arm beside x-aibi-json/u],
    ["DJ3: an `enum` on an arm", (d: Json) => { (arms(d)[0] as Json)["enum"] = ["only"]; }, /OutputJson: the keyword enum is not allowed in an arm beside x-aibi-json/u],
    ["a `const` on an arm", (d: Json) => { (arms(d)[0] as Json)["const"] = "x"; }, /the keyword const is not allowed in an arm/u],
    ["a `pattern` (a residual elsewhere) on an arm", (d: Json) => { (arms(d)[0] as Json)["pattern"] = "^x"; }, /the keyword pattern is not allowed in an arm/u],
    ["a `not` (a residual elsewhere) on an arm", (d: Json) => { (arms(d)[0] as Json)["not"] = { type: "null" }; }, /the keyword not is not allowed in an arm/u],
    ["`minimum` on a string arm (a bound is a number's)", (d: Json) => { (arms(d)[0] as Json)["minimum"] = 0; }, /the keyword minimum is not allowed in an arm/u],
    ["`items` on a list of types", (d: Json) => { (arms(d)[0] as Json)["items"] = { $ref: "#/components/schemas/OutputJson" }; }, /the keyword items is not allowed in an arm/u],
    ["an `items` that is no reference to an x-aibi-json component", (d: Json) => { (arms(d)[2] as Json)["items"] = { type: "string" }; }, /the items of an arm beside x-aibi-json is a reference to an x-aibi-json component/u],
    ["an `additionalProperties` that is a reference to another component", (d: Json) => { (arms(d)[3] as Json)["additionalProperties"] = { $ref: "#/components/schemas/Refusals" }; }, /the additionalProperties of an arm beside x-aibi-json is a reference/u],
    ["a type that is no JSON type (`integer`)", (d: Json) => { (arms(d)[1] as Json)["type"] = "integer"; }, /an arm beside x-aibi-json has a type of the JSON types/u],
    ["an arm without a type", (d: Json) => { delete (arms(d)[1] as Json)["type"]; }, /an arm beside x-aibi-json has a type of the JSON types/u],
    ["an arm that is no schema", (d: Json) => { arms(d)[1] = true as unknown as Json; }, /an arm beside x-aibi-json is a schema of JSON types/u],
    ["a type listed twice", (d: Json) => { arms(d).push({ type: "string" }); }, /anyOf lists each of the JSON types .* once/u],
    ["no anyOf", (d: Json) => { delete at(schemas(d), "OutputJson")["anyOf"]; }, /OutputJson: beside x-aibi-json, anyOf lists the JSON types/u],
    ["a mark that does not state null: true", (d: Json) => { at(schemas(d), "OutputJson")["x-aibi-json"] = { direction: "response", null: false }; }, /OutputJson: an x-aibi-json mark states null: true/u],
    ["a mark that is no object", (d: Json) => { at(schemas(d), "OutputJson")["x-aibi-json"] = true; }, /OutputJson: an x-aibi-json mark states null: true/u],
  ])("%s fails the generation naming it", (_case, change, words) => {
    refused(change, words);
  });

  it("an annotation, and a bound on the number arm, pass", () => {
    const document = real();
    Object.assign(arms(document)[0] ?? {}, { description: "d", title: "T" });
    Object.assign(arms(document)[1] ?? {}, { minimum: -5, maximum: 5 });
    expect(shapesOf(document).components["OutputJson"]).toEqual({ t: "json" });
  });
});

describe("the generation refuses what the walk would not read", () => {
  it.each([
    ["an unknown keyword", (d: Json) => { at(schemas(d), "Caveat")["uniqueItems"] = true; }, /the keyword uniqueItems is neither consumed by the .* node, an annotation, nor a named residual/u],
    ["a segment drifted (TextSegment with a member more)", (d: Json) => { at(schemas(d), "TextSegment", "properties")["more"] = { type: "string" }; }, /TextSegment is not exactly \{text\}/u],
    ["a segment drifted (truncated false)", (d: Json) => { at(schemas(d), "DataSegment", "properties", "truncated")["const"] = false; }, /DataSegment is not exactly/u],
    ["a lone segment", (d: Json) => { at(schemas(d), "Caveat", "properties", "message")["items"] = { $ref: "#/components/schemas/TextSegment" }; }, /a segment outside the pair/u],
    ["an open record an answer is built from", (d: Json) => { delete at(schemas(d), "CatalogHit")["additionalProperties"]; }, /states additionalProperties: false/u],
    ["a record with patternProperties", (d: Json) => { at(schemas(d), "CatalogHit")["patternProperties"] = { "^x$": { type: "string" } }; }, /the keyword patternProperties is neither consumed by the record node/u],
    ["a member named __proto__", (d: Json) => { Object.defineProperty(at(schemas(d), "CatalogHit", "properties"), "__proto__", { value: { type: "string" }, enumerable: true }); }, /a member named __proto__/u],
    ["a non-string enum", (d: Json) => { at(schemas(d), "ReleaseOut", "properties")["status"] = { enum: [1, 2] }; }, /an enum of an answer is of strings/u],
    ["a fail-closed vocabulary without its fallback", (d: Json) => { at(schemas(d), "Severity")["enum"] = ["info", "warn"]; }, /the fail-closed member is not in the vocabulary/u],
    ["a pattern JavaScript cannot compile", (d: Json) => { at(schemas(d), "Proportion", "properties", "not_estimable")["patternProperties"] = { "(?P<x>a)": { type: "string" } }; }, /does not compile with the u flag/u],
    ["a 2xx other than 200", (d: Json) => { at(d, "paths", "/api/tools/explain", "post", "responses")["201"] = { content: { "application/json": { schema: { $ref: "#/components/schemas/Explanation" } } } }; }, /the one answer of 2xx is 200's/u],
    ["an error answer that is no refusal list", (d: Json) => { at(d, "paths", "/api/tools/explain", "post", "responses", "404", "content", "application/json")["schema"] = { $ref: "#/components/schemas/Explanation" }; }, /an answer but a 200's is Refusals/u],
    ["properties beside anyOf (dropped where the alternatives are built)", (d: Json) => { at(schemas(d), "Population", "properties", "unknown_by_reason")["properties"] = { more: { type: "string" } }; }, /Population\.unknown_by_reason: the keyword properties is neither consumed by the alternatives node/u],
    ["items on an object (dropped where the record is built)", (d: Json) => { at(schemas(d), "CatalogHit")["items"] = { type: "string" }; }, /CatalogHit: the keyword items is neither consumed by the record node/u],
    ["propertyNames on a record", (d: Json) => { at(schemas(d), "CatalogHit")["propertyNames"] = { type: "string" }; }, /CatalogHit: the keyword propertyNames is neither consumed by the record node/u],
    ["required alone in an allOf check (a check the walk would read as any)", (d: Json) => { (at(schemas(d), "Proportion")["allOf"] as Json[]).push({ required: ["ci"] }); }, /the keyword required is neither consumed by the any node/u],
    ["required beside a $ref", (d: Json) => { at(schemas(d), "Caveat", "properties", "severity")["required"] = ["x"]; }, /Caveat\.severity: the keyword required is neither consumed by the ref node/u],
    ["required on a string", (d: Json) => { at(schemas(d), "CatalogHit", "properties", "dataset")["required"] = ["x"]; }, /CatalogHit\.dataset: the keyword required is neither consumed by the string node/u],
    ["anyOf beside oneOf", (d: Json) => { at(schemas(d), "Population", "properties", "unknown_by_reason")["oneOf"] = [{ type: "null" }]; }, /anyOf beside oneOf/u],
    ["a grammar position of another form", (d: Json) => { delete at(schemas(d), "CatalogHit", "properties", "dataset")["not"]; }, /a grammar is exactly/u],
    ["a key beside an allOf, handed to the node that builds the base (items beside a record's allOf)", (d: Json) => { at(schemas(d), "Proportion")["items"] = { type: "string" }; }, /Proportion: the keyword items is neither consumed by the record node/u],
    ["required beside an allOf of a list (handed on, whole, to the node that builds the base: a list reads no required)", (d: Json) => { at(schemas(d), "Caveat", "properties", "message")["allOf"] = [{ type: "array" }]; at(schemas(d), "Caveat", "properties", "message")["required"] = ["x"]; }, /the keyword required is neither consumed by the .* node/u],
    ["a type list's arm with a keyword it does not read", (d: Json) => { Object.assign(at(schemas(d), "CatalogHit", "properties", "tables_left_out"), { type: ["integer", "null"], required: ["x"] }); }, /CatalogHit\.tables_left_out: the keyword required is neither consumed by the number node/u],
    ["an empty type list", (d: Json) => { at(schemas(d), "CatalogHit", "properties", "tables_left_out")["type"] = []; }, /CatalogHit\.tables_left_out: a type list is not empty/u],
    ["required that is a string on a record", (d: Json) => { at(schemas(d), "CatalogHit")["required"] = "dataset"; }, /CatalogHit: required is a list of strings/u],
    ["required that holds a number on a record", (d: Json) => { at(schemas(d), "CatalogHit")["required"] = ["dataset", 1]; }, /CatalogHit: required is a list of strings/u],
    ["required that is an object on a map of counts by reason", (d: Json) => { ((at(schemas(d), "Population", "properties", "unknown_by_reason")["anyOf"] as Json[])[0] as Json)["required"] = { NO_ROWS: 1 }; }, /Population\.unknown_by_reason: required is a list of strings/u],
    ["a record's additionalProperties as a schema (a closed answer's)", (d: Json) => { at(schemas(d), "CatalogHit")["additionalProperties"] = { type: "string" }; }, /CatalogHit: a record's additionalProperties is a boolean/u],
    ["a record's additionalProperties as a schema (an allOf check's)", (d: Json) => { Object.assign((at(schemas(d), "Proportion")["allOf"] as Json[]).find((member) => Object.hasOwn(member, "properties") && !Object.hasOwn(member, "if")) ?? {}, { additionalProperties: { type: "string" } }); }, /Proportion: a record's additionalProperties is a boolean/u],
    ["a const that is not of its type", (d: Json) => { ((at(schemas(d), "ReleaseOut", "properties", "label")["anyOf"] as Json[])[1] as Json)["type"] = "integer"; }, /ReleaseOut\.label: a const that is not of its type "integer"/u],
    ["an enum beside another type", (d: Json) => { at(schemas(d), "ReleaseOut", "properties", "status")["type"] = "boolean"; }, /ReleaseOut\.status: an enum of strings is not of the type "boolean"/u],
  ])("%s", (_case, change, words) => {
    const document = real();
    change(document);
    expect(() => shapesOf(document)).toThrow(ShapeError);
    expect(() => shapesOf(document)).toThrow(words);
  });
});
