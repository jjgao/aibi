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
 * - The box's internals (`box`, `textOf`) are named, through any alias or namespace, in
 *   `src/api/box.ts` and `src/api/decode.ts` alone; the decoder's (`decode`, `revive`) in
 *   `src/api/decode.ts` and `src/api/client.ts` alone; the counter (`decodedCount`) in
 *   `src/api/decode.ts`, `src/api/oracle.ts` and the harness alone.
 * - `src/api/index.ts` exports exactly the reviewed list, none of them an internal, and no exported
 *   function (a namespace's included) takes a parameter that can hold a `ServerNumber`: nothing it
 *   exports turns a server number into anything (a number, digits, a request's material).
 */
import path from "node:path";

import ts from "typescript";
import { beforeAll, describe, expect, it } from "vitest";

import { isTooling } from "../../scripts/tooling.mjs";
import { appOptions, WEB } from "./program";

const SRC = path.join(WEB, "src");
const API = path.join(SRC, "api");
const INDEX = path.join(API, "index.ts");
const ORACLE = path.join(API, "oracle.ts");
const HARNESS = path.join(SRC, "harness");

let program: ts.Program;
let checker: ts.TypeChecker;

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

/** What a file outside `src/api/` may import of it: the index, and for the harness the oracle. */
function allowedTargets(file: string): string[] {
  return file.startsWith(`${HARNESS}/`) ? [INDEX, ORACLE] : [INDEX];
}

describe("outside src/api/, the client is reached through its index alone", () => {
  it("lets the harness, and no other file, reach the oracle's counter too", () => {
    expect(allowedTargets(path.join(HARNESS, "Harness.tsx"))).toEqual([INDEX, ORACLE]);
    expect(allowedTargets(path.join(SRC, "operator", "main.tsx"))).toEqual([INDEX]);
    expect(allowedTargets(path.join(SRC, "harnessed", "x.ts"))).toEqual([INDEX]);
  });

  it("every specifier that resolves into src/api/ resolves to index.ts (the harness's also to oracle.ts)", () => {
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
  it.each([
    ["box.ts", "box", ["src/api/box.ts", "src/api/decode.ts"]],
    ["box.ts", "textOf", ["src/api/box.ts"]],
    ["decode.ts", "decode", ["src/api/client.ts", "src/api/decode.ts"]],
    ["decode.ts", "revive", ["src/api/decode.ts"]],
    ["decode.ts", "decodedCount", ["src/api/decode.ts", "src/api/oracle.ts", "src/harness/Harness.tsx"]],
  ])("%s's %s", (file, name, allowed) => {
    expect(namers(declared(file, name))).toEqual(allowed);
  });
});

/** The exports of `src/api/index.ts`, reviewed: a new one fails here until it is added. */
const EXPORTS = [
  "Answer",
  "ClientError",
  "DecodeError",
  "JsonIn",
  "JsonInNull",
  "JsonOut",
  "OperationId",
  "REFUSALS",
  "RESPONSE_CAP_BYTES",
  "RequestBody",
  "Route",
  "ServerNumber",
  "components",
  "exchange",
  "isServerNumber",
  "operations",
  "paths",
  "routes",
  "start",
];

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
    const internals = [declared("box.ts", "box"), declared("box.ts", "textOf"), declared("decode.ts", "decode"), declared("decode.ts", "revive"), declared("decode.ts", "decodedCount"), declared("client.ts", "readCapped")];
    for (const symbol of found) {
      const target = (symbol.flags & ts.SymbolFlags.Alias) !== 0 ? checker.getAliasedSymbol(symbol) : symbol;
      for (const declaration of target.declarations ?? []) {
        expect(internals.includes(declaration), symbol.name).toBe(false);
      }
    }
  });

  /** Whether a type can hold a `ServerNumber`, at any depth (its members, elements, index). */
  function holdsServerNumber(type: ts.Type, seen = new Set<ts.Type>()): boolean {
    if (seen.has(type)) {
      return false;
    }
    seen.add(type);
    if ((type.flags & (ts.TypeFlags.Any | ts.TypeFlags.Unknown)) !== 0) {
      return false;
    }
    if (type.getSymbol()?.name === "ServerNumber") {
      return true;
    }
    if (type.isUnionOrIntersection()) {
      return type.types.some((member) => holdsServerNumber(member, seen));
    }
    if ((type.flags & ts.TypeFlags.Object) === 0) {
      return false;
    }
    if (checker.isArrayType(type) || checker.isTupleType(type)) {
      return checker.getTypeArguments(type as ts.TypeReference).some((member) => holdsServerNumber(member, seen));
    }
    const index = type.getStringIndexType();
    return (
      (index !== undefined && holdsServerNumber(index, seen)) ||
      checker.getPropertiesOfType(type).some((member) => holdsServerNumber(checker.getTypeOfSymbol(member), seen))
    );
  }

  it("exports no function that takes anything a ServerNumber can be", () => {
    const takers: string[] = [];
    let functions = 0;
    const inspect = (symbol: ts.Symbol, name: string): void => {
      const target = (symbol.flags & ts.SymbolFlags.Alias) !== 0 ? checker.getAliasedSymbol(symbol) : symbol;
      if ((target.flags & ts.SymbolFlags.Module) !== 0) {
        checker.getExportsOfModule(target).forEach((member) => {
          inspect(member, `${name}.${member.name}`);
        });
        return;
      }
      if ((target.flags & ts.SymbolFlags.Value) === 0) {
        return;
      }
      const type = checker.getTypeOfSymbol(target);
      const signatures = [...type.getCallSignatures(), ...type.getConstructSignatures()];
      for (const signature of signatures) {
        functions += 1;
        for (const parameter of signature.getParameters()) {
          if (holdsServerNumber(checker.getTypeOfSymbol(parameter))) {
            takers.push(`${name}(${parameter.name})`);
          }
        }
      }
    };
    exports().forEach((symbol) => {
      inspect(symbol, symbol.name);
    });
    expect(takers).toEqual([]);
    expect(functions).toBeGreaterThan(30);
  });

  it("would see a function that takes one (the check's own control)", () => {
    const [signature] = checker.getTypeOfSymbol(exported("box.ts", "textOf")).getCallSignatures();
    const [parameter] = signature?.getParameters() ?? [];
    expect(parameter !== undefined && holdsServerNumber(checker.getTypeOfSymbol(parameter))).toBe(true);
  });
});
