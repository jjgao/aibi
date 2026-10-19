/**
 * The client's confinement, over the TypeScript program of the app (D419): the lint refuses the
 * imports by their text, and this test holds what they resolve to. It runs over every file of the
 * program that is not tooling (`scripts/tooling.mjs`), not `src/` alone; that every file of the
 * package is tooling, under `src/` with an extension of the table or a named root file is
 * `scope.test.ts`'s. (The build's gate holds the same on the bundler's graph,
 * which also sees what no specifier names: `tests/gates/confinement.test.ts`.)
 *
 * - Outside `src/api/`, every import, re-export or dynamic import that resolves into `src/api/`
 *   resolves to `src/api/index.ts` (the harness's also to `oracle.ts`), and no file uses
 *   `import.meta.glob`, which resolves modules no specifier names.
 * - The box's internals are named, through any alias or namespace, where they belong alone (`box`
 *   in `src/api/box.ts` and `src/api/decode.ts`, `textOf` in `box.ts`, `provenance.ts`, where
 *   `idAt` is the one function that reads a box's text for a request and `curator.ts` alone names
 *   it, D425, and `numbers.ts`, which reads it for the page, D430, its glyphs `format.tsx`'s alone), as
 *   are the request half's (`requestId`, `stamp`, `originOf`, `recorder`, `urlOf`, `datasetOf`);
 *   the decoder's (`decode`, `revive`) in
 *   `src/api/decode.ts` and `src/api/client.ts` alone; the counter (`decodedCount`) in
 *   `src/api/decode.ts`, `src/api/oracle.ts` and the harness alone.
 * - `src/api/index.ts` exports exactly the reviewed list, none of them an internal, and no exported
 *   function (a namespace's included) takes a parameter that can hold a `ServerNumber` (by the
 *   checker's assignability, `takers.ts`, never a name; the type guard `isServerNumber` and
 *   `NUMBER_PINS` (D430: `Num`, `isZero`, `isPositive`, `largestOf`, `RetryAfter`) alone are pinned,
 *   each by its exact signature, each taking one number or one map, none comparing two given
 *   apart, and a generic is judged instantiated, `request-half.test.ts` (b)): nothing it exports
 *   turns a server number into a number, digits as text or a request's material.
 * - `src/api/operator.ts` exports the reviewed list, and the operator client holds to its
 *   allow-list (`surface.ts`).
 */
import { readFileSync } from "node:fs";
import path from "node:path";

import ts from "typescript";
import { beforeAll, describe, expect, it } from "vitest";

import { isTooling } from "../../scripts/tooling.mjs";
import { ServerNumber, ServerText } from "../../src/api/box";
import { appOptions, check, WEB } from "./program";
import { declarationOf, surface } from "./surface";
import { gives, takes, takesText, type Takes } from "./takers";

const SRC = path.join(WEB, "src");
const API = path.join(SRC, "api");
const INDEX = path.join(API, "index.ts");
const ORACLE = path.join(API, "oracle.ts");
const OPERATOR = path.join(API, "operator.ts");
const HARNESS = path.join(SRC, "harness");
const OPERATOR_ENTRY = path.join(SRC, "operator");

let program: ts.Program;
let checker: ts.TypeChecker;
let taker: Takes;

beforeAll(() => {
  const parsed = ts.getParsedCommandLineOfConfigFile(path.join(WEB, "tsconfig.app.json"), {}, {
    ...ts.sys,
    onUnRecoverableConfigFileDiagnostic: () => undefined,
  });
  if (parsed === undefined) {
    throw new Error("tsconfig.app.json is unreadable");
  }
  program = ts.createProgram(parsed.fileNames, appOptions());
  checker = program.getTypeChecker();
  taker = takes(program, checker);
}, 120_000);

/** The files of the app: every file of the program under the package but the tooling. */
const sources = (): ts.SourceFile[] =>
  program.getSourceFiles().filter((file) => {
    const relative = path.relative(WEB, file.fileName);
    return !relative.startsWith("..") && !relative.startsWith("node_modules/") && !isTooling(relative);
  });

/** The module specifiers of a file (static imports, re-exports, literal dynamic imports, and
 * `"<computed>"` for a dynamic import of a name not written out), and `"<glob>"` for each `import.meta.glob` (any member of
 * `import.meta` named `glob...`, called or not). */
function specifiers(file: ts.SourceFile): string[] {
  const found: string[] = [];
  const visit = (node: ts.Node): void => {
    if ((ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) && node.moduleSpecifier !== undefined && ts.isStringLiteral(node.moduleSpecifier)) {
      found.push(node.moduleSpecifier.text);
    }
    if (ts.isCallExpression(node) && node.expression.kind === ts.SyntaxKind.ImportKeyword) {
      const [first] = node.arguments;
      found.push(first !== undefined && ts.isStringLiteralLike(first) ? first.text : "<computed>");
    }
    if (
      (ts.isPropertyAccessExpression(node) && node.expression.kind === ts.SyntaxKind.MetaProperty && node.name.text.startsWith("glob")) ||
      (ts.isElementAccessExpression(node) && node.expression.kind === ts.SyntaxKind.MetaProperty)
    ) {
      found.push("<glob>");
    }
    ts.forEachChild(node, visit);
  };
  visit(file);
  return found;
}

function resolved(specifier: string, from: string): string | undefined {
  return ts.resolveModuleName(specifier, from, appOptions(), ts.sys).resolvedModule?.resolvedFileName;
}

/** An export of a module of `src/api/`. */
function exported(file: string, name: string): ts.Symbol {
  const source = program.getSourceFile(path.join(API, file));
  const symbol = source === undefined ? undefined : checker.getSymbolAtLocation(source);
  const found = symbol === undefined ? undefined : checker.getExportsOfModule(symbol).find((member) => member.name === name);
  if (found === undefined) {
    throw new Error(`${file} exports no ${name}`);
  }
  return found;
}

/** The declaration of an export of a module of `src/api/`. */
function declared(file: string, name: string): ts.Declaration {
  const declaration = exported(file, name).declarations?.[0];
  if (declaration === undefined) {
    throw new Error(`${file} exports no ${name}`);
  }
  return declaration;
}

/** The files of `src/` that name a declaration, through any alias (an import, a renaming, a
 * namespace member, a re-export). */
function namers(declaration: ts.Declaration): string[] {
  const found = new Set<string>();
  for (const file of sources()) {
    const visit = (node: ts.Node): void => {
      if (ts.isIdentifier(node)) {
        let symbol = checker.getSymbolAtLocation(node);
        if (symbol !== undefined && (symbol.flags & ts.SymbolFlags.Alias) !== 0) {
          symbol = checker.getAliasedSymbol(symbol);
        }
        if (symbol?.declarations?.includes(declaration) === true) {
          found.add(path.relative(WEB, file.fileName));
        }
      }
      ts.forEachChild(node, visit);
    };
    visit(file);
  }
  return [...found].sort();
}

/** What a file outside `src/api/` may import of it: the index; for the operator entry the page's
 * operator client too (D423), and for the harness the oracle and the operator client. */
