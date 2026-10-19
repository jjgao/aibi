/**
 * The builder's calls and state by position (D433; B4 of the plan; R90f's proxies, OPEN until
 * 2b-1L2): allow-lists read from every module of `src/builder/` in the app's program (the checker
 * resolves `loader` by its symbol), each rule with a control that it alone refuses, asserted by the
 * exact problem it gives.
 *
 * - every reference is read by its symbol, past parentheses, and must be the direct callee of a call
 *   in its permitted position (an alias of a value, a value passed on, an element access refused):
 * - `loader` (the tab's queue, `src/catalogue/load.ts`), by its name, an alias of its import or
 *   `load.loader`, is called only in a function that is itself the value of a JSX `on*` attribute (a
 *   person's action): never in a render, an updater, an initialiser or a callback of a promise; its
 *   module's namespace stands only as `load.loader`;
 * - every sender of `src/api/index.ts` (`SENDERS`: the seam's three and the typed answers' four) is
 *   called only in the function handed to `loader` as its first argument (so that every call goes
 *   through the tab's queue, and only on a press);
 * - the hooks (`useState`, `useSyncExternalStore` and the effect hooks) are found by their `react`
 *   export's symbol, whatever they are called, and are only called;
 * - every `useState` is destructured into a value and a setter, and every reference to a setter is
 *   the callee of a call that stands in such a function (an alias, a `.then(setX)`, a setter in a
 *   store's `subscribe` or in an effect is refused: each is a reference elsewhere);
 * - every `useState` initialiser is a literal, `null`, one of the modules' frozen constants
 *   (`INITIAL`), or an arrow whose body is one;
 * - every `useSyncExternalStore` is handed one store's own `subscribe` and `snapshot`;
 * - no effect hook (`useEffect`, `useLayoutEffect`, `useInsertionEffect`): nothing is sent on load;
 * - by type: a value typed `Typed` (what the person typed, `Field`'s mint) stands at a JSX position of
 *   the builder only as a `<Field>`'s `value`.
 *
 * **Not held here:** the type a `useState` holds. A raw string or number held in state is refused by
 * the display lint where it would be shown (R90, `display/raw-text`), and the seam's types refuse one
 * where it would be sent (D432); the builder's states hold `Outcome`s, whose `status` and
 * `retryAfter` are the client's own numbers.
 */
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";

import ts from "typescript";
import { describe, expect, it } from "vitest";

import { check, type Checked, WEB } from "./program";

/** The module-level constants a `useState` may start from (frozen, of `model.ts` and `Builder.tsx`). */
const INITIAL = ["EMPTY", "IDLE_ASKED"];

const EFFECTS = ["useEffect", "useLayoutEffect", "useInsertionEffect"];

const FUNCTIONS = new Set([ts.SyntaxKind.ArrowFunction, ts.SyntaxKind.FunctionExpression, ts.SyntaxKind.FunctionDeclaration, ts.SyntaxKind.MethodDeclaration]);

/** The problems' words, one for each rule. */
export const PROBLEMS = {
  loader: "loader reached outside the callee of a call in a JSX on* handler",
  effect: "an effect hook",
  store: "useSyncExternalStore not of one store's own subscribe and snapshot",
  destructured: "useState not destructured into a value and a setter",
  initial: "useState's initialiser is no literal, null, initial constant or arrow returning one",
  setter: "a setter outside a call in a JSX on* handler",
  seam: "a seam sender outside the function handed to loader",
  hook: "a hook reached other than as the callee of a call",
  typed: "a Typed value at a JSX position other than <Field value>",
} as const;

/** The nearest function around a node. */
function functionOf(node: ts.Node): ts.Node | null {
  let at: ts.Node = node;
  while (!ts.isSourceFile(at)) {
    at = at.parent;
    if (FUNCTIONS.has(at.kind)) {
      return at;
    }
  }
  return null;
}

/** Whether a node stands in a function that is the value of a JSX `on*` attribute (its nearest). */
function inHandler(node: ts.Node): boolean {
  const found = functionOf(node);
  const expression = found?.parent;
  const attribute = expression?.parent;
  return expression !== undefined && ts.isJsxExpression(expression) && attribute !== undefined && ts.isJsxAttribute(attribute) && /^on[A-Z]/u.test(attribute.name.getText());
}


