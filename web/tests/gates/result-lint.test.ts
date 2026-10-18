/**
 * The result screen's program cells (D435, B5), by the type checker, over `src/result/` and
 * `src/layout/Proportion.tsx` (and, for (f), every module of `src/` outside `src/api/`); no
 * allow-list grows. Each rule has controls, probe modules placed in the project, each refused by its
 * own rule alone with its exact problem.
 *
 * - (a) Every JSX tag is an intrinsic element of D429's `ELEMENTS`, a function component declared in
 *   an app module, or react's own `Fragment`, by its alias-resolved symbol (never the tag's text); a
 *   tag whose type has a `$$typeof` member (an exotic component: `lazy`, `Suspense`, `StrictMode`, a
 *   context's `Provider`, whoever re-exports it) is refused.
 * - (b) No call whose type, `null` and `undefined` removed, is assignable to `ReactElement` (a list
 *   of elements is not one): a component is rendered by React, never called.
 * - (c) A component (by (b)'s type) is referenced only as a JSX tag name.
 * - (d) No JSX attribute and no JSX expression child has a call signature, by type, nor holds one
 *   in an object literal's or a list's member; and no spread attribute. An object of a declared
 *   (interface or alias) type is not walked.
 * - (e) No call whose signature's declaration is named `use[A-Z]…` (react's, react-router's or the
 *   app's), `lazy` or `startTransition`, nor a call of an app function declared with a body that makes
 *   such a call, followed through such declarations. A hook reached through a value (a constant
 *   initialised with an arrow, a table of functions, a hook handed to another function) is not
 *   followed.
 * - (f) No property access, element access by a key of a literal type (a constant key, local or
 *   imported, included) or destructuring by a member's name, outside `src/api/`, resolves to the
 *   generated `ResultEnvelope.values` declaration, and no key of no literal type indexes a type that has
 *   the member. A computed binding key and a generic getter are not seen.
 *
 * - (g) Over every module of `src/`: a `<bdi>` (an intrinsic tag, or the text "bdi" anywhere) and a
 *   `className` whose literal type holds the class `data`, `token` or `number` are written in
 *   `src/layout/Text.tsx` and `src/api/format.tsx` alone (D435's round 3, o2, o3, i7). An isolate then
 *   holds a `ServerText`'s visible form or a `ServerNumber`'s glyphs by type (`Text`, `Ref`, `Num`),
 *   so that the runtime census may leave its text to the isolates' own multiset; but two of
 *   `format.tsx`' `<bdi className="number">` hold a count the page makes (`listOf`'s "and N more" and
 *   `RetryAfter`'s seconds), which `Result` does not reach.
 *
 * These cells hold the spellings they name and no more (D435's round 2: e1–e4, f1, f2, d1);
 * the display lint (D429) and the runtime census (every word, attribute and isolate the page shows,
 * D435) hold what such code would put on the page **in its first render** (`renderToString`, no
 * effect run): what a hook reached through a value would change after mount is held by neither.
 *
 * And B7.5: `Values.tsx`' statistics table fails to compile with an entry removed or a bin index
 * added.
 */
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";

import ts from "typescript";
import { describe, expect, it } from "vitest";

import { ELEMENTS } from "../../scripts/lint/display.mjs";
import { check, type Checked, WEB } from "./program";

export const PROBLEMS = {
  tag: "a JSX tag that is no intrinsic element of ELEMENTS, no app function component and not react's Fragment",
  exotic: "a JSX tag of an exotic component ($$typeof)",
  call: "a call whose type is an element: a component called, not rendered",
  reference: "a component referenced other than as a JSX tag name",
  callable: "a JSX attribute or child with a call signature",
  spread: "a JSX spread attribute",
  hook: "a hook, lazy or startTransition called",
  values: "a result's values member named outside src/api/ (valuesOf is the door)",
  isolate: "a <bdi> outside Text.tsx and format.tsx",
  isolateClass: "an isolate's class (data, token or number) outside Text.tsx and format.tsx",
} as const;