function allowedTargets(file: string): string[] {
  if (file.startsWith(`${HARNESS}/`)) {
    return [INDEX, ORACLE, OPERATOR];
  }
  return file.startsWith(`${OPERATOR_ENTRY}/`) ? [INDEX, OPERATOR] : [INDEX];
}

describe("outside src/api/, the client is reached through its index alone", () => {
  it("lets the harness, and no other file, reach the oracle's counter too, and the operator entry and the harness alone the operator client", () => {
    expect(allowedTargets(path.join(HARNESS, "Harness.tsx"))).toEqual([INDEX, ORACLE, OPERATOR]);
    expect(allowedTargets(path.join(SRC, "operator", "main.tsx"))).toEqual([INDEX, OPERATOR]);
    expect(allowedTargets(path.join(SRC, "operators", "x.ts"))).toEqual([INDEX]);
    expect(allowedTargets(path.join(SRC, "catalogue", "main.tsx"))).toEqual([INDEX]);
    expect(allowedTargets(path.join(SRC, "harnessed", "x.ts"))).toEqual([INDEX]);
  });

  it("every specifier that resolves into src/api/ resolves to index.ts (the harness's also to oracle.ts, the operator entry's and the harness's to operator.ts)", () => {
    const wrong: string[] = [];
    let reached = 0;
    for (const file of sources()) {
      if (file.fileName.startsWith(`${API}/`)) {
        continue;
      }
      const allowed = allowedTargets(file.fileName);
      for (const specifier of specifiers(file)) {
        expect(specifier, path.relative(WEB, file.fileName)).not.toBe("<computed>");
        expect(specifier, path.relative(WEB, file.fileName)).not.toBe("<glob>");
        const target = resolved(specifier, file.fileName);
        if (target?.startsWith(`${API}/`) === true) {
          reached += 1;
          if (!allowed.includes(target)) {
            wrong.push(`${path.relative(WEB, file.fileName)}: ${specifier}`);
          }
        }
      }
    }
    expect(wrong).toEqual([]);
    expect(reached).toBeGreaterThanOrEqual(2);
  });

  it("sees an import.meta.glob in any form, and a computed import (the collector's own controls)", () => {
    const text = (code: string): string[] => specifiers(ts.createSourceFile("x.ts", code, ts.ScriptTarget.ES2023, true));
    expect(text("const a = import.meta.glob('./api/box.ts', { eager: true });")).toEqual(["<glob>"]);
    expect(text("const a = import.meta.glob<string>('./api/*.ts');")).toEqual(["<glob>"]);
    expect(text("const a = import.meta.globEager('./api/*.ts');")).toEqual(["<glob>"]);
    expect(text("const a = import.meta['glob']('./api/*.ts');")).toEqual(["<glob>"]);
    expect(text("const a = import.meta.glob;")).toEqual(["<glob>"]);
    expect(text("import './a'; export * from './b'; void import('./c'); void import(d);")).toEqual(["./a", "./b", "./c", "<computed>"]);
    expect(text("const a = import.meta.url;")).toEqual([]);
  });

  it("covers every non-tooling file of the program, the app's and any other directory's alike", () => {
    const relative = sources().map((file) => path.relative(WEB, file.fileName));
    expect(relative).toContain("src/operator/Operator.tsx");
    expect(relative).toContain("src/api/generated/routes.ts");
    expect(relative.filter((name) => isTooling(name))).toEqual([]);
  });
});

describe("the internals are named where they belong alone", () => {
  /** The files the lint's table lets name an internal, though none does today: `textOf`'s owners are
   * `decode.ts` and `provenance.ts` in both the lint's row and here (m17 of round 4: the lint named
   * `decode.ts` and this test did not); a file named here that does not name it is no failure, one
   * that names it and is not listed (and not allowed) is. `request-half.test.ts` (d) holds the lint's
   * row over every file of `src/api/`. */
  const ALLOWED: Readonly<Record<string, readonly string[]>> = { textOf: ["src/api/decode.ts"] };

  it.each([
    ["box.ts", "box", ["src/api/box.ts", "src/api/decode.ts"]],
    ["box.ts", "textOf", ["src/api/box.ts", "src/api/numbers.ts", "src/api/provenance.ts"]],
    ["numbers.ts", "glyphsOf", ["src/api/format.tsx", "src/api/numbers.ts"]],
    ["numbers.ts", "countGlyphs", ["src/api/format.tsx", "src/api/numbers.ts"]],
    ["numbers.ts", "remainingOf", ["src/api/format.tsx", "src/api/numbers.ts"]],
    ["numbers.ts", "isCountOf", ["src/api/document.ts", "src/api/numbers.ts"]],
    ["ids.ts", "requestId", ["src/api/ids.ts", "src/api/provenance.ts"]],
    ["origin.ts", "stamp", ["src/api/origin.ts", "src/api/provenance.ts"]],
    ["origin.ts", "originOf", ["src/api/origin.ts", "src/api/provenance.ts"]],
    ["provenance.ts", "recorder", ["src/api/client.ts", "src/api/provenance.ts"]],
    ["provenance.ts", "idAt", ["src/api/curator.ts", "src/api/provenance.ts"]],
    ["route.ts", "urlOf", ["src/api/client.ts", "src/api/route.ts"]],
    ["route.ts", "datasetOf", ["src/api/client.ts", "src/api/route.ts"]],
    ["decode.ts", "decode", ["src/api/client.ts", "src/api/decode.ts"]],
    ["decode.ts", "revive", ["src/api/decode.ts"]],
    ["decode.ts", "decodedCount", ["src/api/decode.ts", "src/api/oracle.ts", "src/harness/Harness.tsx"]],
    ["client.ts", "send", ["src/api/client.ts", "src/api/curator.ts"]],
    ["client.ts", "exchange", ["src/api/client.ts", "src/api/oracle.ts", "src/api/typed.ts"]],
    ["box.ts", "boxText", ["src/api/box.ts", "src/api/typed.ts"]],
    ["box.ts", "boxMap", ["src/api/box.ts", "src/api/typed.ts"]],
    ["box.ts", "readText", ["src/api/box.ts", "src/api/text.tsx"]],
    ["typed.ts", "walkComponent", ["src/api/oracle.ts", "src/api/typed.ts"]],
    ["visible.ts", "visibleForm", ["src/api/text.tsx", "src/api/visible.ts"]],
    ["visible.ts", "ENGINE", ["src/api/text.tsx", "src/api/visible.ts"]],
    ["curator.ts", "createCurator", ["src/api/curator.ts", "src/api/operator.ts"]],
    ["text.tsx", "choiceFor", ["src/api/document.ts", "src/api/text.tsx"]],
    ["text.tsx", "choiceText", ["src/api/document.ts", "src/api/text.tsx"]],
    ["text.tsx", "ofTable", ["src/api/document.ts", "src/api/text.tsx"]],
    ["typed.ts", "walkedAs", ["src/api/document.ts", "src/api/typed.ts"]],
    ["typed.ts", "validateDocument", ["src/api/document.ts", "src/api/typed.ts"]],
    ["typed.ts", "countCohort", ["src/api/document.ts", "src/api/typed.ts"]],
    ["typed.ts", "runAnalysis", ["src/api/document.ts", "src/api/typed.ts"]],
    ["datatypes.ts", "DATATYPES", ["src/api/datatypes.ts", "src/api/document.ts"]],
    ["datatypes.ts", "LEFT_OUT", ["src/api/datatypes.ts"]],
    ["operator.ts", "operator", ["src/api/operator.ts", "src/harness/Harness.tsx", "src/operator/Shell.tsx"]],
  ])("%s's %s", (file, name, expected) => {
    const allowed = ALLOWED[name] ?? [];
    expect(namers(declared(file, name)).filter((named) => !allowed.includes(named) || expected.includes(named))).toEqual(expected);
  });

  it("textOf's owners are decode.ts, provenance.ts and numbers.ts (D430): the files that name it are among them, and provenance.ts and numbers.ts its callers (m17 of round 4)", () => {
    const named = namers(declared("box.ts", "textOf"));
    expect(named.filter((file) => file !== "src/api/box.ts" && !["src/api/decode.ts", "src/api/provenance.ts", "src/api/numbers.ts"].includes(file))).toEqual([]);
    expect(named).toContain("src/api/provenance.ts");
    expect(named).toContain("src/api/numbers.ts");
  });
});