/** Whether an initialiser is a literal, `null`, an `INITIAL` constant, or an arrow whose body is one. */
function isInitial(node: ts.Expression | undefined): boolean {
  if (node === undefined) {
    return true;
  }
  if (ts.isArrowFunction(node)) {
    return ts.isBlock(node.body) ? false : isInitial(node.body);
  }
  return (
    ts.isStringLiteral(node) ||
    ts.isNumericLiteral(node) ||
    node.kind === ts.SyntaxKind.TrueKeyword ||
    node.kind === ts.SyntaxKind.FalseKeyword ||
    node.kind === ts.SyntaxKind.NullKeyword ||
    (ts.isIdentifier(node) && INITIAL.includes(node.text))
  );
}

/** The symbol of `src/catalogue/load.ts`' `loader` export. */
function loaderOf(checked: Checked): ts.Symbol {
  const module = checked.checker.getSymbolAtLocation(checked.source("src/catalogue/load.ts"));
  const found = module === undefined ? undefined : checked.checker.getExportsOfModule(module).find((one) => one.name === "loader");
  if (found === undefined) {
    throw new Error("no loader export");
  }
  return found;
}

/** The exports of a module of the program, by name (the module named by its file, or by `react`'s specifier
 * as `Builder.tsx` imports it). */
function exportsOf(checked: Checked, module: ts.Symbol | undefined, names: readonly string[]): Map<ts.Symbol, string> {
  const found = new Map<ts.Symbol, string>();
  for (const one of module === undefined ? [] : checked.checker.getExportsOfModule(module)) {
    if (names.includes(one.name)) {
      const target = (one.flags & ts.SymbolFlags.Alias) !== 0 ? checked.checker.getAliasedSymbol(one) : one;
      found.set(target, one.name);
    }
  }
  if (found.size !== names.length) {
    throw new Error(`not every export found: ${names.join(", ")}`);
  }
  return found;
}

/** The `react` module's symbol, as `Builder.tsx` imports it. */
function reactOf(checked: Checked): ts.Symbol | undefined {
  const source = checked.source("src/builder/Builder.tsx");
  const declaration = source.statements.find((one): one is ts.ImportDeclaration => ts.isImportDeclaration(one) && ts.isStringLiteral(one.moduleSpecifier) && one.moduleSpecifier.text === "react");
  return declaration === undefined ? undefined : checked.checker.getSymbolAtLocation(declaration.moduleSpecifier);
}

/** Whether a node stands in an import or export clause (a name declared, not a reference). */
function inClause(node: ts.Node): boolean {
  for (let at: ts.Node = node.parent; !ts.isSourceFile(at); at = at.parent) {
    if (ts.isImportDeclaration(at) || ts.isExportDeclaration(at)) {
      return true;
    }
  }
  return false;
}

/** A problem: its line and its rule's words. */
export interface Problem {
  readonly line: number;
  readonly problem: string;
}

/** Every export of `src/api/index.ts` that sends a request (the seam's three, and the typed answers'
 * four the builder never needs): each must be called in the function handed to `loader`. */
export const SENDERS = ["validateDraft", "countDraft", "runDraft", "describeDataset", "explain", "listAnalyses", "searchCatalog"];

/** Whether an expression is a name or a chain of members of one (no call, no element access): one
 * store, read twice alike. */
function isPlain(node: ts.Expression): boolean {
  return ts.isIdentifier(node) || (ts.isPropertyAccessExpression(node) && isPlain(node.expression));
}

/** The expression a reference stands as: past the parentheses around it, and a namespace's member
 * taken whole (`load.loader`, `React.useState`). */
function outerOf(node: ts.Identifier): ts.Node {
  let at: ts.Node = ts.isPropertyAccessExpression(node.parent) && node.parent.name === node ? node.parent : node;
  while (ts.isParenthesizedExpression(at.parent)) {
    at = at.parent;
  }
  return at;
}

/** The call a reference is the direct callee of (past parentheses), or `null`. */
function callOf(node: ts.Identifier): ts.CallExpression | null {
  const at = outerOf(node);
  return ts.isCallExpression(at.parent) && at.parent.expression === at ? at.parent : null;
}

