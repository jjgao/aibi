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
 *   in `src/api/box.ts` and `src/api/decode.ts`, `textOf` in `box.ts` and `provenance.ts`, where
 *   `idAt` is the one function that reads a box's text and `curator.ts` alone names it, D425), as
 *   are the request half's (`requestId`, `stamp`, `originOf`, `recorder`, `urlOf`, `datasetOf`);
 *   the decoder's (`decode`, `revive`) in
 *   `src/api/decode.ts` and `src/api/client.ts` alone; the counter (`decodedCount`) in
 *   `src/api/decode.ts`, `src/api/oracle.ts` and the harness alone.
 * - `src/api/index.ts` exports exactly the reviewed list, none of them an internal, and no exported
 *   function (a namespace's included) takes a parameter that can hold a `ServerNumber` (by the
 *   checker's assignability, `takers.ts`, never a name; the type guard `isServerNumber` alone is
 *   pinned, and a generic is judged instantiated, `request-half.test.ts` (b)): nothing it exports
 *   turns a server number into anything (a number, digits, a request's material).
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
import { surface } from "./surface";
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
    ["box.ts", "textOf", ["src/api/box.ts", "src/api/provenance.ts"]],
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
    ["operator.ts", "operator", ["src/api/operator.ts", "src/harness/Harness.tsx", "src/operator/Shell.tsx"]],
  ])("%s's %s", (file, name, expected) => {
    const allowed = ALLOWED[name] ?? [];
    expect(namers(declared(file, name)).filter((named) => !allowed.includes(named) || expected.includes(named))).toEqual(expected);
  });

  it("textOf's owners are decode.ts and provenance.ts: the files that name it are among them, and provenance.ts is its one caller (m17 of round 4)", () => {
    const named = namers(declared("box.ts", "textOf"));
    expect(named.filter((file) => file !== "src/api/box.ts" && !["src/api/decode.ts", "src/api/provenance.ts"].includes(file))).toEqual([]);
    expect(named).toContain("src/api/provenance.ts");
  });
});

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
  "DatasetId",
  "DecodeError",
  "GrammarName",
  "InvalidCause",
  "JsonIn",
  "JsonInNull",
  "Kind",
  "Member",
  "OperationId",
  "Outcome",
  "REFUSALS",
  "RESPONSE_CAP_BYTES",
  "RequestBody",
  "Route",
  "SHAPE_MISMATCH",
  "ServerNumber",
  "ServerText",
  "TypedOperation",
  "UNREACHABLE",
  "VocabularyName",
  "choiceOf",
  "compareText",
  "components",
  "countCohort",
  "describeDataset",
  "entriesOf",
  "explain",
  "idOf",
  "idOfAddress",
  "isServerNumber",
  "kindOf",
  "listAnalyses",
  "memberOf",
  "operations",
  "paths",
  "routes",
  "runAnalysis",
  "sameText",
  "searchCatalog",
  "showText",
  "start",
  "validateDocument",
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
        if (GUARDS[name] === chk.signatureToString(signature, undefined, ts.TypeFormatFlags.NoTruncation, ts.SignatureKind.Call)) {
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
    // `BoxedMap` is exported as a type alone; its class's (private) constructor is generic.
    expect(generics.sort()).toEqual(["BoxedMap", "entriesOf", "kindOf", "memberOf"]);
    expect(guards).toBe(Object.keys(GUARDS).length);
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
  choiceOf: ['(text: ServerText, grammar: "dataset" | "table" | "column" | "analysis"): Choice | null', "an opaque Choice holding the box"],
  sameText: ["(a: ServerText, b: ServerText): boolean", "the sanctioned equality: a boolean"],
  compareText: ["(a: ServerText, b: ServerText): number", "the sanctioned order: a number (an ordering oracle for deliberate code, D426's residual)"],
  entriesOf: ["<V>(map: BoxedMap<V>): readonly (readonly [ServerText, V])[]", "a map's entries, keys boxed"],
  memberOf: ["<V>(map: BoxedMap<V>, name: string): Member<V>", "a member's value by the page's own word, or found: false"],
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
  "index.ts:idOfAddress(): DatasetId": "the address's dataset id (D428): the router module's door, idOf's generated grammar over a text of the address, so its characters are bounded and nothing else of the address is given back",
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
          found.push(`${name} ${checker.signatureToString(signature, undefined, ts.TypeFormatFlags.NoTruncation, ts.SignatureKind.Call)}`);
        }
      }
    }
    expect(found.sort()).toEqual(Object.entries(TEXT_PINS).map(([name, [signature]]) => `${name} ${signature}`).sort());
  });

  it("no export of a module app code may import gives a raw string or a door, but the pinned ones", () => {
    const giving = gives(program, checker);
    const found = new Set(DOORS.flatMap((file) => values(program, checker, file).flatMap(({ name, type }) => giving(type, `${file.replace("src/api/", "")}:${name}`))));
    expect([...found].sort()).toEqual(Object.keys(OUTPUT_PINS).sort());
  });
});