/** An export's declared type, of a module of `src/api/` of a program. */
function declaredType(prog: ts.Program, chk: ts.TypeChecker, file: string, name: string): ts.Type {
  const source = prog.getSourceFile(path.join(API, file));
  const module = source === undefined ? undefined : chk.getSymbolAtLocation(source);
  const found = module === undefined ? undefined : chk.getExportsOfModule(module).find((member) => member.name === name);
  if (found === undefined) {
    throw new Error(`${file} exports no ${name}`);
  }
  return chk.getDeclaredTypeOfSymbol((found.flags & ts.SymbolFlags.Alias) !== 0 ? chk.getAliasedSymbol(found) : found);
}

/** The one call signature of a function a module of `src/api/` exports. */
function signatureOf(prog: ts.Program, chk: ts.TypeChecker, file: string, name: string): ts.Signature {
  const source = prog.getSourceFile(path.join(API, file));
  const module = source === undefined ? undefined : chk.getSymbolAtLocation(source);
  const found = module === undefined ? undefined : chk.getExportsOfModule(module).find((member) => member.name === name);
  const [signature, ...more] = found === undefined ? [] : chk.getTypeOfSymbol(found).getCallSignatures();
  if (signature === undefined || more.length > 0) {
    throw new Error(`${file}'s ${name} is no function of one signature`);
  }
  return signature;
}

/** The `value` of an `Outcome`'s `ok` arm, from a typed function's return type. */
export function okValueOf(chk: ts.TypeChecker, returned: ts.Type): ts.Type | undefined {
  const [outcome] = chk.getTypeArguments(returned as ts.TypeReference);
  const arms = outcome === undefined ? [] : outcome.isUnion() ? outcome.types : [outcome];
  const ok = arms.find((arm) => {
    const kind = chk.getPropertyOfType(arm, "kind");
    return kind !== undefined && chk.typeToString(chk.getTypeOfSymbol(kind)) === '"ok"';
  });
  const value = ok === undefined ? undefined : chk.getPropertyOfType(ok, "value");
  return value === undefined ? undefined : chk.getTypeOfSymbol(value);
}

/** The element type of `runAnalysis`' ok value's `results` (D434: `valuesOf`'s parameter), by the
 * checker's own instantiation. */
export function resultOf(prog: ts.Program, chk: ts.TypeChecker): ts.Type | undefined {
  const answer = okValueOf(chk, signatureOf(prog, chk, "typed.ts", "runAnalysis").getReturnType());
  const results = answer === undefined ? undefined : chk.getPropertyOfType(answer, "results");
  const list = results === undefined ? undefined : chk.getTypeOfSymbol(results);
  return list === undefined ? undefined : chk.getIndexTypeOfType(list, ts.IndexKind.Number);
}

/**
 * The signatures pinned by the identity of their types (minor 4 of the seam's plan round 3): the
 * document seam's four print multi-KB (the walked answer types, expanded), so each is written as the
 * short text below when its one parameter and its return are the very types named (by type-object
 * identity in the program's checker: `optionsOf`'s parameter is `describeDataset`'s ok value, a
 * `*Draft`'s is `document.ts`' `Draft` and its return is `typed.ts`' own function's), and as the
 * checker's text otherwise, which no pin equals.
 */
export function signatureText(prog: ts.Program, chk: ts.TypeChecker, name: string, signature: ts.Signature): string {
  const checked = chk.signatureToString(signature, undefined, ts.TypeFormatFlags.NoTruncation, ts.SignatureKind.Call);
  const [parameter, ...more] = signature.getParameters();
  if (parameter === undefined || more.length > 0 || (signature.typeParameters?.length ?? 0) > 0 || signature.thisParameter !== undefined) {
    return checked;
  }
  const taken = chk.getTypeOfSymbol(parameter);
  const given = signature.getReturnType();
  const identities: Readonly<Record<string, () => readonly [ts.Type | undefined, ts.Type, string]>> = {
    optionsOf: () => [okValueOf(chk, signatureOf(prog, chk, "typed.ts", "describeDataset").getReturnType()), declaredType(prog, chk, "document.ts", "Options"), '(answer: Answered<"describe_dataset">): Options'],
    validateDraft: () => [declaredType(prog, chk, "document.ts", "Draft"), signatureOf(prog, chk, "typed.ts", "validateDocument").getReturnType(), '(draft: Draft): Promise<Outcome<Answered<"validate_document">>>'],
    countDraft: () => [declaredType(prog, chk, "document.ts", "Draft"), signatureOf(prog, chk, "typed.ts", "countCohort").getReturnType(), '(draft: Draft): Promise<Outcome<Answered<"count_cohort">>>'],
    runDraft: () => [declaredType(prog, chk, "document.ts", "Draft"), signatureOf(prog, chk, "typed.ts", "runAnalysis").getReturnType(), '(draft: Draft): Promise<Outcome<Answered<"run_analysis">>>'],
    valuesOf: () => [resultOf(prog, chk), declaredType(prog, chk, "typed.ts", "ValuesRead"), '(result: Answered<"run_analysis">["results"][number]): ValuesRead'],
  };
  const identity = Object.hasOwn(identities, name) ? identities[name] : undefined;
  if (identity === undefined) {
    return checked;
  }
  const [parameterType, returnType, text] = identity();
  return parameterType !== undefined && taken === parameterType && given === returnType && !(parameter.valueDeclaration !== undefined && ts.isParameter(parameter.valueDeclaration) && (parameter.valueDeclaration.questionToken !== undefined || parameter.valueDeclaration.dotDotDotToken !== undefined || parameter.valueDeclaration.initializer !== undefined))
    ? text
    : checked;
}