/** What the allow-lists refuse in one module of the program. */
export function hookProblems(checked: Checked, file: string): Problem[] {
  const source = checked.source(file);
  const checker = checked.checker;
  const loader = loaderOf(checked);
  const loadModule = checker.getSymbolAtLocation(checked.source("src/catalogue/load.ts"));
  const hooks = exportsOf(checked, reactOf(checked), ["useState", "useSyncExternalStore", ...EFFECTS]);
  const senders = exportsOf(checked, checker.getSymbolAtLocation(checked.source("src/api/index.ts")), SENDERS);
  const problems: Problem[] = [];
  const setters = new Map<string, ts.Identifier>();
  const report = (node: ts.Node, problem: string): void => {
    problems.push({ line: source.getLineAndCharacterOfPosition(node.getStart()).line + 1, problem });
  };
  const resolved = (node: ts.Identifier): ts.Symbol | undefined => {
    // A shorthand property (`{ useState }`) names the value it holds.
    const symbol = ts.isShorthandPropertyAssignment(node.parent) && node.parent.name === node ? checker.getShorthandAssignmentValueSymbol(node.parent) : checker.getSymbolAtLocation(node);
    return symbol !== undefined && (symbol.flags & ts.SymbolFlags.Alias) !== 0 ? checker.getAliasedSymbol(symbol) : symbol;
  };
  /** The symbol a call's callee names, past parentheses and a namespace's member. */
  const calleeOf = (call: ts.CallExpression): ts.Symbol | undefined => {
    let inner: ts.Expression = call.expression;
    while (ts.isParenthesizedExpression(inner)) {
      inner = inner.expression;
    }
    const callee = ts.isPropertyAccessExpression(inner) ? inner.name : inner;
    return ts.isIdentifier(callee) ? resolved(callee) : undefined;
  };
  /** Whether a call stands in the function handed to `loader` as its first argument (its nearest). */
  const inThunk = (call: ts.Node): boolean => {
    const found = functionOf(call);
    const outer = found?.parent;
    return found !== null && outer !== undefined && ts.isCallExpression(outer) && outer.arguments[0] === found && calleeOf(outer) === loader;
  };
  const visit = (node: ts.Node): void => {
    if (ts.isIdentifier(node) && !inClause(node)) {
      const symbol = resolved(node);
      if (symbol !== undefined && symbol === loadModule) {
        // The queue's module is reached only as `load.loader`, whose `loader` the next rule reads.
        const member = node.parent;
        if (!(ts.isPropertyAccessExpression(member) && member.expression === node && member.name.text === "loader")) {
          report(node, PROBLEMS.loader);
        }
      }
      if (symbol !== undefined && symbol === loader) {
        const call = callOf(node);
        if (call === null || !inHandler(call)) {
          report(node, PROBLEMS.loader);
        }
      }
      if (symbol !== undefined && senders.has(symbol)) {
        const call = callOf(node);
        if (call === null || !inThunk(call)) {
          report(node, PROBLEMS.seam);
        }
      }
      if (symbol !== undefined && hooks.has(symbol) && callOf(node) === null) {
        report(node, PROBLEMS.hook);
      }
    }
    if (ts.isCallExpression(node)) {
      const symbol = calleeOf(node);
      const name = symbol === undefined ? null : (hooks.get(symbol) ?? null);
      if (name !== null && EFFECTS.includes(name)) {
        report(node, PROBLEMS.effect);
      }
      if (name === "useSyncExternalStore") {
        const [subscribe, snapshot, ...rest] = node.arguments;
        const own =
          subscribe !== undefined &&
          snapshot !== undefined &&
          rest.length === 0 &&
          ts.isPropertyAccessExpression(subscribe) &&
          ts.isPropertyAccessExpression(snapshot) &&
          isPlain(subscribe.expression) &&
          subscribe.name.text === "subscribe" &&
          snapshot.name.text === "snapshot" &&
          subscribe.expression.getText() === snapshot.expression.getText();
        if (!own) {
          report(node, PROBLEMS.store);
        }
      }
      if (name === "useState") {
        const binding = node.parent;
        const setter = ts.isVariableDeclaration(binding) && ts.isArrayBindingPattern(binding.name) && binding.name.elements.length === 2 ? binding.name.elements[1] : undefined;
        if (setter !== undefined && ts.isBindingElement(setter) && ts.isIdentifier(setter.name)) {
          setters.set(setter.name.text, setter.name);
        } else {
          report(node, PROBLEMS.destructured);
        }
        if (!isInitial(node.arguments[0])) {
          report(node, PROBLEMS.initial);
        }
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(source);
  const references = (node: ts.Node): void => {
    if (ts.isIdentifier(node) && setters.has(node.text) && setters.get(node.text) !== node) {
      const call = callOf(node);
      if (call === null || !inHandler(call)) {
        report(node, PROBLEMS.setter);
      }
    }
    ts.forEachChild(node, references);
  };
  references(source);
  return problems;
}

/** The JSX positions of a module at which a value typed `Typed` stands, but a `<Field>`'s `value`. */
export function typedProblems(checked: Checked, file: string): Problem[] {
  const source = checked.source(file);
  const checker = checked.checker;
  const problems: Problem[] = [];
  const isTyped = (type: ts.Type): boolean => {
    const alias = type.aliasSymbol;
    if (alias?.name === "Typed" && alias.declarations?.some((one) => one.getSourceFile().fileName.endsWith("src/layout/Field.tsx")) === true) {
      return true;
    }
    if (type.isUnion()) {
      return type.types.some(isTyped);
    }
    return type.getProperties().some((one) => one.escapedName.toString().includes("typedBrand"));
  };
  const visit = (node: ts.Node): void => {
    if (ts.isJsxExpression(node) && node.expression !== undefined && isTyped(checker.getTypeAtLocation(node.expression))) {
      const attribute = node.parent;
      const element = attribute.parent.parent;
      const allowed = ts.isJsxAttribute(attribute) && attribute.name.getText() === "value" && (ts.isJsxOpeningElement(element) || ts.isJsxSelfClosingElement(element)) && element.tagName.getText() === "Field";
      if (!allowed) {
        problems.push({ line: source.getLineAndCharacterOfPosition(node.getStart()).line + 1, problem: PROBLEMS.typed });
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(source);
  return problems;
}

const BUILDER = path.join(WEB, "src/builder");
const FILES = readdirSync(BUILDER)
  .filter((name) => /\.tsx?$/u.test(name))
  .map((name) => `src/builder/${name}`)
  .sort();
const read = (file: string): string => readFileSync(path.join(WEB, file), "utf8");

/** A probe module of the builder: one control's spelling as the body of a component. */
const probe = (body: string): string => `import * as React from "react";
import { useEffect, useState, useState as useHeld, useSyncExternalStore } from "react";
import * as api from "../api";
import { countDraft, describeDataset, type Draft, validateDraft } from "../api";
import * as load from "../catalogue/load";
import { loader, loader as make } from "../catalogue/load";
import { Field, type Typed } from "../layout/Field";
declare const store: { subscribe: (l: () => void) => () => void; snapshot: () => number };
declare const other: { subscribe: (l: () => void) => () => void; snapshot: () => number };
declare const typed: Typed;
declare const someName: number;
declare function f(): number;
declare const draft: Draft;
export function Probe() {
${body}
}
`;

/** Each control: its name, its spelling, and the exact problems it gives (each refused by its rule
 * alone, but the two that break two rules, named in their problems). */
const CONTROLS: readonly (readonly [string, string, readonly string[]])[] = [
  ["loader called in a render", `const made = loader(() => Promise.resolve(1)); return <p>{String(made)}</p>;`, [PROBLEMS.loader]],
  ["loader by an alias of its import, in a render (P4)", `const made = make(() => Promise.resolve(1)); return <p>{String(made)}</p>;`, [PROBLEMS.loader]],
  ["loader as a namespace's member, in a render (P5)", `const made = load.loader(() => Promise.resolve(1)); return <p>{String(made)}</p>;`, [PROBLEMS.loader]],
  ["loader passed on in a handler", `return <button onClick={() => { void [1].map(loader as never); }}>x</button>;`, [PROBLEMS.loader]],
  ["loader in an updater", `const [s, setS] = useState(null); return <button onClick={() => { setS(() => loader(() => Promise.resolve(1)) as never); }}>{String(s)}</button>;`, [PROBLEMS.loader]],
  ["loader in useState's initialiser", `const [s, setS] = useState(() => loader(() => Promise.resolve(1))); return <button onClick={() => { setS(null as never); }}>{String(s)}</button>;`, [PROBLEMS.initial, PROBLEMS.loader]],
  ["a setter handed to then", `const [s, setS] = useState(null); return <button onClick={() => { void Promise.resolve(null).then(setS); }}>{String(s)}</button>;`, [PROBLEMS.setter]],
  ["a setter called in a promise's callback", `const [s, setS] = useState(null); return <button onClick={() => { void Promise.resolve(null).then(() => { setS(null); }); }}>{String(s)}</button>;`, [PROBLEMS.setter]],
  ["an aliased setter", `const [s, setS] = useState(null); const set = setS; return <button onClick={() => { set(null); }}>{String(s)}</button>;`, [PROBLEMS.setter]],
  ["a setter called in a render", `const [s, setS] = useState(null); setS(null); return <p>{String(s)}</p>;`, [PROBLEMS.setter]],
  ["a setter in a store's subscribe", `const [s, setS] = useState(null); const v = useSyncExternalStore((l) => { setS(null); return store.subscribe(l); }, store.snapshot); return <p>{String(s)}{v}</p>;`, [PROBLEMS.store, PROBLEMS.setter]],
  ["an effect hook", `useEffect(() => undefined); return <p />;`, [PROBLEMS.effect]],
  ["a snapshot of another store", `const v = useSyncExternalStore(store.subscribe, other.snapshot); return <p>{v}</p>;`, [PROBLEMS.store]],
  ["useState not destructured", `const pair = useState(null); return <p>{String(pair)}</p>;`, [PROBLEMS.destructured]],
  ["useState from a name that is no initial constant", `const [s, setS] = useState(someName); return <button onClick={() => { setS(1); }}>{s}</button>;`, [PROBLEMS.initial]],
  ["useState from an arrow that calls", `const [s, setS] = useState(() => f()); return <button onClick={() => { setS(1); }}>{s}</button>;`, [PROBLEMS.initial]],
  ["useState from a value computed in render", `const [s, setS] = useState(Date.now()); return <button onClick={() => { setS(1); }}>{s}</button>;`, [PROBLEMS.initial]],
  ["useState by an alias, its setter handed to then", `const [held, setHeld] = useHeld<boolean>(false); void Promise.resolve(true).then(setHeld); return <p>{String(held)}</p>;`, [PROBLEMS.setter]],
  ["useState as a namespace's member, not destructured", `const pair = React.useState(null); return <p>{String(pair)}</p>;`, [PROBLEMS.destructured]],
  ["an effect hook as a namespace's member", `React.useEffect(() => undefined); return <p />;`, [PROBLEMS.effect]],
  ["loader by an element access of its module's namespace, in a handler", `return <button onClick={() => { void load["loader"](() => Promise.resolve(1)); }}>x</button>;`, [PROBLEMS.loader]],
  ["a seam sender called in a render", `if (draft !== null) { void validateDraft(draft); } return <p />;`, [PROBLEMS.seam]],
  ["a seam sender as a namespace's member, called in a render", `void api.runDraft(draft); return <p />;`, [PROBLEMS.seam]],
  ["a seam sender in a handler, in a function handed to another than loader (gE)", `return <button onClick={() => { setTimeout(() => { void validateDraft(draft); }); }}>x</button>;`, [PROBLEMS.seam]],
  ["a typed answer's sender called in a render (C10)", `void describeDataset({ dataset: "orchard" }); return <p />;`, [PROBLEMS.seam]],
  ["useState as a value alias, its setter handed to then (C1)", `const hook = useState; const [shown, setShown] = hook<boolean>(false); void Promise.resolve(true).then(setShown); return <p>{String(shown)}</p>;`, [PROBLEMS.hook]],
  ["useState in parentheses, its setter handed to then (C2)", `const [shown, setShown] = (useState)<boolean>(false); void Promise.resolve(true).then(setShown); return <p>{String(shown)}</p>;`, [PROBLEMS.setter]],
  ["useSyncExternalStore in parentheses, of another store's snapshot (C4)", `const v = (useSyncExternalStore)(store.subscribe, other.snapshot); return <p>{v}</p>;`, [PROBLEMS.store]],
  ["loader by an element access of its module in parentheses, in a render (C5)", `void (load)["loader"](() => Promise.resolve(1)); return <p />;`, [PROBLEMS.loader]],
  ["useState's setter destructured as an object", `const [s, { call }] = useState<unknown>(null); return <p>{String(s)}{String(call)}</p>;`, [PROBLEMS.destructured]],
  ["useSyncExternalStore of a store a call gives, twice", `const pick = () => store; const v = useSyncExternalStore(pick().subscribe, pick().snapshot); return <p>{v}</p>;`, [PROBLEMS.store]],
  ["useState as a shorthand property of an object", `const hooks = { useState }; const [s, setS] = hooks.useState<unknown>(null); void Promise.resolve(1).then(setS); return <p>{String(s)}</p>;`, [PROBLEMS.hook]],
  ["loader by an element access of an alias of its module (C6)", `const queue = load; void queue["loader"](() => Promise.resolve(1)); return <p />;`, [PROBLEMS.loader]],
  ["a seam sender called in a handler, outside loader", `return <button onClick={() => { void countDraft(draft); }}>x</button>;`, [PROBLEMS.seam]],
  ["a seam sender passed to loader, not called in its function", `return <button onClick={() => { void loader(countDraft as never); }}>x</button>;`, [PROBLEMS.seam]],
  ["a third argument to useSyncExternalStore (g12)", `const v = useSyncExternalStore(store.subscribe, store.snapshot, store.snapshot); return <p>{v}</p>;`, [PROBLEMS.store]],
  ["a setter in a function at an attribute that is no on* handler (g15)", `const [s, setS] = useState(null); return <Field value={typed} onChange={() => undefined} format={() => { setS(null); }}>{String(s)}</Field>;`, [PROBLEMS.setter]],
];

const ACCEPTED = `const [s, setS] = useState(null); const [t, setT] = useState(null); const [u, setU] = useState(() => null); const v = useSyncExternalStore(store.subscribe, store.snapshot);
return <Field value={typed} onChange={(value) => { setT(value as never); }}><button onClick={() => { setS(loader(() => Promise.resolve(v)) as never); setS(make(() => Promise.resolve(1)) as never); setS(load.loader(() => Promise.resolve(2)) as never); setS(loader(() => validateDraft(draft)) as never); setS(load.loader(() => countDraft(draft)) as never); setS(loader(() => api.runDraft(draft)) as never); setS((loader)(() => (validateDraft)(draft)) as never); }}>{String(s)}{String(t)}{String(u)}</button><button onClick={() => { setU(null); }}>u</button></Field>;`;

const TYPED = [
  ["a Typed value as a text", `return <p>{typed}</p>;`],
  ["a Typed value as another element's value (g4)", `return <input value={typed} onChange={() => undefined} />;`],
  ["a Typed value as another attribute", `return <output title={typed} />;`],
  ["a Typed value at a Field's attribute other than value (g13)", `return <Field key={typed} value={typed} onChange={() => undefined}>v</Field>;`],
] as const;

const PROGRAM = check({
  ...Object.fromEntries(FILES.map((file) => [file, read(file)])),
  ...Object.fromEntries(CONTROLS.map(([, body], index) => [`src/builder/Probe${String(index)}.tsx`, probe(body)])),
  ...Object.fromEntries(TYPED.map(([, body], index) => [`src/builder/TypedProbe${String(index)}.tsx`, probe(body)])),
  "src/builder/Accepted.tsx": probe(ACCEPTED),
});

describe("the builder's calls and state, by position (B4)", () => {
  it("the builder's modules are the ones read, and pass every rule", () => {
    expect(FILES).toEqual(["src/builder/Builder.tsx", "src/builder/Ids.tsx", "src/builder/model.ts"]);
    for (const file of FILES) {
      expect(hookProblems(PROGRAM, file), file).toEqual([]);
      expect(typedProblems(PROGRAM, file), file).toEqual([]);
    }
    const builder = read("src/builder/Builder.tsx");
    expect(builder.split("loader(").length - 1).toBe(3);
    expect(builder.split("useSyncExternalStore(").length - 1).toBe(3);
  });

  it.each(CONTROLS.map(([name, , problems], index) => [name, index, problems] as const))("refuses %s, by exactly its rule", (_name, index, problems) => {
    expect(hookProblems(PROGRAM, `src/builder/Probe${String(index)}.tsx`).map((one) => one.problem)).toEqual(problems);
    expect(typedProblems(PROGRAM, `src/builder/Probe${String(index)}.tsx`)).toEqual([]);
  });

  it("accepts the allowed spellings: loader by any name (in parentheses too), and setters, called in a JSX on* handler (a component's on* prop included); a Typed value as a Field's value", () => {
    expect(hookProblems(PROGRAM, "src/builder/Accepted.tsx")).toEqual([]);
    expect(typedProblems(PROGRAM, "src/builder/Accepted.tsx")).toEqual([]);
  });
});

describe("what the person typed, by type", () => {
  it.each(TYPED.map(([name], index) => [name, index] as const))("refuses %s, by exactly its rule", (_name, index) => {
    expect(typedProblems(PROGRAM, `src/builder/TypedProbe${String(index)}.tsx`).map((one) => one.problem)).toEqual([PROBLEMS.typed]);
    expect(hookProblems(PROGRAM, `src/builder/TypedProbe${String(index)}.tsx`)).toEqual([]);
  });
});