/** The modules that write an isolate (g). */
export const ISOLATE_MODULES = ["src/layout/Text.tsx", "src/api/format.tsx"];
const ISOLATE_CLASSES = ["data", "token", "number"];

export interface Problem {
  readonly file: string;
  readonly problem: string;
  readonly at: string;
}

/** The types and symbols the rules compare with, from a module of their own in the program. */
const TYPES = "src/result/__b5_types.ts";
const TYPES_TEXT = `import { Fragment, type ReactElement, useState } from "react";
export declare const element: ReactElement;
export const fragment = Fragment;
/** A control's helpers, in an app module of their own: a hook behind a wrapper not named use…, and a
 * constant key of the result's values member. */
export function counter(): number {
  const [n] = useState(0);
  return n;
}
export const KEY_VALUES = "values" as const;
`;

const isApp = (file: string): boolean => {
  const relative = path.relative(WEB, file);
  return relative.startsWith("src/") && !relative.includes("node_modules");
};

/** The symbol a name resolves to, through import aliases. */
function resolved(checked: Checked, symbol: ts.Symbol | undefined): ts.Symbol | undefined {
  return symbol !== undefined && (symbol.flags & ts.SymbolFlags.Alias) !== 0 ? checked.checker.getAliasedSymbol(symbol) : symbol;
}

function declaration(checked: Checked, name: string): ts.Node {
  const source = checked.source(TYPES);
  let found: ts.Node | undefined;
  const visit = (node: ts.Node): void => {
    if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name) && node.name.text === name) {
      found = node.name;
    }
    ts.forEachChild(node, visit);
  };
  visit(source);
  if (found === undefined) {
    throw new Error(`no ${name}`);
  }
  return found;
}

/** The generated `ResultEnvelope.values` declaration. */
function valuesDeclaration(checked: Checked): ts.Node {
  const source = checked.program.getSourceFile(path.join(WEB, "src/api/generated/openapi.ts"));
  let found: ts.Node | undefined;
  const visit = (node: ts.Node): void => {
    if (ts.isPropertySignature(node) && ts.isIdentifier(node.name) && node.name.text === "ResultEnvelope" && node.type !== undefined) {
      // The component's type literal, alone or the first part of an intersection.
      const literals = ts.isIntersectionTypeNode(node.type) ? node.type.types.filter(ts.isTypeLiteralNode) : ts.isTypeLiteralNode(node.type) ? [node.type] : [];
      found = literals.flatMap((literal) => literal.members).find((member) => member.name !== undefined && ts.isIdentifier(member.name) && member.name.text === "values");
    }
    ts.forEachChild(node, visit);
  };
  if (source !== undefined) {
    visit(source);
  }
  if (found === undefined) {
    throw new Error("no ResultEnvelope.values");
  }
  return found;
}