/** The exports of `src/api/index.ts`, reviewed: a new one fails here until it is added. */
const EXPORTS = [
  "Answered",
  "Boxed",
  "BoxedJson",
  "BoxedMap",
  "BoxedRefusals",
  "BoxedSegment",
  "Choice",
  "ClientError",
  "CohortDraft",
  "ColumnRead",
  "DatasetId",
  "DecodeError",
  "Draft",
  "Facet",
  "GrammarName",
  "InvalidCause",
  "JsonIn",
  "JsonInNull",
  "Kind",
  "LISTED",
  "LeafDraft",
  "ListShown",
  "Listed",
  "Lists",
  "Member",
  "NOT_READ",
  "Num",
  "OperationId",
  "Option",
  "Options",
  "Outcome",
  "REFUSALS",
  "RESPONSE_CAP_BYTES",
  "Refused",
  "RequestBody",
  "RetryAfter",
  "Role",
  "Route",
  "SHAPE_MISMATCH",
  "ServerNumber",
  "ServerText",
  "TypedOperation",
  "UNREACHABLE",
  "UnitOption",
  "ValuesRead",
  "VocabularyName",
  "compareText",
  "components",
  "countDraft",
  "describeDataset",
  "entriesOf",
  "explain",
  "idOf",
  "idOfAddress",
  "isPositive",
  "isServerNumber",
  "isZero",
  "kindOf",
  "largestOf",
  "listAnalyses",
  "listOf",
  "memberOf",
  "operations",
  "optionsOf",
  "paths",
  "routes",
  "runDraft",
  "sameText",
  "searchCatalog",
  "showText",
  "start",
  "validateDraft",
  "valuesOf",
];

describe("the surface walk's leaves: the two boxes' methods take no parameter (round 1 of #117)", () => {
  /** The members of a class's instances (its private fields apart) that are not a function of no
   * parameter, by the checker: the surface walk treats a box as a leaf (`surface.ts`), so an input a
   * callback is held in (`map(f)`, `then(f)`) would be one the walk never sees. */
  function takesSomething(on: ts.TypeChecker, owner: ts.Type): string[] {
    return on.getPropertiesOfType(owner).flatMap((member) => {
      const declaration = member.valueDeclaration ?? member.declarations?.[0];
      if (member.getName().startsWith("#") || declaration === undefined || ts.isPrivateIdentifier(ts.getNameOfDeclaration(declaration) ?? declaration)) {
        return [];
      }
      const signatures = on.getSignaturesOfType(on.getTypeOfSymbolAtLocation(member, declaration), ts.SignatureKind.Call);
      return signatures.length === 0 || signatures.some((signature) => signature.getParameters().length > 0) ? [member.getName()] : [];
    });
  }

  it.each(["ServerNumber", "ServerText"] as const)("%s: every member is a method of no parameter, by the type", (name) => {
    expect(takesSomething(checker, checker.getDeclaredTypeOfSymbol(exported("box.ts", name)))).toEqual([]);
  });

  it("holds at run time too: each method of each box's prototype declares no parameter", () => {
    for (const box of [ServerNumber, ServerText]) {
      const found = Reflect.ownKeys(box.prototype).filter((key) => key !== "constructor");
      expect(found.length).toBeGreaterThanOrEqual(5);
      for (const key of found) {
        const method: unknown = Reflect.get(box.prototype, key);
        expect(typeof method, String(key)).toBe("function");
        expect(typeof method === "function" ? method.length : -1, `${box.name}.${String(key)}`).toBe(0);
      }
    }
  });

  it("sees a box method that takes a callback, or a property that is no method (the check's own controls)", () => {
    const checked = check({
      "src/probe-box.ts": [
        "export class Boxy {",
        "  each(take: (value: string) => void): void {",
        "    take('x');",
        "  }",
        "  readonly held: string = 'x';",
        "  fine(): never {",
        "    throw new TypeError('x');",
        "  }",
        "}",
        "",
      ].join("\n"),
    });
    const source = checked.source("src/probe-box.ts");
    const declaration = source.statements.find(ts.isClassDeclaration);
    const symbol = declaration?.name === undefined ? undefined : checked.checker.getSymbolAtLocation(declaration.name);
    const owner = symbol === undefined ? undefined : checked.checker.getDeclaredTypeOfSymbol(symbol);
    expect(owner).toBeDefined();
    if (owner !== undefined) {
      const found = takesSomething(checked.checker, owner);
      expect(found.sort()).toEqual(["each", "held"]);
    }
  });
});

describe("src/api/index.ts", () => {
  const exports = (): ts.Symbol[] => {
    const source = program.getSourceFile(INDEX);
    const symbol = source === undefined ? undefined : checker.getSymbolAtLocation(source);
    if (symbol === undefined) {
      throw new Error("no index");
    }
    return checker.getExportsOfModule(symbol);
  };

  it("exports the reviewed list, and no internal", () => {
    const found = exports();
    expect(found.map((symbol) => symbol.name).sort()).toEqual(EXPORTS);
    const internals = [
      declared("box.ts", "box"),
      declared("box.ts", "textOf"),
      declared("decode.ts", "decode"),
      declared("decode.ts", "revive"),
      declared("decode.ts", "decodedCount"),
      declared("client.ts", "readCapped"),
      declared("client.ts", "send"),
      declared("ids.ts", "requestId"),
      declared("origin.ts", "stamp"),
      declared("origin.ts", "originOf"),
      declared("provenance.ts", "recorder"),
      declared("provenance.ts", "idAt"),
      declared("route.ts", "urlOf"),
      declared("route.ts", "datasetOf"),
      declared("client.ts", "exchange"),
      declared("box.ts", "boxText"),
      declared("box.ts", "boxMap"),
      declared("box.ts", "readText"),
      declared("typed.ts", "walkComponent"),
      declared("visible.ts", "visibleForm"),
      declared("visible.ts", "ENGINE"),
      declared("numbers.ts", "glyphsOf"),
      declared("numbers.ts", "countGlyphs"),
      declared("numbers.ts", "remainingOf"),
      declared("numbers.ts", "isCountOf"),
      declared("text.tsx", "choiceFor"),
      declared("text.tsx", "choiceText"),
      declared("text.tsx", "ofTable"),
      declared("typed.ts", "walkedAs"),
      declared("typed.ts", "validateDocument"),
      declared("typed.ts", "countCohort"),
      declared("typed.ts", "runAnalysis"),
      declared("datatypes.ts", "DATATYPES"),
      declared("datatypes.ts", "LEFT_OUT"),
    ];
    for (const symbol of found) {
      const target = (symbol.flags & ts.SymbolFlags.Alias) !== 0 ? checker.getAliasedSymbol(symbol) : symbol;
      for (const declaration of target.declarations ?? []) {
        expect(internals.includes(declaration), symbol.name).toBe(false);
      }
    }
  });

  /** The one export that takes `unknown`, by design: a type guard, which answers a boolean and
   * hands back nothing (`isServerNumber`), pinned by its exact signature (any other signature of
   * the name is checked like the rest). */
  const GUARDS: Readonly<Record<string, string>> = { isServerNumber: "(value: unknown): value is ServerNumber" };

  /** The exports that take a server number by design (D430), each pinned by its exact signature with
   * its reason: what they give is an element that shows a number, a boolean of one number, or a key
   * of one map, never a number or its text, and none compares two numbers given apart (below). */
  const NUMBER_PINS: Readonly<Record<string, readonly [string, string]>> = {
    Num: ["({ value }: { readonly value: ServerNumber; }): ReactElement<unknown, string | JSXElementConstructor<any>>", "shows a server number by its exact source text: an element whose props hold the box, never its digits"],
    isZero: ["(number: ServerNumber): boolean", "a sanctioned predicate (Q4 of D427): whether one shown number is zero, a boolean"],
    isPositive: ["(number: ServerNumber): boolean", "a sanctioned predicate (Q4 of D427): whether one shown number is above zero, a boolean"],
    largestOf: [
      "(map: BoxedMap<ServerNumber> | null): ServerText | null",
      "a sanctioned predicate (Q4 of D427): the key of one map's largest count, a box the server sent, never a number; a map with a null (suppressed) member is refused by type",
    ],
    RetryAfter: ["({ refused }: { readonly refused: Refused; }): ReactElement<unknown, string | JSXElementConstructor<any>> | null", "a refusal's Retry-After, as an element: the client's parse of a header, never a server number"],
    valuesOf: [
      '(result: Answered<"run_analysis">["results"][number]): ValuesRead',
      "the values seam (D434): it reads no number of the result; it gives the result's own values, walked when this seam walked the answer, boxes the server sent regrouped by the server's own kind, and two page words, never a number or its text (pinned by identity: signatureText)",
    ],
    optionsOf: [
      '(answer: Answered<"describe_dataset">): Options',
      "the document seam (D432): it reads, of the describe answer's numbers, whether the disclosure's min_cell_count is null and whether columns_total is the number of columns the answer lists, two booleans of the page's lists, and gives choices, boxes the server sent and booleans, never a number or its text (pinned by identity: signatureText)",
    ],
  };

  /** The takers among everything a module exports as a function (a namespace's included), with how
   * many functions were looked at, how many were the pinned guards, and the generic ones, which are
   * judged instantiated at every member of their constraint (`request-half.test.ts` (b)): a
   * generic's parameters are its type parameter's, which `takes` does not see through. */
  function exportTakers(prog: ts.Program, chk: ts.TypeChecker, root: ts.Symbol): { takers: string[]; functions: number; guards: number; generics: string[] } {
    const takes_ = takes(prog, chk);
    const takers: string[] = [];
    const generics: string[] = [];
    let functions = 0;
    let guards = 0;
    const inspect = (symbol: ts.Symbol, name: string): void => {
      const target = (symbol.flags & ts.SymbolFlags.Alias) !== 0 ? chk.getAliasedSymbol(symbol) : symbol;
      if ((target.flags & ts.SymbolFlags.Module) !== 0) {
        chk.getExportsOfModule(target).forEach((member) => {
          inspect(member, `${name}.${member.name}`);
        });
        return;
      }
      if ((target.flags & ts.SymbolFlags.Value) === 0) {
        return;
      }
      const type = chk.getTypeOfSymbol(target);
      const signatures = [...type.getCallSignatures(), ...type.getConstructSignatures()];
      for (const signature of signatures) {
        functions += 1;
        const written = signatureText(prog, chk, name, signature);
        if (GUARDS[name] === written || NUMBER_PINS[name]?.[0] === written) {
          guards += 1;
          continue;
        }
        if ((signature.typeParameters?.length ?? 0) > 0) {
          generics.push(name);
          continue;
        }
        for (const parameter of signature.getParameters()) {
          if (takes_(chk.getTypeOfSymbol(parameter))) {
            takers.push(`${name}(${parameter.name})`);
          }
        }
      }
    };
    chk.getExportsOfModule(root).forEach((symbol) => {
      inspect(symbol, symbol.name);
    });
    return { takers, functions, guards, generics };
  }

  it("exports no function that takes anything a ServerNumber can be", () => {
    const source = program.getSourceFile(INDEX);
    const root = source === undefined ? undefined : checker.getSymbolAtLocation(source);
    if (root === undefined) {
      throw new Error("no index");
    }
    const { takers, functions, guards, generics } = exportTakers(program, checker, root);
    expect(takers).toEqual([]);
    // `BoxedMap` and `Choice` (D432) are exported as types alone; their classes' (private)
    // constructors are generic.
    expect(generics.sort()).toEqual(["BoxedMap", "Choice", "entriesOf", "kindOf", "memberOf"]);
    expect(guards).toBe(Object.keys(GUARDS).length + Object.keys(NUMBER_PINS).length);
    expect(functions).toBeGreaterThan(30);
  });

  it.each([
    ["a guard whose signature is another (it answers a string)", "export function isServerNumber(value: unknown): string {", ["isServerNumber(value)"]],
    ["a guard that takes more than the pinned signature", "export function isServerNumber(value: unknown, more?: object): value is ServerNumber {", ["isServerNumber(value)", "isServerNumber(more)"]],
    ["the pinned guard itself", "export function isServerNumber(value: unknown): value is ServerNumber {", []],
  ])("pins the one unknown-taker by its exact signature, not by its name: %s", (_case, declaration, expected) => {
    const original = readFileSync(path.join(API, "box.ts"), "utf8");
    const from = "export function isServerNumber(value: unknown): value is ServerNumber {";
    expect(original).toContain(from);
    const checked = check({ "src/api/box.ts": original.replace(from, declaration), "src/probe-guard.ts": "export * from './api';\n" });
    const root = checked.checker.getSymbolAtLocation(checked.source("src/probe-guard.ts"));
    if (root === undefined) {
      throw new Error("no probe module");
    }
    expect(exportTakers(checked.program, checked.checker, root).takers).toEqual(expected);
  });

  /** The most parameters of one of each export's signatures that can be handed a server number
   * (`takes`), generics left out. */
  function numberParameters(prog: ts.Program, chk: ts.TypeChecker, root: ts.Symbol): Map<string, number> {
    const takes_ = takes(prog, chk);
    const found = new Map<string, number>();
    for (const symbol of chk.getExportsOfModule(root)) {
      const target = (symbol.flags & ts.SymbolFlags.Alias) !== 0 ? chk.getAliasedSymbol(symbol) : symbol;
      if ((target.flags & ts.SymbolFlags.Value) === 0) {
        continue;
      }
      const type = chk.getTypeOfSymbol(target);
      for (const signature of [...type.getCallSignatures(), ...type.getConstructSignatures()]) {
        // A generic is judged instantiated elsewhere (`request-half.test.ts` (b)), as `exportTakers` does.
        if ((signature.typeParameters?.length ?? 0) > 0) {
          continue;
        }
        const count = signature.getParameters().filter((parameter) => takes_(chk.getTypeOfSymbol(parameter))).length;
        found.set(symbol.name, Math.max(found.get(symbol.name) ?? 0, count));
      }
    }
    return found;
  }

  it("D430: the exports that take a server number are NUMBER_PINS' and the guard, each takes one, and none compares two numbers given apart", () => {
    const source = program.getSourceFile(INDEX);
    const root = source === undefined ? undefined : checker.getSymbolAtLocation(source);
    if (root === undefined) {
      throw new Error("no index");
    }
    const taking = [...numberParameters(program, checker, root)].filter(([, count]) => count > 0);
    expect(taking.map(([name]) => name).sort()).toEqual([...Object.keys(NUMBER_PINS), ...Object.keys(GUARDS)].sort());
    expect(taking.filter(([, count]) => count !== 1)).toEqual([]);
    for (const [name, [signature, reason]] of Object.entries(NUMBER_PINS)) {
      expect(EXPORTS, name).toContain(name);
      expect(signature, name).not.toBe("");
      expect(reason, name).not.toBe("");
    }
  });

  it("D430: would see an export that compares two numbers, and one pinned under another signature (the check's own controls)", () => {
    const checked = check({ "src/probe-compare.ts": "import type { ServerNumber } from './api';\nexport * from './api';\nexport function above(a: ServerNumber, b: ServerNumber): boolean { return a === b; }\n" });
    const root = checked.checker.getSymbolAtLocation(checked.source("src/probe-compare.ts"));
    if (root === undefined) {
      throw new Error("no probe module");
    }
    expect(numberParameters(checked.program, checked.checker, root).get("above")).toBe(2);
    expect(exportTakers(checked.program, checked.checker, root).takers).toEqual(["above(a)", "above(b)"]);
    const original = readFileSync(path.join(API, "numbers.ts"), "utf8");
    const from = "export function isZero(number: ServerNumber): boolean {";
    expect(original).toContain(from);
    const widened = check({ "src/api/numbers.ts": original.replace(from, "export function isZero(number: ServerNumber, other?: ServerNumber): boolean {"), "src/probe-pin.ts": "export * from './api';\n" });
    const pinned = widened.checker.getSymbolAtLocation(widened.source("src/probe-pin.ts"));
    if (pinned === undefined) {
      throw new Error("no probe module");
    }
    expect(exportTakers(widened.program, widened.checker, pinned).takers).toEqual(["isZero(number)", "isZero(other)"]);
  });

  it("D430: largestOf refuses a map with a null (suppressed) member, by type, and takes a map of numbers or null", () => {
    const probe = [
      "import type { BoxedMap, ServerNumber } from './api';",
      "import { largestOf } from './api';",
      "declare const counts: BoxedMap<ServerNumber>;",
      "declare const withheld: BoxedMap<ServerNumber | null>;",
      "export const a = largestOf(counts);",
      "export const b = largestOf(null);",
      "export const c = largestOf(withheld);",
      "",
    ].join("\n");
    const checked = check({ "src/probe-largest.ts": probe });
    const found = checked.diagnostics("src/probe-largest.ts").map((diagnostic) => [diagnostic.code, ts.getLineAndCharacterOfPosition(checked.source("src/probe-largest.ts"), diagnostic.start ?? 0).line]);
    expect(found).toEqual([[2345, 6]]);
  });

  it("re-exports nothing of the operator client: no specifier of index.ts resolves to operator.ts or curator.ts (D423)", () => {
    const source = program.getSourceFile(INDEX);
    expect(source).toBeDefined();
    const targets = source === undefined ? [] : specifiers(source).map((specifier) => resolved(specifier, INDEX));
    expect(targets).not.toContain(OPERATOR);
    expect(targets).not.toContain(path.join(API, "curator.ts"));
    expect(targets).toContain(path.join(API, "client.ts"));
  });

  it("src/api/operator.ts exports the reviewed list, and the operator client holds to its allow-list: nothing it is handed but a primitive or a pinned type at its pinned place (D423, D425: surface.ts, request-half.test.ts (a))", () => {
    const source = program.getSourceFile(OPERATOR);
    const symbol = source === undefined ? undefined : checker.getSymbolAtLocation(source);
    if (symbol === undefined) {
      throw new Error("no operator.ts");
    }
    expect(checker.getExportsOfModule(symbol).map((one) => one.name).sort()).toEqual(["Edit", "IDLE_LIMIT_MS", "LockReason", "NAME_LIMIT", "Outcome", "Unlocked", "View", "operator"]);
    const walked = surface(program, checker);
    expect(walked.problems).toEqual([]);
    expect(walked.members).toBe(18);
  });

  it("would see a function that takes one (the check's own control)", () => {
    const [signature] = checker.getTypeOfSymbol(exported("box.ts", "textOf")).getCallSignatures();
    const [parameter] = signature?.getParameters() ?? [];
    expect(parameter !== undefined && taker(checker.getTypeOfSymbol(parameter))).toBe(true);
  });
});