/** The problems of (a)–(e) in one file. */
export function screenProblems(checked: Checked, file: string): Problem[] {
  const { checker } = checked;
  const source = checked.source(file);
  const problems: Problem[] = [];
  const add = (problem: string, node: ts.Node): void => {
    problems.push({ file, problem, at: node.getText(source) });
  };
  const elementType = checker.getTypeAtLocation(declaration(checked, "element"));
  // React's own Fragment: the symbol the types module's `Fragment` resolves to.
  const initializer = (declaration(checked, "fragment").parent as ts.VariableDeclaration).initializer;
  const reactFragment = initializer === undefined ? undefined : resolved(checked, checker.getSymbolAtLocation(initializer));
  const isElement = (type: ts.Type): boolean => checker.isTypeAssignableTo(checker.getNonNullableType(type), elementType);
  const isComponent = (symbol: ts.Symbol | undefined): boolean =>
    (symbol?.declarations ?? []).some(
      (one) => (ts.isFunctionDeclaration(one) || ts.isVariableDeclaration(one)) && isApp(one.getSourceFile().fileName) && checker.getSignaturesOfType(checker.getTypeOfSymbolAtLocation(symbol as ts.Symbol, one), ts.SignatureKind.Call).some((signature) => isElement(signature.getReturnType())),
    );
  // (e): a call is a hook's when its signature's declaration is named use…, lazy or startTransition,
  // or is an app function whose body makes such a call, transitively (by symbol, never by name alone).
  const HOOKED = new Map<ts.Node, boolean>();
  const isHookName = (name: string): boolean => /^use[A-Z]/u.test(name) || name === "lazy" || name === "startTransition";
  const declarationsOf = (call: ts.CallExpression): ts.SignatureDeclaration[] =>
    checker.getSignaturesOfType(checker.getTypeAtLocation(call.expression), ts.SignatureKind.Call).flatMap((signature) => {
      const declared = signature.getDeclaration() as ts.SignatureDeclaration | undefined;
      return declared === undefined ? [] : [declared];
    });
  const bodyCallsHook = (declared: ts.SignatureDeclaration): boolean => {
    const known = HOOKED.get(declared);
    if (known !== undefined) {
      return known;
    }
    HOOKED.set(declared, false);
    let found = false;
    const body = (declared as { readonly body?: ts.Node }).body;
    const visitBody = (inner: ts.Node): void => {
      if (found) {
        return;
      }
      if (ts.isCallExpression(inner) && hooked(inner)) {
        found = true;
        return;
      }
      ts.forEachChild(inner, visitBody);
    };
    if (body !== undefined && isApp(declared.getSourceFile().fileName)) {
      visitBody(body);
    }
    HOOKED.set(declared, found);
    return found;
  };
  function hooked(call: ts.CallExpression): boolean {
    return declarationsOf(call).some((declared) => (declared.name !== undefined && ts.isIdentifier(declared.name) && isHookName(declared.name.text)) || bodyCallsHook(declared));
  }
  // (d): a type with a call signature, or an object literal's or a list's member that has one.
  const callable = (type: ts.Type, depth = 0): boolean => {
    if (checker.getSignaturesOfType(type, ts.SignatureKind.Call).length > 0) {
      return true;
    }
    if (depth > 3) {
      return false;
    }
    if (type.isUnion()) {
      return type.types.some((one) => callable(one, depth + 1));
    }
    if (checker.isArrayType(type) || checker.isTupleType(type)) {
      return checker.getTypeArguments(type as ts.TypeReference).some((one) => callable(one, depth + 1));
    }
    if ((type.flags & ts.TypeFlags.Object) !== 0 && ((type as ts.ObjectType).objectFlags & ts.ObjectFlags.ObjectLiteral) !== 0) {
      return type.getProperties().some((property) => callable(checker.getTypeOfSymbol(property), depth + 1));
    }
    return false;
  };
  const tagNameOf = (node: ts.Node): boolean =>
    (ts.isJsxOpeningElement(node.parent) || ts.isJsxSelfClosingElement(node.parent) || ts.isJsxClosingElement(node.parent)) && node.parent.tagName === node;
  const visit = (node: ts.Node): void => {
    // (a)
    if (ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) {
      const tag = node.tagName;
      if (ts.isIdentifier(tag) && /^[a-z]/u.test(tag.text)) {
        if (!ELEMENTS.includes(tag.text)) {
          add(PROBLEMS.tag, tag);
        }
      } else {
        const symbol = resolved(checked, checker.getSymbolAtLocation(tag));
        if (symbol === undefined || symbol !== reactFragment) {
          const type = symbol === undefined ? undefined : checker.getTypeOfSymbolAtLocation(symbol, tag);
          if (type?.getProperty("$$typeof") !== undefined) {
            add(PROBLEMS.exotic, tag);
          } else if (!(symbol?.declarations ?? []).some((one) => ts.isFunctionDeclaration(one) && isApp(one.getSourceFile().fileName))) {
            add(PROBLEMS.tag, tag);
          }
        }
      }
      // (d)
      for (const attribute of node.attributes.properties) {
        if (ts.isJsxSpreadAttribute(attribute)) {
          add(PROBLEMS.spread, attribute);
        } else if (attribute.initializer !== undefined && ts.isJsxExpression(attribute.initializer) && attribute.initializer.expression !== undefined) {
          if (callable(checker.getTypeAtLocation(attribute.initializer.expression))) {
            add(PROBLEMS.callable, attribute);
          }
        }
      }
    }
    if (ts.isJsxExpression(node) && node.expression !== undefined && (ts.isJsxElement(node.parent) || ts.isJsxFragment(node.parent))) {
      if (callable(checker.getTypeAtLocation(node.expression))) {
        add(PROBLEMS.callable, node);
      }
    }
    // (b) and (e)
    if (ts.isCallExpression(node)) {
      if (isElement(checker.getTypeAtLocation(node))) {
        add(PROBLEMS.call, node);
      }
      if (hooked(node)) {
        add(PROBLEMS.hook, node);
      }
    }
    // (c)
    if (ts.isIdentifier(node) && !tagNameOf(node) && !ts.isImportSpecifier(node.parent) && !ts.isImportClause(node.parent) && !ts.isExportSpecifier(node.parent)) {
      const declared = (ts.isFunctionDeclaration(node.parent) || ts.isVariableDeclaration(node.parent)) && node.parent.name === node;
      if (!declared && !ts.isPropertyAccessExpression(node.parent) && isComponent(resolved(checked, checker.getSymbolAtLocation(node)))) {
        add(PROBLEMS.reference, node);
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(source);
  return problems;
}

/** The problems of (f) in one file. */
export function valuesProblems(checked: Checked, file: string): Problem[] {
  const { checker } = checked;
  const source = checked.source(file);
  const target = valuesDeclaration(checked);
  const problems: Problem[] = [];
  const names = (symbol: ts.Symbol | undefined): boolean => (symbol?.declarations ?? []).includes(target as ts.Declaration);
  const visit = (node: ts.Node): void => {
    let symbol: ts.Symbol | undefined;
    if (ts.isPropertyAccessExpression(node)) {
      symbol = checker.getSymbolAtLocation(node.name);
    } else if (ts.isElementAccessExpression(node)) {
      // The key by its type: a literal key names the property it types; any other key on a type that
      // has the values member is refused too.
      const key = checker.getTypeAtLocation(node.argumentExpression);
      const holder = checker.getTypeAtLocation(node.expression);
      if (key.isStringLiteral()) {
        symbol = holder.getProperty(key.value);
      } else if (names(holder.getProperty("values"))) {
        problems.push({ file, problem: PROBLEMS.values, at: node.getText(source) });
      }
    } else if (ts.isBindingElement(node) && ts.isObjectBindingPattern(node.parent)) {
      const name = node.propertyName ?? node.name;
      const text = ts.isIdentifier(name) || ts.isStringLiteral(name) ? name.text : undefined;
      symbol = text === undefined ? undefined : checker.getTypeAtLocation(node.parent).getProperty(text);
    }
    if (names(symbol)) {
      problems.push({ file, problem: PROBLEMS.values, at: node.getText(source) });
    }
    ts.forEachChild(node, visit);
  };
  visit(source);
  return problems;
}

/** The problems of (g) in one file. */
export function isolateProblems(checked: Checked, file: string): Problem[] {
  if (ISOLATE_MODULES.includes(file)) {
    return [];
  }
  const { checker } = checked;
  const source = checked.source(file);
  const problems: Problem[] = [];
  const add = (problem: string, node: ts.Node): void => {
    problems.push({ file, problem, at: node.getText(source) });
  };
  // The texts a type may hold: each string literal of it (a union's members), and a template's parts.
  const literals = (type: ts.Type): string[] => {
    if (type.isUnion()) {
      return type.types.flatMap(literals);
    }
    if (type.isStringLiteral()) {
      return [type.value];
    }
    if ((type.flags & ts.TypeFlags.TemplateLiteral) !== 0) {
      const template = type as ts.TemplateLiteralType;
      return [template.texts.join(" ")];
    }
    return [];
  };
  const visit = (node: ts.Node): void => {
    if ((ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) && ts.isIdentifier(node.tagName) && node.tagName.text === "bdi") {
      add(PROBLEMS.isolate, node.tagName);
    }
    if ((ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) && node.text === "bdi") {
      add(PROBLEMS.isolate, node);
    }
    if (ts.isJsxAttribute(node) && ts.isIdentifier(node.name) && node.name.text === "className" && node.initializer !== undefined) {
      const expression = ts.isJsxExpression(node.initializer) ? node.initializer.expression : node.initializer;
      const texts = expression === undefined ? [] : ts.isStringLiteral(expression) ? [expression.text] : literals(checker.getTypeAtLocation(expression));
      if (texts.some((text) => text.split(/\s+/u).some((one) => ISOLATE_CLASSES.includes(one)))) {
        add(PROBLEMS.isolateClass, node);
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(source);
  return problems;
}

const read = (file: string): string => readFileSync(path.join(WEB, file), "utf8");
const filesOf = (directory: string, pattern: RegExp): string[] =>
  readdirSync(path.join(WEB, directory), { recursive: true, encoding: "utf8" })
    .filter((name) => pattern.test(name))
    .map((name) => path.join(directory, name))
    .sort();

const SCREEN = [...filesOf("src/result", /\.tsx$/u), "src/layout/Proportion.tsx"];
const ALL_SRC = filesOf("src", /\.tsx?$/u).filter((file) => !file.endsWith(".d.ts"));
const APP = filesOf("src", /\.tsx?$/u).filter((file) => !file.startsWith("src/api/") && !file.endsWith(".d.ts"));

/** A probe module of the result screen: one control's spelling in a module beside the screen's. */
const probe = (body: string): string => `import * as React from "react";
import { Fragment, lazy, StrictMode, Suspense, Suspense as Shadow, createContext, useState, type ReactElement } from "react";
import { useParams as p } from "react-router";
import { type Answered, Num } from "../api";
import { Caveats } from "../layout/Caveat";
import { Text } from "../layout/Text";
import { Values } from "./Values";
import { counter, KEY_VALUES } from "./__b5_types";
type Result = Answered<"run_analysis">["results"][number];
declare const result: Result;
declare const rows: readonly string[];
const Context = createContext(0);
export function Row({ value }: { readonly value: string }): ReactElement {
  return <p>{value}</p>;
}
declare function Each(props: { readonly children: (r: string) => ReactElement }): ReactElement;
declare function Holder(props: { readonly cfg: { readonly render: () => ReactElement } }): ReactElement;
function useThing(): number {
  return 1;
}
export function Probe(): ReactElement | null {
${body}
}
`;

/** Each control: its name, its spelling, the exact problems it gives. */
const CONTROLS: readonly (readonly [string, string, readonly string[]])[] = [
  ["an intrinsic span and an app component pass", `return <span><Row value="x" /></span>;`, []],
  ["react's Fragment passes", `return <Fragment><Row value="x" /></Fragment>;`, []],
  ["Suspense imported as Fragment's name fails (a)", `return <Shadow><Row value="x" /></Shadow>;`, [PROBLEMS.exotic]],
  ["Suspense fails (a)", `return <Suspense><Row value="x" /></Suspense>;`, [PROBLEMS.exotic]],
  ["StrictMode fails (a)", `return <StrictMode><Row value="x" /></StrictMode>;`, [PROBLEMS.exotic]],
  ["a context's Provider fails (a)", `return <Context.Provider value={1}><Row value="x" /></Context.Provider>;`, [PROBLEMS.exotic]],
  ["a lazy component fails (a), and its making is a lazy call (e)", `const L = lazy(() => import("./Probe0").then((module) => ({ default: module.Row }))); return <L value="x" />;`, [PROBLEMS.hook, PROBLEMS.exotic]],
  ["an element not on ELEMENTS fails (a)", `return <h4>x</h4>;`, [PROBLEMS.tag]],
  ["a component called fails (b), and the reference is (c)'s", `return <div>{Row({ value: "x" })}</div>;`, [PROBLEMS.call, PROBLEMS.reference]],
  ["a component of ReactElement | null called fails (b)", `return <div>{Caveats({ caveats: [] })}</div>;`, [PROBLEMS.call, PROBLEMS.reference]],
  ["a list of elements made by map passes", `return <div>{rows.map((r) => <Row key={r} value={r} />)}</div>;`, []],
  ["a component called in a map fails (b) at the inner call", `return <div>{rows.map((r) => Row({ value: r }))}</div>;`, [PROBLEMS.call, PROBLEMS.reference]],
  ["a call returning a string is not (b)'s", `return <div>{rows.join(", ")}</div>;`, []],
  ["a component as a value fails (c)", `const C = Values; return <p>{String(C === Values)}</p>;`, [PROBLEMS.reference, PROBLEMS.reference, PROBLEMS.reference]],
  ["a component in a list fails (c)", `return <p>{[Row].length > 0 ? "a" : "b"}</p>;`, [PROBLEMS.reference]],
  ["a handler fails (d)", `return <p onClick={() => undefined}>x</p>;`, [PROBLEMS.callable]],
  ["a function child fails (d)", `return <Each>{(r) => <Row value={r} />}</Each>;`, [PROBLEMS.callable]],
  ["a spread attribute fails (d)", `const props = { value: "x" }; return <Row {...props} />;`, [PROBLEMS.spread]],
  ["a hook by an alias of its import fails (e)", `const id = p(); return <p>{String(id)}</p>;`, [PROBLEMS.hook]],
  ["a hook through a const fails (e)", `const u = useState; const [n] = u(0); return <p>{String(n)}</p>;`, [PROBLEMS.hook]],
  ["an app hook fails (e)", `return <p>{String(useThing())}</p>;`, [PROBLEMS.hook]],
  ["a hook behind an app wrapper not named use… fails (e)", `return <p>{String(counter())}</p>;`, [PROBLEMS.hook]],
  ["a render function nested in an object attribute fails (d)", `return <Holder cfg={{ render: () => <p>x</p> }} />;`, [PROBLEMS.callable]],
  ["a local constant key of the values member fails (f)", `const KEY = "values" as const; return <p>{String(result[KEY])}</p>;`, [PROBLEMS.values]],
  ["an imported constant key of the values member fails (f)", `return <p>{String(result[KEY_VALUES])}</p>;`, [PROBLEMS.values]],
  ["a key of no literal type on the result fails (f)", `const pick = (key: keyof Result): unknown => result[key]; return <p>{String(pick("digest"))}</p>;`, [PROBLEMS.values]],
  ["result.values fails (f)", `return <p>{String(result.values)}</p>;`, [PROBLEMS.values]],
  ['result["values"] fails (f)', `return <p>{String(result["values"])}</p>;`, [PROBLEMS.values]],
  ["a spread's values fails (f)", `return <p>{String({ ...result }.values)}</p>;`, [PROBLEMS.values]],
  ["destructured values fails (f)", `const { values } = result; return <p>{String(values)}</p>;`, [PROBLEMS.values]],
  ["another member passes (f)", `return <p><Num value={result.derivation.semantics_version} /><Text value={result.digest} /></p>;`, []],
  ["a page-made number in an isolate fails (g) (o3)", `return <p>Packs: <bdi className="number">2</bdi></p>;`, [PROBLEMS.isolate, PROBLEMS.isolateClass]],
  ["a page word in a data isolate fails (g) (i7)", `return <p><bdi className="data">suppressed</bdi></p>;`, [PROBLEMS.isolate, PROBLEMS.isolateClass]],
  ["a bdi of no class fails (g)", `return <p><bdi>2</bdi></p>;`, [PROBLEMS.isolate]],
  ["an isolate's class on a span, by a union, fails (g)", `return <p><span className={rows.length > 0 ? "data token" : "x"}>2</span></p>;`, [PROBLEMS.isolateClass]],
  ["an isolate's class by a constant fails (g)", `const C = "number" as const; return <p><span className={C}>2</span></p>;`, [PROBLEMS.isolateClass]],
  ["a bdi made by createElement fails (g) (and (b): an element called for)", `return React.createElement("bdi", null, "2");`, [PROBLEMS.call, PROBLEMS.isolate]],
  ["another class passes (g)", `return <p className={rows.length > 0 ? "note" : "meta numbers"}>x</p>;`, []],
];

/** The app's modules, the types module and every control's probe, in one program. */
const probeFile = (index: number): string => `src/result/Probe${String(index)}.tsx`;
const PROGRAM = check({
  [TYPES]: TYPES_TEXT,
  ...Object.fromEntries([...APP, "src/api/generated/openapi.ts"].map((file) => [file, read(file)])),
  ...Object.fromEntries(CONTROLS.map(([, body], index) => [probeFile(index), probe(body)])),
});

describe("the result screen's program cells (B5)", () => {
  it("the screen's modules pass (a)–(e), and no module outside src/api/ names a result's values (f)", () => {
    expect(SCREEN).toEqual(["src/result/Analysed.tsx", "src/result/Charts.tsx", "src/result/Figures.tsx", "src/result/Provenance.tsx", "src/result/Values.tsx", "src/layout/Proportion.tsx"]);
    expect(SCREEN.flatMap((file) => screenProblems(PROGRAM, file))).toEqual([]);
    expect(APP.length).toBeGreaterThan(30);
    expect(APP.flatMap((file) => valuesProblems(PROGRAM, file))).toEqual([]);
  });

  it("only Text.tsx and format.tsx write an isolate or an isolate's class (g), over every module of src/", () => {
    expect(ALL_SRC.length).toBeGreaterThan(APP.length);
    expect(ALL_SRC).toEqual(expect.arrayContaining([...ISOLATE_MODULES, "src/result/Provenance.tsx", "src/layout/Segments.tsx"]));
    expect(ALL_SRC.flatMap((file) => isolateProblems(PROGRAM, file))).toEqual([]);
    // The two modules write them (the rule is no vacuum there).
    const text = read("src/layout/Text.tsx");
    const format = read("src/api/format.tsx");
    expect([text.includes("<bdi"), format.includes('<bdi className="number">')]).toEqual([true, true]);
  });

  it.each(CONTROLS.map(([name, , problems], index) => [name, index, problems] as const))("%s", (_name, index, problems) => {
    const file = probeFile(index);
    expect(PROGRAM.codes(file)).toEqual([]);
    const found = [...screenProblems(PROGRAM, file), ...valuesProblems(PROGRAM, file), ...isolateProblems(PROGRAM, file)].map((one) => one.problem);
    expect(found.sort()).toEqual([...problems].sort());
  });
});

describe("the statistics table (B7.5)", () => {
  const VALUES = "src/result/Values.tsx";
  const text = read(VALUES);
  it("compiles as it stands, and fails with an entry removed or a bin index added", () => {
    expect(check({ [VALUES]: text }).codes(VALUES)).toEqual([]);
    const removed = text.replace('  "/sd": { name: "sd", of: (arm: Numbers) => arm.sd },\n', "");
    expect(removed).not.toBe(text);
    expect(check({ [VALUES]: removed }).codes(VALUES).length).toBeGreaterThan(0);
    const added = text.replace('  "/histogram": { name: "histogram", of: null },', '  "/histogram": { name: "histogram", of: null },\n  "/q1_bin": { name: "q1_bin", of: (arm: Numbers) => arm.q1_bin ?? null },');
    expect(added).not.toBe(text);
    expect(check({ [VALUES]: added }).codes(VALUES).length).toBeGreaterThan(0);
  });
});