// ---------------------------------------------------------------------------------------------

/** The modules a module outside `src/api/` may import (the app's import rules: the index; the
 * operator entry's `operator.ts`; the harness's `oracle.ts` and `operator.ts`). */
const DOORS = ["src/api/index.ts", "src/api/operator.ts", "src/api/oracle.ts"];

/** Each value an exported module gives, by its name (a namespace's members by theirs). */
function values(prog: ts.Program, chk: ts.TypeChecker, file: string): { name: string; type: ts.Type; symbol: ts.Symbol }[] {
  const source = prog.getSourceFile(path.join(WEB, file));
  const root = source === undefined ? undefined : chk.getSymbolAtLocation(source);
  if (root === undefined) {
    throw new Error(`no ${file}`);
  }
  const found: { name: string; type: ts.Type; symbol: ts.Symbol }[] = [];
  const inspect = (symbol: ts.Symbol, name: string): void => {
    // An export of a type alone (`export type { BoxedMap }`) is no value at run time.
    if ((symbol.declarations ?? []).some((declaration) => ts.isTypeOnlyExportDeclaration(declaration) || ts.isTypeOnlyImportDeclaration(declaration))) {
      return;
    }
    const target = (symbol.flags & ts.SymbolFlags.Alias) !== 0 ? chk.getAliasedSymbol(symbol) : symbol;
    if ((target.flags & ts.SymbolFlags.Module) !== 0) {
      chk.getExportsOfModule(target).forEach((member) => {
        inspect(member, `${name}.${member.name}`);
      });
      return;
    }
    if ((target.flags & ts.SymbolFlags.Value) !== 0) {
      found.push({ name, type: chk.getTypeOfSymbol(target), symbol: target });
    }
  };
  chk.getExportsOfModule(root).forEach((symbol) => {
    inspect(symbol, symbol.name);
  });
  return found;
}

/**
 * The index's takers of a server text (D426): each function a `ServerText` or a `BoxedMap` can be
 * handed to, pinned by its exact signature with its reason; any other is a failure (a function that
 * takes two boxes, or one, and gives anything is reviewed here first).
 */
const TEXT_PINS: Readonly<Record<string, readonly [string, string]>> = {
  isServerNumber: ["(value: unknown): value is ServerNumber", "the number box's brand check: a boolean"],
  kindOf: ["<K extends VocabularyName>(text: ServerText, vocabulary: K): Kind<K>", "a generated vocabulary's own member (a literal of the code), or null; Severity fail-closed"],
  idOf: ["(text: ServerText): DatasetId | null", "a DatasetId, whose generated grammar bounds its characters"],
  optionsOf: [
    '(answer: Answered<"describe_dataset">): Options',
    "the document seam (D432): opaque choices of a walked describe answer's ids (holding the boxes), the answer's own boxes as labels, its DatasetId and booleans of the page's lists (pinned by identity: signatureText)",
  ],
  validateDraft: [
    '(draft: Draft): Promise<Outcome<Answered<"validate_document">>>',
    "the document seam (D432): a Draft's choices hold their boxes as ES private members, read by documentOf alone at their role's positions; the answer is typed.ts' walk (pinned by identity: signatureText)",
  ],
  countDraft: [
    '(draft: Draft): Promise<Outcome<Answered<"count_cohort">>>',
    "the document seam (D432): a Draft's choices hold their boxes as ES private members, read by documentOf alone at their role's positions; the answer is typed.ts' walk (pinned by identity: signatureText)",
  ],
  runDraft: [
    '(draft: Draft): Promise<Outcome<Answered<"run_analysis">>>',
    "the document seam (D432): a Draft's choices hold their boxes as ES private members, read by documentOf alone at their role's positions; the answer is typed.ts' walk (pinned by identity: signatureText)",
  ],
  valuesOf: [
    '(result: Answered<"run_analysis">["results"][number]): ValuesRead',
    "the values seam (D434): a result's values, read only from a result the typed answers gave (a module WeakMap), its column labels the canonical document's boxes; no text is read (pinned by identity: signatureText)",
  ],
  sameText: ["(a: ServerText, b: ServerText): boolean", "the sanctioned equality: a boolean"],
  compareText: ["(a: ServerText, b: ServerText): number", "the sanctioned order: a number (an ordering oracle for deliberate code, D426's residual)"],
  entriesOf: ["<V>(map: BoxedMap<V>): readonly (readonly [ServerText, V])[]", "a map's entries, keys boxed"],
  memberOf: ["<V>(map: BoxedMap<V>, name: string): Member<V>", "a member's value by the page's own word, or found: false"],
  largestOf: [
    "(map: BoxedMap<ServerNumber> | null): ServerText | null",
    "a sanctioned predicate (D430, Q4 of D427): the key of one map's largest count, a box the server sent (a NUMBER_PINS row too)",
  ],
  listOf: [
    "(hit: Lists, at: Listed): ListShown",
    "one of D312's lists alone (D430), by position (a facet named by its key, compared by sameText), found once: its first LISTED members, boxes the server sent, and \"and N more\", an element of a count the page made of what the server sent",
  ],
  RetryAfter: [
    "({ refused }: { readonly refused: Refused; }): ReactElement<unknown, string | JSXElementConstructor<any>> | null",
    "a refusal's Retry-After (D430), as an element (a NUMBER_PINS row too): it reads no text of the refusals",
  ],
  showText: [
    "(text: ServerText): ReactElement<unknown, string | JSXElementConstructor<any>>",
    "the one door from a server text to the DOM (D428): an element whose props hold the box, never its text, rendering the visible form (D312) as the position's form asks",
  ],
};

/**
 * What the exports of the doors may give that is a raw string or a door (`takers.ts`' `gives`), each
 * path pinned with its reason; any other path fails the gate (m1, m2 of the typed answers' round 4).
 */
const OUTPUT_PINS: Readonly<Record<string, string>> = {
  "index.ts:ClientError(): Error": "the client's own refusal: its message is REFUSALS' fixed words, its name a constant",
  "index.ts:DecodeError(): Error": "the decoder's refusal: its message is UNDECODABLE's fixed words",
  "index.ts:idOf(): DatasetId": "a dataset id, a string whose generated grammar bounds its characters (the brand proves form, not provenance)",
  "index.ts:optionsOf().dataset: DatasetId":
    "the describe answer's dataset id (D432), idOf's: a string whose generated grammar bounds its characters, which a Draft hands back as its dataset (documentOf holds it to the one its choices were minted from)",
  "index.ts:idOfAddress(): DatasetId": "the address's dataset id (D428): the router module's door, idOf's generated grammar over a text of the address, so its characters are bounded and nothing else of the address is given back",
  "index.ts:Num(): ReactElement (an unlisted library type)":
    "shows a server number by its exact source text (D430): its props hold the box, never its digits, which a module-private context hands to the digits as they render; a detached root's textContent reads them by deliberate code (D419's boundary; react-dom/client entry-only, D429)",
  "index.ts:RetryAfter(): ReactElement (an unlisted library type)": "a refusal's Retry-After in whole seconds (D430): a count the client parsed from a header, shown, never a server number",
  "index.ts:listOf().more: ReactElement (an unlisted library type)":
    "\"and N more\" for D312's lists alone (D430): a count the page made of the members the server sent, every one disclosed, never of a list §8.4 governs",
  "index.ts:showText(): ReactElement (an unlisted library type)":
    "the one door from a server text to the DOM (D428): its props (unknown to the caller) hold the box, never its text, and it renders the visible form; a detached root's textContent reads that form's text by deliberate code (D419's boundary; react-dom/client entry-only in 2b-1L)",
  ...Object.fromEntries(
    ["acceptProposal", "change", "dataset", "datasets", "discard", "open", "publish", "queue", "queueAt", "rejectProposal", "takeOver", "withdraw"].flatMap((member) => [
      [`operator.ts:operator.${member}()<0>.body: string`, "the operator side stays raw (D426): no operator screen renders a body until the curation screens' plan reads it through the seam"],
      [`operator.ts:operator.${member}()<0>.codes<0>: string`, "the operator side stays raw (D426): a refusal's codes, read by the shell against its fixed vocabulary, rendered by no screen"],
    ]),
  ),
  "operator.ts:operator.unlock()<0>.codes<0>: string": "the operator side stays raw (D426): an unlock's refusal codes, read against the shell's fixed vocabulary",
};

describe("D426: the typed answers' doors", () => {
  it("the index's takers of a server text are exactly the pinned ones, each by its exact signature", () => {
    const takesBox = takesText(program, checker);
    const found: string[] = [];
    for (const { name, type } of values(program, checker, "src/api/index.ts")) {
      for (const signature of [...type.getCallSignatures(), ...type.getConstructSignatures()]) {
        if (signature.getParameters().some((parameter) => takesBox(checker.getTypeOfSymbol(parameter)))) {
          found.push(`${name} ${signatureText(program, checker, name, signature)}`);
        }
      }
    }
    expect(found.sort()).toEqual(Object.entries(TEXT_PINS).map(([name, [signature]]) => `${name} ${signature}`).sort());
  });

  it.each([
    ["optionsOf of another operation's answer", 'export function optionsOf(answer: Answered<"describe_dataset">): Options {', 'export function optionsOf(answer: Answered<"search_catalog">): Options {', "optionsOf"],
    ["optionsOf of the answer and more", 'export function optionsOf(answer: Answered<"describe_dataset">): Options {', 'export function optionsOf(answer: Answered<"describe_dataset">, notes?: string): Options {', "optionsOf"],
    ["validateDraft giving count_cohort's answer", 'export function validateDraft(draft: Draft): Promise<Outcome<Answered<"validate_document">>> {\n  return validateDocument(', 'export function validateDraft(draft: Draft): Promise<Outcome<Answered<"count_cohort">>> {\n  return countCohort(', "validateDraft"],
    ["runDraft of a draft and more", 'export function runDraft(draft: Draft): Promise<Outcome<Answered<"run_analysis">>> {', 'export function runDraft(draft: Draft & { readonly notes: string }): Promise<Outcome<Answered<"run_analysis">>> {', "runDraft"],
  ])("signatureText pins by identity: %s is no pinned text (its control)", (_case, from, to, name) => {
    const original = readFileSync(path.join(API, "document.ts"), "utf8");
    expect(original).toContain(from);
    const checked = check({ "src/api/document.ts": original.replace(from, to), "src/probe-identity.ts": "export * from './api';\n" });
    const signature = signatureOf(checked.program, checked.checker, "document.ts", name);
    const written = signatureText(checked.program, checked.checker, name, signature);
    expect(written).not.toBe(TEXT_PINS[name]?.[0]);
    expect(written).toBe(checked.checker.signatureToString(signature, undefined, ts.TypeFormatFlags.NoTruncation, ts.SignatureKind.Call));
    const real = signatureOf(program, checker, "document.ts", name);
    expect(signatureText(program, checker, name, real)).toBe(TEXT_PINS[name]?.[0]);
  });

  const VALUES_OF = 'export function valuesOf(result: Answered<"run_analysis">["results"][number]): ValuesRead {';
  it.each([
    ["valuesOf of a BoxedJson", VALUES_OF.replace('Answered<"run_analysis">["results"][number]', "BoxedJson"), false],
    ["valuesOf of a structural copy of the result type, declared apart", `type Copied = { readonly [K in keyof Answered<"run_analysis">["results"][number]]: Answered<"run_analysis">["results"][number][K] };\n${VALUES_OF.replace('Answered<"run_analysis">["results"][number]', "Copied")}`, false],
    ["valuesOf of a result or null", VALUES_OF.replace('Answered<"run_analysis">["results"][number]', 'Answered<"run_analysis">["results"][number] | null'), false],
    ["valuesOf giving another read", VALUES_OF.replace("): ValuesRead {", "): ValuesRead | null {"), false],
    ["CONTROL valuesOf through a local alias", `type Result = Answered<"run_analysis">["results"][number];\n${VALUES_OF.replace('Answered<"run_analysis">["results"][number]', "Result")}`, true],
  ])("signatureText pins valuesOf by identity (D434): %s", (_case, to, pinned) => {
    const original = readFileSync(path.join(API, "typed.ts"), "utf8");
    expect(original).toContain(VALUES_OF);
    const checked = check({ "src/api/typed.ts": original.replace(VALUES_OF, to), "src/probe-values.ts": "export * from './api';\n" });
    const signature = signatureOf(checked.program, checked.checker, "typed.ts", "valuesOf");
    expect(signatureText(checked.program, checked.checker, "valuesOf", signature) === TEXT_PINS["valuesOf"]?.[0]).toBe(pinned);
  });

  /** The doors pinned by identity (`signatureText`'s), each with the module that declares it: the
   * index's export of each must be that very declaration, through any alias (m1 of #128's round 2: a
   * pin by the declaring module's name alone let the index export another function of the same types). */
  const IDENTITY_DOORS: Readonly<Record<string, string>> = {
    optionsOf: "document.ts",
    validateDraft: "document.ts",
    countDraft: "document.ts",
    runDraft: "document.ts",
    valuesOf: "typed.ts",
  };

  /** The declaration the index's export of a name reaches, through every alias, as `file:name`. */
  function doorOf(prog: ts.Program, chk: ts.TypeChecker, name: string): string {
    const source = prog.getSourceFile(INDEX);
    const module = source === undefined ? undefined : chk.getSymbolAtLocation(source);
    const found = module === undefined ? undefined : chk.getExportsOfModule(module).find((member) => member.name === name);
    return declarationOf(prog, found !== undefined && (found.flags & ts.SymbolFlags.Alias) !== 0 ? chk.getAliasedSymbol(found) : found);
  }

  it("each door pinned by identity is, through the index, the declaration its pin names: every identity entry of TEXT_PINS", () => {
    const identity = Object.entries(TEXT_PINS).filter(([, [, reason]]) => reason.includes("pinned by identity")).map(([name]) => name);
    expect(identity.sort()).toEqual(Object.keys(IDENTITY_DOORS).sort());
    for (const [name, file] of Object.entries(IDENTITY_DOORS)) {
      expect(doorOf(program, checker, name), name).toBe(`src/api/${file}:${name}`);
    }
  });

  const INDEX_TEXT = readFileSync(INDEX, "utf8");
  it.each(Object.entries(IDENTITY_DOORS))("the index exporting another function of %s's very types under its name is refused, and a local alias of the real one passes (its controls)", (name, file) => {
    const declaring = readFileSync(path.join(API, file), "utf8");
    const listed = new RegExp(`\\b${name},`, "u");
    expect(INDEX_TEXT).toMatch(listed);
    const other = check({
      [`src/api/${file}`]: `${declaring}\nexport const ${name}Other: typeof ${name} = (...given) => ${name}(...given);\n`,
      "src/api/index.ts": INDEX_TEXT.replace(listed, `${name}Other as ${name},`),
    });
    expect(doorOf(other.program, other.checker, name)).toBe(`src/api/${file}:${name}Other`);
    const aliased = check({
      "src/api/index.ts": `${INDEX_TEXT.replace(listed, "")}import { ${name} as door } from "./${file.replace(/\.ts$/u, "")}";\nexport { door as ${name} };\n`,
    });
    expect(aliased.diagnostics("src/api/index.ts").map((diagnostic) => diagnostic.code)).toEqual([]);
    expect(doorOf(aliased.program, aliased.checker, name)).toBe(`src/api/${file}:${name}`);
  });

  it("no export of a module app code may import gives a raw string or a door, but the pinned ones", () => {
    const giving = gives(program, checker);
    const found = new Set(DOORS.flatMap((file) => values(program, checker, file).flatMap(({ name, type }) => giving(type, `${file.replace("src/api/", "")}:${name}`))));
    expect([...found].sort()).toEqual(Object.keys(OUTPUT_PINS).sort());
  });
});
