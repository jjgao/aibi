/**
 * The display lint's corpora (D429, `scripts/lint/display.mjs`), shared by the rows' tests
 * (`tests/unit/lint-display.test.ts`, R90–R93) and the tables' (`tests/gates/display-lint.test.ts`).
 * Each corpus is one probe file: a header (imports, declarations) and cases, each on lines of its
 * own; `lintCases` lints it through `eslint.config.mjs` (the canary: a rule that the configuration
 * does not wire refuses nothing) and gives each case the `display/*` rules reported on its lines.
 * A case lists the rules that must refuse it (a `display/*` rule by its short name, another rule
 * as `eslint:<id>`), and may name a text one of the `display/*` messages on its lines must hold
 * (`said`: the branch it stands for); a control lists none, and no `display/*` rule may report on
 * its lines. A residual or an OPEN row is a case the rules do NOT refuse, asserted open, with its
 * reason (D429's residuals and its OPEN flow class, each with its probe; no assertion, no `any`).
 */
import path from "node:path";

import { Linter } from "./linter";
import { WEB } from "./program";

/** A case of a corpus: its code (one or more lines), and the `display/*` rules that must refuse it
 * (none: a control, which no `display/*` rule may report). */
export interface Case {
  readonly id: string;
  readonly code: string;
  readonly refused: readonly string[];
  /** A text one of the case's `display/*` messages must hold. */
  readonly said?: string;
}

/** The probe paths the tests lint at (no file on disk: the project service's default project). */
export const PROBES = [
  "src/catalogue/probe-display.tsx",
  "src/dataset/probe-display.tsx",
  "src/layout/probe-display.tsx",
  "src/harness/probe-display.tsx",
  "src/api/probe-display.ts",
  "src/api/probe-display.tsx",
  "src/operator/probe-display.tsx",
];

/** A linter through `eslint.config.mjs`: its rules as configured, the parser told of the probes. */
export function displayLinter(): Linter {
  return new Linter({
    cwd: WEB,
    overrideConfig: {
      languageOptions: {
        parserOptions: {
          projectService: {
            allowDefaultProject: PROBES,
            defaultProject: "tsconfig.app.json",
            maximumDefaultProjectFileMatchCount_THIS_WILL_SLOW_DOWN_LINTING: 16,
          },
        },
      },
    },
  });
}

/** What a corpus came to: each case's `display/*` rules (short names), its other rules (`eslint:<id>`)
 * and its `display/*` messages, the header's rules, and any parse failure. */
export interface Linted {
  readonly cases: ReadonlyMap<string, readonly string[]>;
  readonly others: ReadonlyMap<string, readonly string[]>;
  readonly messages: ReadonlyMap<string, readonly string[]>;
  readonly header: readonly string[];
  readonly fatal: readonly string[];
}

/** Lint `header` and `cases` as the file `file`; each case's reports are those on its own lines. */
export async function lintCases(linter: Linter, file: string, header: string, cases: readonly Case[]): Promise<Linted> {
  const lines: string[] = header.split("\n");
  const ranges = new Map<string, readonly [number, number]>();
  for (const one of cases) {
    const start = lines.length + 1;
    lines.push(...one.code.split("\n"));
    ranges.set(one.id, [start, lines.length]);
  }
  const [result] = await linter.lintText(`${lines.join("\n")}\n`, { filePath: path.join(WEB, file) });
  const messages = result?.messages ?? [];
  // A reviewed site's count is the file's, not a case's (`Program:exit`): it goes with the header.
  const display = messages.flatMap((message) => (message.ruleId?.startsWith("display/") === true ? [{ line: message.messageId === "site" ? 0 : message.line, rule: message.ruleId.slice("display/".length), text: message.message }] : []));
  const other = messages.flatMap((message) => (message.ruleId !== null && !message.ruleId.startsWith("display/") ? [{ line: message.line, rule: `eslint:${message.ruleId}` }] : []));
  const found = new Map<string, string[]>();
  const others = new Map<string, string[]>();
  const said = new Map<string, string[]>();
  for (const [id, [start, end]] of ranges) {
    const within = ({ line }: { readonly line: number }): boolean => line >= start && line <= end;
    found.set(
      id,
      display.filter(within).map(({ rule }) => rule),
    );
    others.set(
      id,
      other.filter(within).map(({ rule }) => rule),
    );
    said.set(
      id,
      display.filter(within).map(({ rule, text }) => `${rule}: ${text}`),
    );
  }
  const first = ranges.size === 0 ? lines.length + 1 : Math.min(...[...ranges.values()].map(([start]) => start));
  return {
    cases: found,
    others,
    messages: said,
    header: display.filter(({ line }) => line < first).map(({ rule }) => rule),
    fatal: messages.filter((message) => message.fatal === true).map((message) => message.message),
  };
}

/** The cases whose reports differ from what they state: a refused case missing a rule it names, a
 * control with any `display/*` report. Empty when the corpus holds. */
export function wrong(linted: Linted, cases: readonly Case[]): string[] {
  return cases.flatMap(({ id, refused, said }) => {
    const got = linted.cases.get(id) ?? [];
    if (refused.length === 0) {
      return got.length === 0 ? [] : [`${id}: a control, reported by ${got.join(", ")}`];
    }
    const all = [...got, ...(linted.others.get(id) ?? [])];
    const missing = refused.filter((rule) => !all.includes(rule));
    if (missing.length > 0) {
      return [`${id}: not refused by ${missing.join(", ")} (got ${all.length === 0 ? "nothing" : all.join(", ")})`];
    }
    const messages = linted.messages.get(id) ?? [];
    return said === undefined || messages.some((message) => message.includes(said)) ? [] : [`${id}: no message says "${said}" (${messages.join(" | ")})`];
  });
}

/** The declarations every raw-text probe reads. */
export const RAW_HEADER = [
  'import { type ComponentClass, type ComponentType, createContext, type ExoticComponent, type ForwardRefExoticComponent, lazy, type MemoExoticComponent, type NamedExoticComponent, type Provider, type ProviderExoticComponent, type ProviderProps, type ReactElement, type ReactNode, Suspense, useContext, useState } from "react";',
  'import type { RouteObject } from "react-router";',
  'import { type DatasetId, type ServerText } from "../api";',
  'import type { Typed } from "../layout/Field";',
  'import { type Key, keyOf } from "../layout/key";',
  'import { Text } from "../layout/Text";',
  "declare const raw: string;",
  "declare const n: number;",
  "declare const big: bigint;",
  "declare const anyv: any;",
  "declare const unk: unknown;",
  "declare const id: DatasetId;",
  "declare const typed: Typed;",
  "declare const key: Key;",
  "declare const box: ServerText;",
  "declare const list: readonly string[];",
  "declare const map: ReadonlyMap<string, string>;",
  "declare const maybe: boolean;",
  "declare const node: ReactNode;",
  "declare const el: ReactElement;",
  "declare const params: Readonly<Partial<Record<string, string>>>;",
  "declare function show(shown: ReactNode): void;",
  'const WORDS = { a: "Fixed words.", b: "Other words." } as const;',
  // Two components that show their own children (the exemption's controls), for the OPEN rows.
  "function Given({ children }: { readonly children: ReactNode }) { return <div>{children}</div>; }",
  "function Row({ children }: { readonly children: string }) { return <p>{children}</p>; }",
].join("\n");

/** R90 at JSX positions and component returns (round 3's 22 rows, round 4's, the class's members,
 * the tags, the own-children exemption's edges, and the controls). */
export const RAW_CASES: readonly Case[] = [
  { id: "C01 a string child", code: "export const c01 = <p>{raw}</p>;", refused: ["raw-text"] },
  { id: "C02 an array of strings", code: "export const c02 = <p>{[raw]}</p>;", refused: ["raw-text"] },
  { id: "C03 a conditional", code: "export const c03 = <p>{maybe ? raw : null}</p>;", refused: ["raw-text"] },
  { id: "C04 a Set", code: "export const c04 = <p>{new Set([raw])}</p>;", refused: ["raw-text"] },
  { id: "C05 a Promise", code: "export const c05 = <p>{Promise.resolve(raw)}</p>;", refused: ["raw-text"] },
  { id: "C06 a type parameter bounded by ReactNode", code: "export function C06<T extends ReactNode>({ v }: { readonly v: T }) { return <p>{v}</p>; }", refused: ["raw-text"] },
  { id: "C07 a ReactNode", code: "export const c07 = <p>{node}</p>;", refused: ["raw-text"] },
  { id: "C08 a string and a structural member", code: "export const c08 = (fake: string & { readonly length: number }) => <p>{fake}</p>;", refused: ["raw-text"] },
  { id: "C09 string & {}", code: "export const c09 = (empty: string & {}) => <p>{empty}</p>;", refused: ["raw-text"] },
  { id: "C11 a children attribute", code: "export const c11 = <p children={raw} />;", refused: ["raw-text", "elements"] },
  { id: "C14 a template", code: "export const c14 = <p>{`${raw}`}</p>;", refused: ["raw-text"] },
  { id: "C16 a map's values", code: "export const c16 = <p>{map.values()}</p>;", refused: ["raw-text"] },
  { id: "C17 a route's parameter", code: 'export const c17 = <p>{params["id"]}</p>;', refused: ["raw-text"] },
  { id: "C22 a generator", code: "function* g22(s: string) { yield s; }\nexport const c22 = <p>{g22(raw)}</p>;", refused: ["raw-text"] },
  { id: "C24 a list", code: "export const c24 = <p>{list}</p>;", refused: ["raw-text"] },
  { id: "C28 Array.from", code: "export const c28 = <p>{Array.from(raw)}</p>;", refused: ["raw-text"] },
  { id: "C32 a list's length (a number)", code: "export const c32 = <p>{list.length}</p>;", refused: ["raw-text"] },
  { id: "C39 a state", code: 'export function C39() { const [s] = useState<string>("x"); return <p>{s}</p>; }', refused: ["raw-text"] },
  { id: "C41 a fragment's child", code: "export const c41 = <>{raw}</>;", refused: ["raw-text"] },
  { id: "C20 a title", code: "export const c20 = <p title={raw}>x</p>;", refused: ["raw-text"] },
  { id: "C35 an href", code: "export const c35 = <a href={raw}>x</a>;", refused: ["raw-text", "elements"] },
  { id: "R4-01 any", code: "export const r401 = <p>{anyv}</p>;", refused: ["raw-text"] },
  { id: "R4-03 bigint", code: "export const r403 = <p>{big}</p>;", refused: ["raw-text"] },
  { id: "a number", code: "export const num = <p>{n}</p>;", refused: ["raw-text"] },
  { id: "unknown, in a component's prop", code: "function Shows({ v }: { readonly v: unknown }) { return <p>{v === null ? null : 1}</p>; }\nexport const unknownProp = <Shows v={unk} />;", refused: ["raw-text"] },
  { id: "unknown in a data attribute", code: "export const unknownData = <p data-x={unk} />;", refused: ["raw-text", "elements"] },
  { id: "C12 a tag of string type", code: "export function C12() { const Tag = raw; return <Tag />; }", refused: ["raw-text"] },
  { id: "C13 a tag of a literal union", code: 'export function C13() { const Lit: "p" | "iframe" = maybe ? "p" : "iframe"; return <Lit />; }', refused: ["raw-text"] },
  { id: "a template literal type of a number", code: "export const tl = (v: `x${number}`) => <p>{v}</p>;", refused: ["raw-text"] },
  { id: "a string mapping", code: "export const up = (v: Uppercase<string>) => <p>{v}</p>;", refused: ["raw-text"] },
  { id: "an object a string is assignable to ({})", code: "export const empty = (v: {}) => <p data-x={v} />;", refused: ["raw-text"] },
  { id: "Object", code: "export const object = (v: Object) => <p data-v={v} />;", refused: ["raw-text"] },
  { id: "a type parameter with no constraint", code: "export function Free<T>(v: T) { return <p data-v={v} />; }", refused: ["raw-text"] },
  { id: "a type too deep to read", code: "export const deep = (v: string[][][][][][][][][][][][][][][][][][]) => <p>{v}</p>;", refused: ["raw-text"], said: "a type too deep to read" },
  { id: "a literal 17 levels deep: past the depth of 16, refused", code: 'export const deep17 = (v: "a"[][][][][][][][][][][][][][][][][]) => <p>{v}</p>;', refused: ["raw-text"], said: "a type too deep to read" },
  { id: "a literal 16 levels deep: read (the depth is 16)", code: 'export const deep16 = (v: "a"[][][][][][][][][][][][][][][][]) => <p>{v}</p>;', refused: [] },
  { id: "an iterable with two iterator signatures, the second of strings", code: 'export const twoIterators = (v: { [Symbol.iterator](): Iterator<"a">; [Symbol.iterator](step: number): Iterator<string> }) => <p data-v={v} />;', refused: ["raw-text"], said: "an iterable of string" },
  { id: "an object a number (not a string) is assignable to", code: "export const numberLike = (v: { toFixed(digits?: number): string }) => <p>{v}</p>;", refused: ["raw-text"], said: "an object type a string or a number is assignable to" },
  { id: "a member of the first parameter other than children", code: "export function S50(props: { readonly label: string }) { return <p>{props.label}</p>; }", refused: ["raw-text"], said: "as a JSX child" },
  {
    id: "a computed key named children that names another prop",
    code: 'function makeKeyed() { const children = "label"; function Keyed({ [children]: shown }: { readonly label: string }) { return <p>{shown}</p>; } return Keyed; }\nexport const keyed = makeKeyed();',
    refused: ["raw-text"],
    said: "as a JSX child",
  },
  { id: "a spread child", code: "export const spreadChild = <p>{...list}</p>;", refused: ["raw-text"] },
  { id: "a brand of the same name, declared elsewhere", code: "declare const datasetBrand: unique symbol;\nexport const forged = (v: string & { readonly [datasetBrand]: true }) => <p>{v}</p>;", refused: ["raw-text"] },
  { id: "a component typed by the library", code: "export const Typed1: ComponentType = () => raw;", refused: ["raw-text"] },
  { id: "what a component named as one returns", code: "export function Count() { return list.length; }", refused: ["raw-text"] },
  { id: "a component a call made, by its tag", code: "const made = (): (() => string) => () => raw;\nconst Made = made();\nexport const madeShown = <Made />;", refused: ["raw-text"] },
  { id: "a lazy component that returns a string", code: "const Lazy = lazy(async () => ({ default: () => raw }));\nexport const lazyShown = <Lazy />;", refused: ["raw-text"] },
  { id: "a component's children given as an attribute", code: "function Own({ children }: { readonly children: ReactNode }) { return <div>{children}</div>; }\nexport const ownAttribute = <Own children={raw} />;", refused: ["raw-text"] },
  { id: "a component's children given as a child", code: "function Own2({ children }: { readonly children: ReactNode }) { return <div>{children}</div>; }\nexport const ownChild = <Own2>{raw}</Own2>;", refused: ["raw-text"] },
  { id: "a component's own children as an attribute", code: "export function Own4({ children }: { readonly children: string }) { return <p title={children}>x</p>; }", refused: ["raw-text"] },
  { id: "a component that returns its children", code: "export function Own5({ children }: { readonly children: ReactNode }) { return children; }", refused: ["raw-text"] },
  { id: "a generic member type as a child", code: 'export function Indexed<T extends { readonly a: string }>({ v }: { readonly v: T["a"] }) { return <p>{v}</p>; }', refused: ["raw-text"] },
  { id: "a conditional type as a child", code: "export function Cond<T>({ v }: { readonly v: T extends string ? T : string }) { return <p>{v}</p>; }", refused: ["raw-text"] },
  { id: "a tag of type any", code: "export function AnyTag() { const Tag: any = anyv; return <Tag />; }", refused: ["raw-text"] },
  { id: "a tag of type unknown", code: "export function UnknownTag() { const Tag: unknown = unk; return <Tag />; }", refused: ["raw-text"] },
  { id: "an iterator that is no function", code: "export const notCallable = (v: { [Symbol.iterator]: string }) => <p data-v={v} />;", refused: ["raw-text"] },
  { id: "an iterator with no next", code: "export const noNextAtAll = (v: { [Symbol.iterator](): object }) => <p data-v={v} />;", refused: ["raw-text"] },
  { id: "an iterator whose results have no value", code: "export const noValue = (v: { [Symbol.iterator](): { next(): { done: false } } }) => <p data-v={v} />;", refused: ["raw-text"] },
  { id: "a generator whose return (done) is a string, yielding literals", code: 'export const doneString = (v: Generator<"a", string, unknown>) => <p>{v}</p>;', refused: [] },
  { id: "a recursive type", code: "type Deep = readonly Deep[];\nexport const recursive = (v: Deep) => <p>{v}</p>;", refused: [] },
  { id: "a symbol given to a component", code: "function Shows2({ v }: { readonly v: symbol }) { return <p>{v === Symbol.iterator ? 1 : 2}</p>; }\nexport const symbolProp = (v: symbol) => <Shows2 v={v} />;", refused: [] },
  { id: "a mapping of an id's template", code: "export const mappedId = (v: Uppercase<`#${DatasetId}`>) => <p>{v}</p>;", refused: [] },
  { id: "a child no scope declares", code: "export const undeclaredChild = <p>{nowhereAtAll}</p>;", refused: ["raw-text"] },
  { id: "a tag of a type parameter with no constraint", code: "export function FreeTag<C>({ as: Tag }: { readonly as: C }) { return <div><Tag /></div>; }", refused: ["raw-text"], said: "unknown as what this component returns" },
  { id: "the children of a name no scope declares", code: "export const undeclared = <p>{nowhere.children}</p>;", refused: ["raw-text"] },
  { id: "an iterator with no next signature", code: "export const noNext = (v: { [Symbol.iterator](): { next: string } }) => <p data-v={v} />;", refused: ["raw-text"] },
  {
    id: "children in a function not named as a component",
    code: "export function label({ children }: { readonly children: string }) { return <p>{children}</p>; }",
    refused: ["raw-text"],
  },
  { id: "a local named children, not the component's own", code: "export function NotOwn() { const children = raw; return <p>{children}</p>; }", refused: ["raw-text"] },
  { id: "a member named children of another object", code: "export function NotOwn2(props: { readonly children: string }, other: { readonly children: string }) { return <p>{props.children === '' ? null : other.children}</p>; }", refused: ["raw-text"] },
  { id: "a component's second parameter's children", code: "export function NotOwn3(_props: object, other: { readonly children: string }) { return <p>{other.children}</p>; }", refused: ["raw-text"] },
  // The controls: what the rule lets through.
  { id: "literals", code: 'export const ok1 = <p title="t" data-x="y">{"x"}{1}{1n}{true}{null}{undefined}words</p>;', refused: [] },
  { id: "a literal union and an as-const table", code: "export const ok2 = <p>{maybe ? WORDS.a : WORDS.b}</p>;", refused: [] },
  { id: "a DatasetId, a Typed and a Key", code: "export const ok3 = <p key={key}>{id}<input value={typed} /></p>;", refused: [] },
  { id: "a template literal type of a DatasetId", code: "export const ok4 = (target: `#/datasets/${DatasetId}`) => <a href={target}>x</a>;", refused: [] },
  { id: "an element, a list of elements, a server text through Text", code: "export const ok5 = <div>{el}{list.map((_, i) => <span key={keyOf(i)}>x</span>)}<Text value={box} /></div>;", refused: [] },
  { id: "a component's own children, destructured", code: "export function Ok6({ children }: { readonly children: ReactNode }) { return <div>{children}</div>; }", refused: [] },
  { id: "a component that returns elements and null", code: "export function Ok8() { return maybe ? <p>x</p> : null; }", refused: [] },
  { id: "never", code: "export const ok9 = <p>{unk as never}</p>;", refused: [] },
  { id: "words outside a render position", code: "export const ok10 = raw.length + n;\nexport function ok11(s: string): string { return s; }", refused: [] },
];

/** Residuals of R90 and R91 (D429): not refused, each named with its probe (no assertion, no `any`). */
export const RAW_RESIDUALS: readonly Case[] = [
  { id: "a literal type minted by array covariance", code: 'const lits: "x"[] = ["x"];\nconst strs: string[] = lits;\nstrs.push(raw);\nexport const covariant = <p>{lits[0]}</p>;', refused: [] },
  { id: "a literal type minted by a method's bivariance", code: 'interface Shows { m(x: string): ReactElement }\nconst impl = { m(x: "a") { return <p>{x}</p>; } };\nconst shows: Shows = impl;\nexport const bivariant = shows.m(raw);', refused: [] },
  { id: "a declaration that lies about what a component returns", code: "declare const Lies: () => ReactElement;\nexport const liesShown = <Lies />;", refused: [] },
];

/** R90's flow class, OPEN (D429; closed by 2b-1L2, which has its own cold plan review): a raw text
 * that reaches a render slot other than at a JSX position. Each is a probe the rule does not refuse
 * (no assertion, no `any`); `tests/unit/lint-display-runtime.test.tsx` renders three of them raw. */
export const RAW_OPEN: readonly Case[] = [
  { id: "a route's element in place", code: 'export const routeInPlace: RouteObject[] = [{ path: "/", element: raw }];', refused: [] },
  { id: "a route's errorElement in place", code: 'export const routeError: RouteObject[] = [{ path: "/", errorElement: raw }];', refused: [] },
  { id: "a route's element built apart", code: "const homeApart = { index: true, element: raw };\nexport const routeApart: RouteObject[] = [homeApart];", refused: [] },
  { id: "a route's children, nested and built apart", code: 'const deepTable = [{ path: "/", children: [{ path: "", children: [{ index: true, element: raw }] }] }];\nexport const routeNested: RouteObject[] = deepTable;', refused: [] },
  { id: "root.render(x): an argument typed as a ReactNode", code: "show(raw);\nshow(n);", refused: [] },
  { id: "a spread argument", code: "show(...[raw]);", refused: [] },
  { id: "a component called as a function", code: "export const called = Row({ children: raw });", refused: [] },
  { id: "a component called as a function, its props built apart", code: "const givenApart = { children: raw };\nexport const calledApart = Row(givenApart);", refused: [] },
  { id: "a component passed as a value (rows.map(Row))", code: "export const mapped = [{ children: raw }].map(Row);", refused: [] },
  { id: "a component through an alias", code: "const Alias = Row;\nexport const aliased = Alias({ children: raw });", refused: [] },
  { id: "a component through call, apply and bind", code: "export const viaCall = Row.call(null, { children: raw });\nexport const viaApply = Row.apply(null, [{ children: raw }]);\nexport const viaBind = Row.bind(null, { children: raw })();", refused: [] },
  { id: "a component in lower case given as a route's Component", code: 'function count() { return list.length; }\nexport const routeComponent: RouteObject[] = [{ path: "/", Component: count }];', refused: [] },
  { id: "a state setter handed to then", code: "export function Later() { const [shown, setShown] = useState<ReactNode>(null); void Promise.resolve(raw).then(setShown); return Given({ children: shown }); }", refused: [] },
  { id: "a library container (a ReadonlyMap of ReactNode)", code: 'const nodesByName: ReadonlyMap<string, ReactNode> = map;\nexport const fromMap = Given({ children: nodesByName.get("a") });', refused: [] },
  { id: "an iterable spread into a route table", code: 'const routeMap = new Map([["a", { path: "/", element: raw }]]);\nexport const spreadRoutes: RouteObject[] = [...routeMap.values()];', refused: [] },
  { id: "an index signature (ArrayLike of ReactNode)", code: "const indexed: ArrayLike<ReactNode> = list;\nexport const fromIndex = Given({ children: indexed[0] });", refused: [] },
  // A tag whose type React declares as an exotic component is exempt and not read (R90f, 2b-1L2): its
  // render function is not read by R90, so a raw text it renders is OPEN.
  { id: "a lazy typed ExoticComponent, rendered", code: "const OpenBody: ExoticComponent = lazy(async () => ({ default: () => raw }));\nexport const exoticLazy = <Suspense fallback={null}><OpenBody /></Suspense>;", refused: [] },
  { id: "a lazy typed NamedExoticComponent, rendered", code: "const OpenNamed: NamedExoticComponent = lazy(async () => ({ default: () => raw }));\nexport const exoticNamed = <Suspense fallback={null}><OpenNamed /></Suspense>;", refused: [] },
  { id: "a ReadonlyMap of lazies typed NamedExoticComponent", code: 'const OPEN_SCREENS: ReadonlyMap<string, NamedExoticComponent> = new Map([["a", lazy(async () => ({ default: () => list.length }))]]);\nexport function OpenScreen() { const Got = OPEN_SCREENS.get("a"); return Got === undefined ? null : <Got />; }', refused: [] },
  { id: "a context typed ExoticComponent", code: "const OPEN_VIEW = createContext<ExoticComponent>(lazy(async () => ({ default: () => raw })));\nexport function OpenView() { const View = useContext(OPEN_VIEW); return <Suspense fallback={null}><View /></Suspense>; }", refused: [] },
  { id: "a lazy typed Provider<string>", code: "const OpenProvider: Provider<string> = lazy(async () => ({ default: (props: ProviderProps<string>) => props.value }));\nexport const exoticProvider = <OpenProvider value=\"a\" />;", refused: [] },
  { id: "a lazy typed ProviderExoticComponent", code: "const OpenProviderExotic: ProviderExoticComponent<{ readonly v: string }> = lazy(async () => ({ default: ({ v }: { readonly v: string }) => v }));\nexport const exoticProviderExotic = <OpenProviderExotic v={\"a\"} />;", refused: [] },
  { id: "a declared ForwardRefExoticComponent", code: "declare const OpenForward: ForwardRefExoticComponent<object>;\nexport const exoticForward = <OpenForward />;", refused: [] },
  { id: "a declared MemoExoticComponent of a function that returns a string", code: "declare const OpenMemo: MemoExoticComponent<() => string>;\nexport const exoticMemo = <OpenMemo />;", refused: [] },
  { id: "an app interface that extends ExoticComponent", code: "interface OpenExtends extends ExoticComponent { (): string }\ndeclare const OpenExt: OpenExtends;\nexport const exoticExtends = <OpenExt />;", refused: [] },
  { id: "an intersection with ExoticComponent", code: "declare const OpenIntersect: (() => string) & ExoticComponent;\nexport const exoticIntersect = <OpenIntersect />;", refused: [] },
  { id: "an app interface that extends ExoticComponent and declares _result", code: "interface OpenResult extends ExoticComponent { readonly _result: () => string }\ndeclare const OpenRes: OpenResult;\nexport const exoticResult = <OpenRes />;", refused: [] },
  { id: "a ComponentClass as a tag (no app can make one)", code: "declare const OpenClass: ComponentClass;\nexport const exoticClass = <OpenClass />;", refused: [] },
  { id: "a ReactNode-typed binding or member given a raw text, then handed to a slot", code: "export function setChildren(props: { children: ReactNode }): void { props.children = raw; }\nconst shownLater: ReactNode = raw;\nshow(shownLater);", refused: [] },
];

/** The declarations every DOM probe reads. */
export const DOM_HEADER = [
  "declare const raw: string;",
  "declare const k: string;",
  "declare const el: HTMLElement;",
  "declare const select: HTMLSelectElement;",
  "declare const input: HTMLInputElement;",
  "declare const maybe: HTMLElement | null;",
].join("\n");

/** R91: the DOM's text APIs, the dialogs, the sheets (round 3's 18 sinks, round 4's, and every
 * spelling around a call: an alias, `.call`, a destructuring, a computed member). */
export const DOM_CASES: readonly Case[] = [
  { id: "D01 textContent", code: "el.textContent = raw;", refused: ["dom"] },
  { id: "D02 append", code: "el.append(raw);", refused: ["dom"] },
  { id: "D03 insertAdjacentText", code: 'el.insertAdjacentText("beforeend", raw);', refused: ["dom"] },
  { id: "D04 createTextNode", code: "el.appendChild(document.createTextNode(raw));", refused: ["dom"] },
  { id: "D05 innerText", code: "el.innerText = raw;", refused: ["dom"] },
  { id: "D06 title", code: "el.title = raw;", refused: ["dom"] },
  { id: "D07 ariaLabel", code: "el.ariaLabel = raw;", refused: ["dom"] },
  { id: "D08 dataset", code: 'el.dataset["x"] = raw;', refused: ["dom"] },
  { id: "D09 setAttribute", code: 'el.setAttribute("title", raw);', refused: ["dom"] },
  { id: "D10 replaceChildren", code: "el.replaceChildren(raw);", refused: ["dom"] },
  { id: "D11 before", code: "el.before(raw);", refused: ["dom"] },
  { id: "D12 document.body.append", code: "document.body.append(el);", refused: ["dom"] },
  { id: "D13 alert", code: "alert(raw);", refused: ["dom"] },
  { id: "D14 window.confirm", code: "window.confirm(raw);", refused: ["dom"] },
  { id: "D15 Notification", code: "export const n15 = new Notification(raw);", refused: ["dom"] },
  { id: "D16 outerText", code: "el.outerText = raw;", refused: ["dom"] },
  { id: "D17 a text node's data", code: 'export const t17 = document.createTextNode("");\nt17.data = raw;', refused: ["dom"] },
  { id: "D18 style.setProperty", code: 'el.style.setProperty("--x", raw);', refused: ["dom"] },
  { id: "prompt", code: "export const p = prompt(raw);", refused: ["dom"] },
  { id: "navigator.share (a native sheet)", code: "void navigator.share({ text: raw });", refused: ["dom"] },
  { id: "speech", code: "speechSynthesis.speak(new SpeechSynthesisUtterance(raw));", refused: ["dom"] },
  { id: "the navigation's timing entry", code: 'export const entries = performance.getEntriesByType("navigation");', refused: ["dom"] },
  { id: "a hashchange listener", code: 'addEventListener("hashchange", () => undefined);\nwindow.addEventListener("hashchange", () => undefined);', refused: ["dom"] },
  { id: "an option added", code: "select.options.add(new Option(raw));", refused: ["dom"] },
  { id: "an option written", code: "select.options[0] = new Option(raw);", refused: ["dom"] },
  { id: "a class added", code: "el.classList.add(raw);", refused: ["dom"] },
  { id: "execCommand", code: 'document.execCommand("insertText", false, raw);', refused: ["dom"] },
  { id: "a method as a value", code: "export const f = el.append;", refused: ["dom"] },
  { id: "a method through call", code: "el.append.call(el, raw);", refused: ["dom"] },
  { id: "a method destructured", code: "export const { append } = el;", refused: ["dom"] },
  { id: "a dialog as a value", code: "export const a = alert;", refused: ["dom"] },
  { id: "a write through an array destructuring", code: "[el.title] = [raw];", refused: ["dom"] },
  { id: "a write through an object destructuring", code: "({ t: el.title } = { t: raw });", refused: ["dom"] },
  { id: "a write as a loop's variable", code: "for (el.title of [raw]) { break; }", refused: ["dom"] },
  { id: "a write as a default's target", code: "[el.title = raw] = [];", refused: ["dom"] },
  { id: "a write as a rest element", code: "export const rest = () => { [...el.title] = raw; };", refused: ["dom"] },
  { id: "a method destructured and renamed", code: "export const { append: add } = el;", refused: ["dom"] },
  { id: "a member destructured by a key named at run time", code: "export const { [k]: picked } = el as unknown as Record<string, unknown> & HTMLElement;", refused: ["dom"] },
  { id: "a reviewed name on a receiver one member of which is unreviewed", code: "declare const either: AbortController | FileReader;\neither.abort();", refused: ["dom"] },
  { id: "a computed write", code: 'el["textContent"] = raw;', refused: ["dom"] },
  { id: "a computed member named at run time", code: "export const g = el[k as keyof HTMLElement];", refused: ["dom"] },
  { id: "a write through an optional chain's object", code: "if (maybe !== null) { maybe.textContent = raw; }", refused: ["dom"] },
  { id: "a field's custom validity", code: "input.setCustomValidity(raw);", refused: ["dom"] },
  { id: "a timer (deny by default: not reviewed)", code: "setTimeout(() => undefined, 0);", refused: ["dom"] },
  { id: "a timer's extra arguments handed to a function (R90f's spelling; refused outside SITES, D431)", code: "declare const setWords: (words: string) => void;\nsetTimeout(setWords, 0, raw);", refused: ["dom"] },
  { id: "a timer cleared (not reviewed outside SITES, D431)", code: "clearTimeout(0);", refused: ["dom"] },
  { id: "the title", code: "document.title = raw;", refused: ["dom"] },
  { id: "a type of the app's that extends one of the DOM's", code: "interface Mine extends HTMLElement { readonly mine: true }\ndeclare const mine: Mine;\nmine.title = raw;", refused: ["dom"] },
  { id: "a type parameter bounded by one of the DOM's", code: "export function bounded<T extends HTMLElement>(e: T): void { e.title = raw; }", refused: ["dom"] },
  { id: "a shorthand property of a dialog", code: "export const shorthand = { alert };", refused: ["dom"], said: "alert" },
  { id: "an import-equals of a dialog", code: "import alertAlias = globalThis.alert;", refused: ["dom"] },
  { id: "a use of that alias, by the symbol it names", code: "export const aliasUsed = alertAlias;", refused: ["dom"], said: "alert" },
  { id: "a static of a global's type literal, by its key", code: "void Notification.requestPermission();", refused: ["dom"], said: "Notification.requestPermission" },
  // The controls: reads.
  { id: "reads", code: "export const r2 = [el.textContent, input.value, el.title, select.selectedIndex];", refused: [] },
];

/** R91's residuals (D429): a DOM value flowed into a type that does not say so (a structural type, a
 * type parameter), each asserted open. */
export const DOM_RESIDUALS: readonly Case[] = [
  { id: "a structural parameter's write", code: "function write(x: { title: string }): void { x.title = raw; }\nwrite(el);", refused: [] },
  { id: "a generic setter", code: "function setOf<T, K extends keyof T>(o: T, k: K, v: T[K]): void { o[k] = v; }\nsetOf(el, \"title\", raw);", refused: [] },
  { id: "a local variable typed structurally", code: "const structural: { title: string } = el;\nstructural.title = raw;", refused: [] },
];

/** The declarations every address probe reads. */
export const ADDRESS_HEADER = [
  "declare const el: HTMLElement;",
  "declare const link: HTMLAnchorElement;",
  "declare const base: HTMLBaseElement;",
  "declare const change: HashChangeEvent;",
  "declare const entry: PerformanceEntry;",
  "declare const url: URL;",
  "declare const form: HTMLFormElement;",
  "declare const button: HTMLButtonElement;",
  "declare const field: HTMLInputElement;",
  "declare const request: Request;",
].join("\n");

/** R92: the address's members, by type (round 3's 7 reads, D428's open readers). */
export const ADDRESS_CASES: readonly Case[] = [
  { id: "location.pathname", code: "export const a1 = location.pathname;", refused: ["address"] },
  { id: "location.search", code: "export const a2 = location.search;", refused: ["address"] },
  { id: "location.hash and href", code: "export const a3 = [location.hash, location.href];", refused: ["address"] },
  { id: "location['search']", code: 'export const a4 = location["search"];', refused: ["address"] },
  { id: "location.toString()", code: "export const a5 = location.toString();", refused: ["address"] },
  { id: "document.URL", code: "export const a6 = document.URL;", refused: ["address"] },
  { id: "document.documentURI", code: "export const a7 = document.documentURI;", refused: ["address"] },
  { id: "document.referrer", code: "export const a8 = document.referrer;", refused: ["address"] },
  { id: "document.baseURI", code: "export const a9 = document.baseURI;", refused: ["address"] },
  { id: "window.location.search", code: "export const a10 = window.location.search;", refused: ["address"] },
  { id: "document.location.pathname", code: "export const a11 = document.location.pathname;", refused: ["address"] },
  { id: "an element's baseURI", code: "export const a12 = el.baseURI;", refused: ["address"] },
  { id: "a link's href (an empty one is the address)", code: "export const a13 = [link.href, link.pathname, link.search, link.hash, link.toString()];", refused: ["address"] },
  { id: "a base element's href", code: "export const a14 = base.href;", refused: ["address"] },
  { id: "a hashchange's URLs", code: "export const a15 = [change.oldURL, change.newURL];", refused: ["address"] },
  { id: "the navigation entry's name", code: "export const a16 = entry.name;", refused: ["address"] },
  { id: "a destructured member", code: "export const { baseURI } = el;", refused: ["address"] },
  { id: "a URL's query, iterated whole (m2 of round 3)", code: "export const query = Object.fromEntries(url.searchParams);\nexport const queried: string[] = [];\nfor (const [, v] of url.searchParams) { queried.push(v); }", refused: ["address"], said: "URL.searchParams" },
  { id: "a URL's and a link's user and password", code: "export const credentials = [url.username, url.password, link.username, link.password];", refused: ["address"], said: "URL.username" },
  // The controls: the server's own members.
  { id: "the origin, the host, the title", code: "export const ok = [location.origin, location.host, location.protocol, document.title];", refused: [] },
];

/** R92: one row for each entry of `ADDRESS`, written out by hand and not generated from the table: removing an entry
 * from the table fails its row (a generated row would go with it), and a test holds that these rows name exactly the table's keys. */
export const ADDRESS_ENTRY_CASES: readonly Case[] = [
  { id: "Document.URL", code: "export const entry0 = document.URL;", refused: ["address"], said: "Document.URL" },
  { id: "Document.documentURI", code: "export const entry1 = document.documentURI;", refused: ["address"], said: "Document.documentURI" },
  { id: "Document.location", code: "export const entry2 = document.location;", refused: ["address"], said: "Document.location" },
  { id: "Document.referrer", code: "export const entry3 = document.referrer;", refused: ["address"], said: "Document.referrer" },
  { id: "HTMLBaseElement.href", code: "export const entry4 = base.href;", refused: ["address"], said: "HTMLBaseElement.href" },
  { id: "HTMLHyperlinkElementUtils.hash", code: "export const entry5 = link.hash;", refused: ["address"], said: "HTMLHyperlinkElementUtils.hash" },
  { id: "HTMLHyperlinkElementUtils.href", code: "export const entry6 = link.href;", refused: ["address"], said: "HTMLHyperlinkElementUtils.href" },
  { id: "HTMLHyperlinkElementUtils.password", code: "export const entry7 = link.password;", refused: ["address"], said: "HTMLHyperlinkElementUtils.password" },
  { id: "HTMLHyperlinkElementUtils.pathname", code: "export const entry8 = link.pathname;", refused: ["address"], said: "HTMLHyperlinkElementUtils.pathname" },
  { id: "HTMLHyperlinkElementUtils.search", code: "export const entry9 = link.search;", refused: ["address"], said: "HTMLHyperlinkElementUtils.search" },
  { id: "HTMLHyperlinkElementUtils.toString", code: "export const entry10 = link.toString();", refused: ["address"], said: "HTMLHyperlinkElementUtils.toString" },
  { id: "HTMLHyperlinkElementUtils.username", code: "export const entry11 = link.username;", refused: ["address"], said: "HTMLHyperlinkElementUtils.username" },
  { id: "HTMLButtonElement.formAction", code: "export const entry12 = button.formAction;", refused: ["address"], said: "HTMLButtonElement.formAction" },
  { id: "HTMLFormElement.action", code: "export const entry13 = form.action;", refused: ["address"], said: "HTMLFormElement.action" },
  { id: "HTMLInputElement.formAction", code: "export const entry14 = field.formAction;", refused: ["address"], said: "HTMLInputElement.formAction" },
  { id: "HashChangeEvent.newURL", code: "export const entry15 = change.newURL;", refused: ["address"], said: "HashChangeEvent.newURL" },
  { id: "HashChangeEvent.oldURL", code: "export const entry16 = change.oldURL;", refused: ["address"], said: "HashChangeEvent.oldURL" },
  { id: "Location.hash", code: "export const entry17 = location.hash;", refused: ["address"], said: "Location.hash" },
  { id: "Location.href", code: "export const entry18 = location.href;", refused: ["address"], said: "Location.href" },
  { id: "Location.pathname", code: "export const entry19 = location.pathname;", refused: ["address"], said: "Location.pathname" },
  { id: "Location.search", code: "export const entry20 = location.search;", refused: ["address"], said: "Location.search" },
  { id: "Location.toString", code: "export const entry21 = location.toString();", refused: ["address"], said: "Location.toString" },
  { id: "Node.baseURI", code: "export const entry22 = el.baseURI;", refused: ["address"], said: "Node.baseURI" },
  { id: "PerformanceEntry.name", code: "export const entry23 = entry.name;", refused: ["address"], said: "PerformanceEntry.name" },
  { id: "Request.url", code: "export const entry24 = request.url;", refused: ["address"], said: "Request.url" },
  { id: "URL.hash", code: "export const entry25 = url.hash;", refused: ["address"], said: "URL.hash" },
  { id: "URL.href", code: "export const entry26 = url.href;", refused: ["address"], said: "URL.href" },
  { id: "URL.password", code: "export const entry27 = url.password;", refused: ["address"], said: "URL.password" },
  { id: "URL.pathname", code: "export const entry28 = url.pathname;", refused: ["address"], said: "URL.pathname" },
  { id: "URL.search", code: "export const entry29 = url.search;", refused: ["address"], said: "URL.search" },
  { id: "URL.searchParams", code: "export const entry30 = url.searchParams;", refused: ["address"], said: "URL.searchParams" },
  { id: "URL.toJSON", code: "export const entry31 = url.toJSON();", refused: ["address"], said: "URL.toJSON" },
  { id: "URL.toString", code: "export const entry32 = url.toString();", refused: ["address"], said: "URL.toString" },
  { id: "URL.username", code: "export const entry33 = url.username;", refused: ["address"], said: "URL.username" },
  { id: "Window.location", code: "export const entry34 = window.location;", refused: ["address"], said: "Window.location" },
];

/** The declarations every answers probe reads. */
export const ANSWERS_HEADER = ['import type { Outcome } from "../api/operator";', "declare const outcome: Outcome;"].join("\n");

/** R93: an operator answer's raw parts, by name. */
export const ANSWERS_CASES: readonly Case[] = [
  { id: "a member", code: 'export const b1 = outcome.kind === "answer" ? outcome.body : null;', refused: ["answers"] },
  { id: "an optional member", code: 'export const b2 = outcome.kind === "refused" ? outcome?.codes : null;', refused: ["answers"] },
  { id: "a computed literal", code: 'export const b3 = outcome.kind === "answer" ? outcome["body"] : null;', refused: ["answers"] },
  { id: "a computed template", code: 'export const b4 = outcome.kind === "refused" ? outcome[`codes`] : null;', refused: ["answers"] },
  { id: "a destructuring", code: 'export const b5 = outcome.kind === "answer" ? (({ body }) => body)(outcome) : null;', refused: ["answers"] },
  { id: "a renamed destructuring", code: 'export function b6(): unknown { if (outcome.kind !== "refused") { return null; } const { codes: c } = outcome; return c; }', refused: ["answers"] },
  { id: "a string key destructured", code: 'export function b7(): unknown { if (outcome.kind !== "answer") { return null; } const { "body": b } = outcome; return b; }', refused: ["answers"] },
  // Past R93's names, and refused where the text lands (R90).
  { id: "the values of an outcome, shown", code: "export const v1 = <p>{Object.values(outcome).join()}</p>;", refused: ["raw-text"] },
  { id: "an outcome as JSON, shown", code: "export const v2 = <p>{JSON.stringify(outcome)}</p>;", refused: ["raw-text"] },
  { id: "a member named at run time, shown", code: 'declare const which: "kind";\nexport const v3 = <p>{outcome[which]}</p>;', refused: [] },
  // The control: the outcome's closed words, and a type.
  { id: "the kind, and a type", code: 'export const ok = outcome.kind;\nexport type Body = Extract<Outcome, { readonly kind: "answer" }>["body"];', refused: [] },
];

/** The elements and attributes (`display/elements`). */
export const ELEMENT_HEADER = ["declare const raw: string;", "declare const words: string;", "declare const props: { readonly title: string };", 'import type { Typed } from "../layout/Field";', "declare const typed: Typed;", "declare const r: { current: HTMLElement | null };"].join("\n");

export const ELEMENT_CASES: readonly Case[] = [
  { id: "an iframe", code: 'export const e1 = <iframe src="/x" />;', refused: ["elements"] },
  { id: "an image (a channel: its src)", code: 'export const e2 = <img src="/x" alt="" />;', refused: ["elements"] },
  { id: "a style element", code: "export const e3 = <style>{'p{}'}</style>;", refused: ["elements"] },
  { id: "a meta refresh", code: 'export const e4 = <meta httpEquiv="refresh" content="0" />;', refused: ["elements"] },
  { id: "a base", code: 'export const e5 = <base href="/" />;', refused: ["elements"] },
  { id: "a title", code: "export const e6 = <title>t</title>;", refused: ["elements"] },
  { id: "an option outside Option.tsx", code: 'export const e7 = <select><option value="a">a</option></select>;', refused: ["elements"] },
  { id: "a spread on an element", code: "export const e8 = <p {...props} />;", refused: ["elements"] },
  { id: "a spread on a component", code: "function C({ title }: { readonly title: string }) { return <p>{title === '' ? 'a' : 'b'}</p>; }\nexport const e9 = <C {...props} />;", refused: ["elements"] },
  { id: "a role of type string", code: "export const e22 = <p role={words}>x</p>;", refused: ["elements"] },
  { id: "a className of a template of an id (a brand is a hole of href alone)", code: 'import type { DatasetId } from "../api";\nexport const e23 = (v: `x-${DatasetId}`) => <p className={v}>x</p>;', refused: ["elements"] },
  { id: "a className of a template with a free hole", code: "export const e21 = (v: `x${string}`) => <p className={v}>x</p>;", refused: ["elements"] },
  { id: "an attribute off the list", code: 'export const e10 = <a href="/" target="_blank">x</a>;', refused: ["elements"] },
  { id: "hidden, off the list (it hides what the page says)", code: "export const e10b = <p hidden={true}>x</p>;", refused: ["elements"] },
  { id: "style", code: "export const e11 = <p style={{ color: 'red' }}>x</p>;", refused: ["elements"] },
  { id: "a ref outside the reviewed modules", code: "export const e12 = <p ref={r} />;", refused: ["elements"] },
  { id: "a className of type string", code: "export const e13 = <p className={words}>x</p>;", refused: ["elements"] },
  { id: "a className of a brand", code: "export const e14 = <p className={typed}>x</p>;", refused: ["elements"] },
  { id: "an aria-label of type string", code: "export const e15 = <p aria-label={words}>x</p>;", refused: ["elements"] },
  { id: "a data attribute of a number", code: "export const e16 = <p data-n={raw.length}>x</p>;", refused: ["elements"] },
  { id: "a namespaced attribute", code: 'export const e17 = <p xlink:href="x">x</p>;', refused: ["elements"] },
  { id: "a namespaced attribute in data-'s and aria-'s namespace", code: 'export const e19 = <p data-x:y="1" aria-x:y="2">x</p>;', refused: ["elements"] },
  { id: "an event handler off the list", code: "export const e18 = <p onMouseOver={() => undefined}>x</p>;", refused: ["elements"] },
  { id: "a namespaced tag", code: "export const e24 = <x:p />;", refused: ["elements"], said: "The element <x:p>" },
  { id: "dangerouslySetInnerHTML on an element", code: "export const e25 = <p dangerouslySetInnerHTML={{ __html: raw }} />;", refused: ["elements", "eslint:no-restricted-syntax"], said: "The attribute dangerouslySetInnerHTML" },
  {
    id: "dangerouslySetInnerHTML on a component",
    code: 'function Html(props: { readonly dangerouslySetInnerHTML: { readonly __html: string } }) { return <p>{props.dangerouslySetInnerHTML.__html === "" ? "a" : "b"}</p>; }\nexport const e26 = <Html dangerouslySetInnerHTML={{ __html: raw }} />;',
    refused: ["eslint:no-restricted-syntax"],
  },
  // The controls.
  { id: "literals, a literal union, words", code: 'export const ok = <section aria-label="a" className={raw === "" ? "x" : "y"} data-x="z"><p role="status" title="t">x</p><button type="button" disabled={false} onClick={() => undefined}>b</button></section>;', refused: [] },
  { id: "an href of a template of a DatasetId alone", code: 'import type { DatasetId as Bare1 } from "../api";\ndeclare const bare1: `${Bare1}`;\nexport const e27 = <a href={bare1}>x</a>;', refused: ["elements"] },
  { id: "an href protocol-relative from a DatasetId", code: 'import type { DatasetId as Far } from "../api";\ndeclare const far: `//${Far}`;\nexport const e28 = <a href={far}>x</a>;', refused: ["elements"], said: "href takes a literal" },
  { id: "an href with a scheme from a DatasetId", code: 'import type { DatasetId as Scheme } from "../api";\ndeclare const scheme: `${Scheme}:x`;\nexport const e29 = <a href={scheme}>x</a>;', refused: ["elements"] },
  { id: "an href of the dataset prefix and more after the id", code: 'import type { DatasetId as After } from "../api";\ndeclare const after: `#/datasets/${After}/x`;\nexport const e30 = <a href={after}>x</a>;', refused: ["elements"] },
  { id: "an href of another prefix and a DatasetId", code: 'import type { DatasetId as Other } from "../api";\ndeclare const other: `#/x/${Other}`;\nexport const e31 = <a href={other}>x</a>;', refused: ["elements"] },
  { id: "an href of the dataset prefix and a Typed", code: "declare const typedRef: `#/datasets/${Typed}`;\nexport const e32 = <a href={typedRef}>x</a>;", refused: ["elements"] },
  { id: "an href of the dataset prefix and a Key", code: 'import type { Key as KeyRef } from "../layout/key";\ndeclare const keyRef: `#/datasets/${KeyRef}`;\nexport const e33 = <a href={keyRef}>x</a>;', refused: ["elements"] },
  { id: "an href of the dataset prefix and two ids", code: 'import type { DatasetId as Twice } from "../api";\ndeclare const twice: `#/datasets/${Twice}${Twice}`;\nexport const e35 = <a href={twice}>x</a>;', refused: ["elements"] },
  { id: "a className of href's template (the template is href's alone)", code: 'import type { DatasetId as Classed } from "../api";\ndeclare const classed: `#/datasets/${Classed}`;\nexport const e36 = <p className={classed}>x</p>;', refused: ["elements"], said: "className takes a literal" },
  { id: "an href with text before the prefix (an off-site address)", code: 'import type { DatasetId as Off } from "../api";\ndeclare const off: `//evil.example/#/datasets/${Off}`;\nexport const e37 = <a href={off}>x</a>;', refused: ["elements"], said: "href takes a literal" },
  { id: "an href with text between the prefix and the id", code: 'import type { DatasetId as Mid } from "../api";\ndeclare const mid: `#/datasets/x/${Mid}`;\nexport const e38 = <a href={mid}>x</a>;', refused: ["elements"], said: "href takes a literal" },
  // A branded value beside `undefined` or `null` is no literal (a conditional's branch, a union).
  { id: "a className given a DatasetId or undefined", code: 'import type { DatasetId as Maybe1 } from "../api";\ndeclare const maybeId1: Maybe1;\nexport const e39 = <p className={raw === "" ? maybeId1 : undefined}>x</p>;', refused: ["elements"], said: "className takes a literal" },
  { id: "an href given a DatasetId or undefined", code: 'import type { DatasetId as Maybe2 } from "../api";\ndeclare const maybeId2: Maybe2;\nexport const e40 = <a href={raw === "" ? maybeId2 : undefined}>x</a>;', refused: ["elements"], said: "href takes a literal" },
  { id: "a data attribute given a DatasetId or null", code: 'import type { DatasetId as Maybe3 } from "../api";\ndeclare const maybeId3: Maybe3;\nexport const e41 = <p data-id={raw === "" ? maybeId3 : null}>x</p>;', refused: ["elements"], said: "data-id takes a literal" },
  { id: "a className given a string or undefined, and a Typed or null", code: 'export const e42 = [<p key="a" className={raw === "" ? words : undefined}>x</p>, <p key="b" className={raw === "" ? typed : null}>x</p>];', refused: ["elements"], said: "className takes a literal" },
  { id: "attribute prefixes without their dash", code: 'export const e34 = <p dataa="x" ariaa="y">x</p>;', refused: ["elements"], said: "The attribute dataa" },
  { id: "an href of a bare DatasetId", code: 'import type { DatasetId } from "../api";\ndeclare const bare: DatasetId;\nexport const e20 = <a href={bare}>x</a>;', refused: ["elements"] },
];

/** The specifiers (`display/specifiers`): each a line of its own. */
export const SPECIFIER_CASES: readonly Case[] = [
  { id: "react/jsx-runtime", code: 'import { jsx } from "react/jsx-runtime";\nexport const j = jsx;', refused: ["specifiers"] },
  { id: "a path into node_modules with backslashes", code: 'import { jsx as j3 } from "..\\\\..\\\\node_modules\\\\react\\\\jsx-runtime.js";\nexport const jb = j3;', refused: ["specifiers"] },
  { id: "jsx-runtime by a path into node_modules", code: 'import { jsx as j2 } from "../../node_modules/react/jsx-runtime.js";\nexport const jj = j2;', refused: ["specifiers"] },
  { id: "createElement", code: 'import { createElement } from "react";\nexport const ce = createElement;', refused: ["specifiers"] },
  { id: "cloneElement and Children", code: 'import { Children, cloneElement } from "react";\nexport const cl = [cloneElement, Children];', refused: ["specifiers"] },
  { id: "a namespace import", code: 'import * as React from "react";\nexport const ns = React;', refused: ["specifiers"] },
  { id: "a default import", code: 'import ReactDefault from "react";\nexport const df = ReactDefault;', refused: ["specifiers"] },
  { id: "a side-effect import", code: 'import "react";', refused: ["specifiers"] },
  { id: "an export-all", code: 'export * from "react";', refused: ["specifiers"] },
  { id: "a re-export of a name off the list", code: 'export { createElement } from "react";', refused: ["specifiers"] },
  { id: "createPortal", code: 'import { createPortal } from "react-dom";\nexport const cp = createPortal;', refused: ["specifiers"] },
  { id: "react-dom/client outside the entries", code: 'import { createRoot } from "react-dom/client";\nexport const cr = createRoot;', refused: ["specifiers"] },
  { id: "react-dom/server, statically", code: 'import { renderToStaticMarkup } from "react-dom/server";\nexport const rs = renderToStaticMarkup;', refused: ["specifiers"] },
  { id: "react-dom/server, dynamically (a literal)", code: 'export const later = () => import("react-dom/server");', refused: ["specifiers"] },
  { id: "a type import of react-dom/server", code: 'export type Server = typeof import("react-dom/server");', refused: ["specifiers"] },
  { id: "a type of the router off its list", code: 'import type { Location } from "react-router";\nexport type L = Location;', refused: ["specifiers"] },
  { id: "an inline type of the router off its list", code: 'import { type Navigation } from "react-router";\nexport type N = Navigation;', refused: ["specifiers"] },
  { id: "a package the app does not name", code: 'import { x } from "some-package";\nexport const sp = x;', refused: ["specifiers"] },
  { id: "an import named at run time (another rule's)", code: 'declare const where: string;\nexport const runtime = () => import(where);', refused: [] },
  { id: "a template specifier", code: "export const templated = () => import(`react-dom/server`);", refused: ["specifiers"] },
  { id: "an absolute path", code: 'import { y } from "/src/api/box";\nexport const ap = y;', refused: ["specifiers"] },
  // The controls.
  { id: "the allowed names, and React's types", code: 'import { Fragment, lazy, StrictMode, Suspense, useState, type FC, type ReactNode } from "react";\nimport { createHashRouter, type RouteObject } from "react-router";\nimport { RouterProvider } from "react-router/dom";\nexport const ok = [Fragment, lazy, StrictMode, Suspense, useState, createHashRouter, RouterProvider];\nexport type Ok = [FC, ReactNode, RouteObject];', refused: [] },
  { id: "a relative import", code: 'import { keyOf } from "../layout/key";\nexport const rel = keyOf;\nexport const dyn = () => import("../layout/key");', refused: [] },
];

/** The declarations of D428's open readers. */
export const OPEN_HEADER = [
  'import { createHashRouter, type RouteObject } from "react-router";',
  "declare const router: ReturnType<typeof createHashRouter>;",
  "declare const el: HTMLElement;",
].join("\n");

/** D428's readers OPEN until 2b-1L ("Residuals": the address past the names the router module's
 * checks see), each where its text lands at a JSX position (R90), a DOM write or call (R91) or by its
 * member (R92); past a JSX position (a route's `element`) they are R90's flow class (`RAW_OPEN`). */
export const OPEN_CASES: readonly Case[] = [
  { id: "router.state.matches[].params in an entry's main", code: 'export const o1 = <p>{router.state.matches.at(-1)?.params["id"]}</p>;', refused: ["raw-text"] },
  {
    id: "patchRoutesOnNavigation's path, in a route it patches",
    code: 'export const o2 = createHashRouter([], { patchRoutesOnNavigation: ({ path, patch }) => { patch(null, [{ path: "x", element: <p>{path}</p> }]); } });',
    refused: ["raw-text"],
  },
  { id: "a middleware's request URL", code: 'export const o3b: RouteObject[] = [{ path: "w", middleware: [({ request }) => { el.title = request.url; }] }];', refused: ["dom"] },
  { id: "shouldRevalidate's currentUrl, in a route literal", code: 'export const o3: RouteObject[] = [{ path: "y", shouldRevalidate: ({ currentUrl }) => { el.title = currentUrl.pathname; return true; } }];', refused: ["dom"] },
  {
    id: "a router handed to a parameter typed structurally",
    code: "export function O4({ r }: { readonly r: { readonly state: { readonly location: { readonly pathname: string } } } }) { return <p>{r.state.location.pathname}</p>; }\nexport const o4 = <O4 r={router} />;",
    refused: ["raw-text"],
  },
  { id: "an element's baseURI", code: "export const o5 = <p>{el.baseURI}</p>;", refused: ["raw-text", "address"] },
  { id: "the navigation entry's name", code: 'export const o6 = <p>{performance.getEntriesByType("navigation")[0]?.name}</p>;', refused: ["raw-text", "dom", "address"] },
  { id: "a hashchange listener's newURL", code: 'addEventListener("hashchange", (event) => { el.textContent = event.newURL; });', refused: ["dom", "address"] },
  { id: "a link's href", code: "export const o8 = <p>{document.links[0]?.href}</p>;", refused: ["raw-text", "address"] },
  // `navigation` is not in TypeScript's DOM library: an error type, which the walk reads as `any`.
  { id: "the Navigation API", code: "export const o9 = <p>{navigation.currentEntry.url}</p>;", refused: ["raw-text"] },
];

// ---------------------------------------------------------------------------------------------
// The generated corpora: each from a table of the rule (`scripts/lint/display.mjs`) or of the
// language's forms (every write of a name, every receiver of a DOM value).

/** The writes ESLint's scope analysis knows of a name, as statements over `@` (the name) and `raw`. */
export const NAME_WRITES: readonly (readonly [string, string])[] = [
  ["an assignment", "@ = raw;"],
  ["a compound assignment", "@ += raw;"],
  ["a nullish assignment", "@ ??= raw;"],
  ["a logical or assignment", "@ ||= raw;"],
  ["a logical and assignment", "@ &&= raw;"],
  ["an update", "@++;"],
  ["a for-of target", "for (@ of [raw]) { break; }"],
  ["a for-in target", "for (@ in { [raw]: 1 }) { break; }"],
  ["an array destructuring", "[@] = [raw];"],
  ["an object destructuring", "({ c: @ } = { c: raw });"],
  ["a default in a destructuring", "[@ = raw] = [];"],
];

/** MA-1 of round 1 of #119: the own-`children` exemption, against every write of the binding or of
 * the props: each is refused (the child is read as any other). */
export function childrenWriteCases(): Case[] {
  const destructured = NAME_WRITES.map(([form, statement], index): Case => ({
    id: `own children, ${form}`,
    code: `export function W${String(index)}({ children }: { children: string }) { ${statement.replaceAll("@", "children")} return <p>{children}</p>; }`,
    refused: ["raw-text"],
  }));
  const memberWrites = NAME_WRITES.map(([form, statement], index): Case => ({
    id: `props.children, ${form} of the member`,
    code: `export function M${String(index)}(props: { children: string }) { ${statement.replaceAll("@", "props.children")} return <p>{props.children}</p>; }`,
    refused: ["raw-text"],
  }));
  const propsWrites = NAME_WRITES.filter(([form]) => !/update|compound|logical|for-in/u.test(form)).map(([form, statement], index): Case => ({
    id: `props, ${form} of the parameter`,
    code: `export function P${String(index)}(props: { children: string }) { ${statement.replaceAll("@", "props").replaceAll("raw", "{ children: raw }")} return <p>{props.children}</p>; }`,
    refused: ["raw-text"],
  }));
  return [
    ...destructured,
    ...memberWrites,
    ...propsWrites,
    { id: "own children, a default", code: "export function D1({ children = raw }: { readonly children?: string }) { return <p>{children}</p>; }", refused: ["raw-text"] },
    { id: "props, a default", code: "export function D2(props: { readonly children: string } = { children: raw }) { return <p>{props.children}</p>; }", refused: ["raw-text"] },
    { id: "own children of a default object pattern", code: "export function D3({ children }: { readonly children: string } = { children: raw }) { return <p>{children}</p>; }", refused: ["raw-text"] },
    { id: "own children, nested in the pattern", code: "export function D4({ p: { children } }: { readonly p: { readonly children: string } }) { return <p>{children}</p>; }", refused: ["raw-text"] },

    { id: "own children of the second parameter", code: "export function D6(_first: object, { children }: { readonly children: string }) { return <p>{children}</p>; }", refused: ["raw-text"] },
    // The controls: unwritten, the exemption holds.
    { id: "own children, unwritten", code: "export function C1({ children }: { readonly children: string }) { return <p>{children}</p>; }", refused: [] },
    // The member form is not exempt (round 3 of #119, m1): `props` is written through an alias or by a callee unseen.
    { id: "props.children, unwritten (the member form is not exempt)", code: "export function C2(props: { readonly children: string }) { return <p>{props.children}</p>; }", refused: ["raw-text"], said: "as a JSX child" },
    { id: "props written through an alias", code: "export function E1(props: { children: string }) { const p = props; p.children = raw; return <p>{props.children}</p>; }", refused: ["raw-text"] },
    { id: "props written by a callee", code: "declare function mutate(o: { children: string }): void;\nexport function E2(props: { children: string }) { mutate(props); return <p>{props.children}</p>; }", refused: ["raw-text"] },
    { id: "props of a ReactNode written through an alias", code: "export function E5(props: { children: ReactNode }) { const p = props; p.children = raw; return <div>{props.children}</div>; }", refused: ["raw-text"] },
    { id: "props[children], a computed key named by a variable", code: 'export function Z9(props: { readonly label: string }) { const children = "label"; return <p>{props[children]}</p>; }', refused: ["raw-text"] },
    { id: "own children, renamed (the key is `children`, unwritten)", code: "export function C4({ children: shown }: { readonly children: string }) { return <p>{shown}</p>; }", refused: [] },
    { id: "a binding named children whose key is another", code: "export function D5({ label: children }: { readonly label: string }) { return <p>{children}</p>; }", refused: ["raw-text"] },
    { id: "own children, the key as a string", code: 'export function C3({ "children": children }: { readonly children: string }) { return <p>{children}</p>; }', refused: [] },
  ];
}

/** R90's walk (`rawIn`) by its constructors × the place of the raw member (MA-2 of round 3 of #119):
 * each constructor holding one raw `string` at each of its places (a tuple's first, middle and last
 * element; each of two signatures, in either order; each member of a union of function types, in
 * either order), refused with the walk's own words, and the same with a literal in that place, a
 * control. Each is shown as a JSX child, and the objects as a component's attribute too. */
export function compositeCases(): Case[] {
  const R = "string";
  const L = '"a"';
  /** A constructor: its id, its type over `@` (the raw member's place, given `R` or `L`), the places, the words. */
  const constructors: readonly (readonly [string, (at: number, held: string) => string, number, string])[] = [
    ["a tuple", (at, held) => `readonly [${[0, 1, 2].map((place) => (place === at ? held : L)).join(", ")}]`, 3, "an array of string"],
    ["an array", (_, held) => `readonly ${held}[]`, 1, "an array of string"],
    ["a promise", (_, held) => `Promise<${held}>`, 1, "a promise of string"],
    ["a template", (_, held) => (held === R ? "`x${string}`" : '"xa"'), 1, "a template of string"],
    ["a string mapping", (_, held) => `Uppercase<${held}>`, 1, "a mapping of string"],
    ["a union of functions", (at, held) => (at === 0 ? `(() => ${held}) | (() => ${L})` : `(() => ${L}) | (() => ${held})`), 2, "a function that returns string"],
    ["a function's call signatures", (at, held) => (at === 0 ? `{ (): ${held}; (step: number): ${L} }` : `{ (): ${L}; (step: number): ${held} }`), 2, "a function that returns string"],
    ["an iterable's iterator signatures", (at, held) => (at === 0 ? `{ [Symbol.iterator](): Iterator<${held}>; [Symbol.iterator](step: number): Iterator<${L}> }` : `{ [Symbol.iterator](): Iterator<${L}>; [Symbol.iterator](step: number): Iterator<${held}> }`), 2, "an iterable of string"],
    [
      "an iterator's next signatures",
      (at, held) => `{ [Symbol.iterator](): { next(): { done: false; value: ${at === 0 ? held : L} }; next(step: number): { done: false; value: ${at === 1 ? held : L} } } }`,
      2,
      "an iterable of string",
    ],
    ["a constraint", (_, held) => `T${held === R ? "s" : "l"}`, 1, "string"],
  ];
  const cases = constructors.flatMap(([name, typeOf, places, said], index) =>
    Array.from({ length: places }, (_, at): Case[] => {
      const n = `${String(index)}_${String(at)}`;
      const shown = (held: string, tag: string): string => {
        const type = typeOf(at, held);
        return /^T[sl]$/u.test(type) ? `export function Cons${tag}${n}<${type} extends ${held}>(v: ${type}) { return <p>{v}</p>; }` : `export const cons${tag}${n} = (v: ${type}) => <p>{v}</p>;`;
      };
      return [
        { id: `${name}, raw at ${String(at)}`, code: shown(R, "R"), refused: ["raw-text"], said },
        { id: `${name}, a literal at ${String(at)} (control)`, code: shown(L, "L"), refused: [] },
      ];
    }).flat(),
  );
  return [
    ...cases,
    // An object a string or a number is assignable to, at a child and at a component's attribute.
    ...[
      ["only a string is assignable to", "{ readonly length: number }"],
      ["only a number is assignable to", "{ toFixed(digits?: number): string }"],
    ].flatMap(([which, type], index): Case[] => [
      { id: `an object ${String(which)}, as a child`, code: `export const assignableChild${String(index)} = (v: ${String(type)}) => <p>{v}</p>;`, refused: ["raw-text"], said: "an object type a string or a number is assignable to" },
      { id: `an object ${String(which)}, at a component's attribute`, code: `function Takes${String(index)}({ v }: { readonly v: unknown }) { return <p>{v === null ? 1 : 2}</p>; }\nexport const assignableProp${String(index)} = (v: ${String(type)}) => <Takes${String(index)} v={v} />;`, refused: ["raw-text"], said: "an object type a string or a number is assignable to" },
    ]),
  ];
}

/** The refused half of the library's component types, a HAND list: the types of `@types/react` and
 * `react-router` that a tag of is read by what it returns (a `ReactNode`) and so refused (MA-1 of
 * round 3). It is not the class: of the ten `react` types with a call or construct signature that
 * pass `tsc` as a tag, five (`ExoticComponent`, `NamedExoticComponent`, `ProviderExoticComponent`,
 * `ForwardRefExoticComponent`, `ComponentClass`) are read as nothing and are OPEN (`RAW_OPEN`, R90f);
 * the checker-enumerated class test of both halves is R90f's (2b-1L2). */
export const REFUSED_LIBRARY_COMPONENT_TYPES: readonly string[] = [
  "ComponentType",
  "ComponentType<{ readonly v?: string }>",
  "FC",
  "FunctionComponent",
  "FunctionComponent<{ readonly at?: number }>",
  "JSXElementConstructor<object>",
  'NonNullable<RouteObject["Component"]>',
  "ComponentType & { readonly mark?: true }",
];

/** Every place a function reaches a tag, over `@` (the component's type) and `#` (a unique name). */
export const TAG_PLACES: readonly (readonly [string, string])[] = [
  ["a prop", "export function Frame#({ Body }: { readonly Body: @ }) { return <div><Body /></div>; }"],
  ["an object's member", "declare const views#: { readonly Home: @ };\nexport const member# = <views#.Home />;"],
  ["a conditional's branch", "declare const a#: @;\nconst Pick# = maybe ? a# : () => null;\nexport const pick# = <Pick# />;"],
  ["a factory's return", "declare function make#(): @;\nconst Made# = make#();\nexport const made# = <Made# />;"],
  ["a state", "export function State#() { const [Body] = useState<@ | null>(null); return Body === null ? null : <Body />; }"],
  ["a map's value", 'declare const byName#: ReadonlyMap<string, @>;\nexport function Mapped#() { const Got = byName#.get("a"); return Got === undefined ? null : <Got />; }'],
  ["a type parameter's constraint", "export function Poly#<C extends @>({ as: Tag }: { readonly as: C }) { return <div><Tag /></div>; }"],
  ["a union, first", "declare const First#: @ | (() => null);\nexport const first# = <First# />;"],
  ["a union, last", "declare const Last#: (() => null) | @;\nexport const last# = <Last# />;"],
  ["a lazy module", "declare const mod#: { readonly default: @ };\nconst Lazy# = lazy(async () => mod#);\nexport const lazy# = <Lazy# />;"],
  ["own children rendered as a tag", "export function Pass#({ children: Inner }: { readonly children: @ }) { return <div><Inner /></div>; }"],
];

/** The declarations the component corpus reads. */
export const COMPONENT_HEADER = [
  'import { type ComponentType, createContext, type FC, Fragment, type FunctionComponent, type JSXElementConstructor, lazy, type ReactElement, StrictMode, Suspense, useState } from "react";',
  'import type { RouteObject } from "react-router";',
  "declare const raw: string;",
  "declare const list: readonly string[];",
  "declare const maybe: boolean;",
  'const LABEL = createContext("");',
].join("\n");

/** R90 at a tag and at a function given to a JSX position (MA-1 of round 3 of #119): every library
 * component type × every place a function reaches a tag, refused; app-typed tags of each
 * constructor (a union in either order, overloads in either order); functions at an attribute and
 * as a child (a context's consumer); React's exotic components (Fragment, StrictMode, Suspense, a context) and an element-returning function, controls. */
export function componentCases(): Case[] {
  const said = "as what this component returns";
  const library = REFUSED_LIBRARY_COMPONENT_TYPES.flatMap((type, index) =>
    TAG_PLACES.map(([place, code], at): Case => ({ id: `${type}, ${place}`, code: code.replaceAll("@", type).replaceAll("#", `${String(index)}_${String(at)}`), refused: ["raw-text"], said })),
  );
  return [
    ...library,
    { id: "a component typed by a library type alone (round 2's residual, closed)", code: "declare const Declared: ComponentType;\nexport const declaredShown = <Declared />;", refused: ["raw-text"], said },
    { id: "an app component type that carries its own $$typeof and _result (exotic only as a package declares it)", code: "declare const Fake: { (): string; readonly $$typeof: symbol; readonly _result: () => null };\nexport const fake = <Fake />;", refused: ["raw-text"], said },
    { id: "an app tag, a union with the string second", code: "declare const U10: (() => null) | (() => string);\nexport const z10 = <U10 />;", refused: ["raw-text"], said },
    { id: "an app tag, a union with the string first", code: "declare const U11: (() => string) | (() => null);\nexport const z11 = <U11 />;", refused: ["raw-text"], said },
    { id: "an app tag, overloads with the string second", code: "declare const O14: { (): null; (props: { readonly v?: 1 }): string };\nexport const z14 = <O14 />;", refused: ["raw-text"], said },
    { id: "an app tag, overloads with the string first", code: "declare const O15: { (): string; (props: { readonly v?: 1 }): null };\nexport const z15 = <O15 />;", refused: ["raw-text"], said },
    { id: "a function at an attribute (a component prop given an inline arrow)", code: "function Frame16({ Body }: { readonly Body: () => ReactElement | null }) { return <div><Body /></div>; }\nexport const z16 = <Frame16 Body={() => raw} />;", refused: ["raw-text"], said: "a function that returns string" },
    { id: "a block-bodied function at an attribute, a number", code: "function Frame17({ Count }: { readonly Count: () => ReactElement | null }) { return <div><Count /></div>; }\nexport const z17 = <Frame17 Count={() => { return list.length; }} />;", refused: ["raw-text"], said: "a function that returns number" },
    { id: "a context consumer's function child", code: "export const k1 = <LABEL.Consumer>{(given) => given}</LABEL.Consumer>;", refused: ["raw-text"], said: "a function that returns string" },
    // The controls.
    { id: "React's exotic components and a context's provider", code: 'export const exotic = <StrictMode><Fragment><Suspense fallback={null}><LABEL value="a"><p>x</p></LABEL></Suspense></Fragment></StrictMode>;', refused: [] },
    { id: "a consumer whose child returns an element, a handler that returns nothing", code: "export const fine = <LABEL.Consumer>{() => <p>x</p>}</LABEL.Consumer>;\nexport const handled = <button onClick={() => undefined}>x</button>;", refused: [] },
    { id: "a lazy component and a prop typed by what they return (an element)", code: "const Fine = lazy(async () => ({ default: () => <p>x</p> }));\nfunction Frame18({ Body }: { readonly Body: () => ReactElement }) { return <div><Body /></div>; }\nexport const fineLazy = <Suspense fallback={null}><Fine /><Frame18 Body={() => <p>x</p>} /></Suspense>;", refused: [] },
  ];
}

/** R91's references by the place of the identifier (MA-2 of round 3): a DOM function (`alert`) called,
 * as the object of `.call`, `.apply` and `.bind`, as an argument, a shorthand, an array's element, a
 * conditional's branch and a return; and a method of `lib.dom.iterable.d.ts`. */
export function domReferenceCases(): Case[] {
  const places: readonly (readonly [string, string])[] = [
    ["called", "alert(raw);"],
    ["the object of call", "alert.call(null, raw);"],
    ["the object of apply", "alert.apply(null, [raw]);"],
    ["the object of bind", "export const bound = alert.bind(null, raw);"],
    ["an argument", "declare function keep(f: unknown): void;\nkeep(alert);"],
    ["a shorthand", "export const shorthand2 = { alert };"],
    ["an array's element", "export const listed = [alert];"],
    ["a conditional's branch", "export const either = raw === \"\" ? alert : null;"],
    ["a return", "export function giveAlert() { return alert; }"],
  ];
  return [
    ...places.map(([place, code]): Case => ({ id: `alert, ${place}`, code, refused: ["dom"], said: "(R91): alert." })),
    { id: "a method of lib.dom.iterable.d.ts", code: "export const tokens = el.classList.entries();", refused: ["dom"], said: "DOMTokenList.entries" },
  ];
}

/** The receivers of a DOM value (R91), as a parameter list and its name: direct, a union, a
 * constraint, a base, `Pick`, `Partial`, a mapped type, `ReturnType`, an alias. */
export const DOM_RECEIVERS: readonly (readonly [string, string])[] = [
  ["direct", "(r: HTMLElement)"],
  ["a union", "(r: HTMLElement | SVGElement)"],
  ["a constraint", "<T extends HTMLElement>(r: T)"],
  ["a base", "(r: MineElement)"],
  ["Pick", '(r: Pick<HTMLElement, "title" | "append">)'],
  ["Partial", "(r: Partial<HTMLElement>)"],
  ["a mapped type", "(r: Writable<HTMLElement>)"],
  ["ReturnType", "(r: ReturnType<() => HTMLElement>)"],
  ["an alias", "(r: ElementAlias)"],
];

/** The declarations the receivers read. */
export const DOM_RECEIVER_HEADER = [
  "declare const raw: string;",
  "declare const data: Record<string, number>;",
  'declare const key: "title" | "append";',
  "interface MineElement extends HTMLElement { readonly mine: true }",
  "type Writable<T> = { -readonly [K in keyof T]: T[K] };",
  "type ElementAlias = HTMLElement;",
].join("\n");

/** R91's writes: every write form of a member (`@` the member, and `delete`) × every receiver. */
export function domWriteCases(): Case[] {
  const forms: readonly (readonly [string, string])[] = [
    ...NAME_WRITES,
    ["a delete", "delete @;"],
    ["a for-in target over data", "for (@ in data) { break; }"],
    ["a rest", "[...@] = raw;"],
  ];
  return DOM_RECEIVERS.flatMap(([receiver, parameters], at) =>
    forms.flatMap(([form, statement], index): Case[] => [
      { id: `${receiver}: ${form}`, code: `export function w${String(at)}_${String(index)}${parameters}: void { ${statement.replaceAll("@", "r.title")} }`, refused: ["dom"] },
      { id: `${receiver}: ${form}, a computed literal`, code: `export function x${String(at)}_${String(index)}${parameters}: void { ${statement.replaceAll("@", 'r["title"]')} }`, refused: ["dom"] },
    ]),
  );
}

/** R91's references: an unreviewed method (`append`) in every read form × every receiver. */
export function domReadCases(): Case[] {
  const forms: readonly (readonly [string, string])[] = [
    ["a call", "r.append?.(raw);"],
    ["a computed literal call", 'r["append"]?.(raw);'],
    ["a value", "return r.append;"],
    ["a destructuring", "const { append } = r; return append;"],
    ["a renamed destructuring", "const { append: add } = r; return add;"],
    ["a computed member named at run time", "return r[key];"],
  ];
  return DOM_RECEIVERS.flatMap(([receiver, parameters], at) =>
    forms.map(([form, statement], index): Case => ({ id: `${receiver}: ${form} of append`, code: `export function r${String(at)}_${String(index)}${parameters}: unknown { ${statement} return null; }`, refused: ["dom"] })),
  );
}

/** R91's allow-list, each key as a control (refused nothing), from `CALLS`. */
export function callControls(keys: readonly string[]): Case[] {
  return keys.map((key, index): Case => {
    const dot = key.indexOf(".");
    const owner = dot < 0 ? key : key.slice(0, dot);
    const code =
      dot < 0
        ? `export const g${String(index)} = new ${key}();`
        : `declare const o${String(index)}: ${owner};\nexport const g${String(index)} = o${String(index)}.${key.slice(dot + 1)};`;
    return { id: `CALLS: ${key}`, code, refused: [] };
  });
}

/** R92: each key of `ADDRESS` as a member, a computed literal, a destructured key, and on a union. */
export function addressCases(keys: readonly string[]): Case[] {
  return keys.flatMap((key, index): Case[] => {
    const dot = key.indexOf(".");
    const owner = key.slice(0, dot);
    const member = key.slice(dot + 1);
    const at = String(index);
    return [
      { id: `${key}: a member`, code: `declare const addr${at}: ${owner};\nexport const addrMember${at} = addr${at}.${member};`, refused: ["address"] },
      { id: `${key}: a computed literal`, code: `export const addrComputed${at} = addr${at}["${member}"];`, refused: ["address"] },
      { id: `${key}: a destructured key`, code: `export const { ${member}: addrDestructured${at} } = addr${at};`, refused: ["address"] },
      { id: `${key}: on a union`, code: `declare const addrUnion${at}: ${owner} | { readonly other${at}: true };\nexport const addrOnUnion${at} = addrUnion${at}["${member}"];`, refused: ["address"] },
    ];
  });
}

/** The elements and attributes, from the tables: each listed element and attribute as a control, an
 * element of React's not listed, and each literal attribute with a string, a brand and a union. */
export function elementCases(elements: readonly string[], refusedElements: readonly string[], attributes: readonly string[], literal: readonly string[]): Case[] {
  return [
    ...elements.filter((name) => name !== "option").map((name, index): Case => ({ id: `<${name}>`, code: `export const l${String(index)} = <${name} />;`, refused: [] })),
    ...refusedElements.map((name, index): Case => ({ id: `<${name}> (not listed)`, code: `export const n${String(index)} = <${name} />;`, refused: ["elements"] })),
    ...attributes
      .filter((name) => name !== "ref" && name !== "key")
      .map((name, index): Case => ({ id: `${name}= (listed)`, code: `export const a${String(index)} = <p ${name}=${/^on[A-Z]/u.test(name) ? "{() => undefined}" : name === "disabled" || name === "spellCheck" ? "{true}" : '"x"'} />;`, refused: [] })),
    ...literal.flatMap((name, index): Case[] => [
      { id: `${name}: a string`, code: `export const s${String(index)} = <p ${name}={words} />;`, refused: ["elements"] },
      { id: `${name}: a brand`, code: `export const b${String(index)} = <p ${name}={typed} />;`, refused: ["elements"] },
      { id: `${name}: a literal union`, code: `export const u${String(index)} = <p ${name}={raw === "" ? "a" : "b"} />;`, refused: [] },
    ]),
  ];
}

/** A corpus: the file it is linted as, its header and its cases. */
export interface Corpus {
  readonly name: string;
  readonly file: string;
  readonly header: string;
  readonly cases: readonly Case[];
}

const CATALOGUE_PROBE = "src/catalogue/probe-display.tsx";

/** Every corpus of the rows' tests, generated ones included (the tables passed in are the rule's). */
export function corpora(tables: {
  readonly elements: readonly string[];
  readonly attributes: readonly string[];
  readonly literal: readonly string[];
  readonly calls: readonly string[];
  readonly address: readonly string[];
}): Corpus[] {
  return [
    { name: "raw", file: CATALOGUE_PROBE, header: RAW_HEADER, cases: [...RAW_CASES, ...RAW_RESIDUALS, ...childrenWriteCases(), ...compositeCases()] },
    { name: "raw, components", file: CATALOGUE_PROBE, header: COMPONENT_HEADER, cases: componentCases() },
    { name: "raw, open", file: CATALOGUE_PROBE, header: RAW_HEADER, cases: RAW_OPEN },
    { name: "open", file: CATALOGUE_PROBE, header: OPEN_HEADER, cases: OPEN_CASES },
    {
      name: "elements",
      file: CATALOGUE_PROBE,
      header: ELEMENT_HEADER,
      cases: [...ELEMENT_CASES, ...elementCases(tables.elements, ["iframe", "img", "script", "style", "meta", "base", "title", "link", "object", "embed", "svg", "textarea", "table", "ul"], tables.attributes, tables.literal)],
    },
    { name: "specifiers", file: CATALOGUE_PROBE, header: "", cases: SPECIFIER_CASES },
    { name: "dom", file: CATALOGUE_PROBE, header: DOM_HEADER, cases: [...DOM_CASES, ...DOM_RESIDUALS, ...domReferenceCases(), ...callControls(tables.calls)] },
    { name: "dom, generated", file: CATALOGUE_PROBE, header: DOM_RECEIVER_HEADER, cases: [...domWriteCases(), ...domReadCases()] },
    { name: "address", file: CATALOGUE_PROBE, header: ADDRESS_HEADER, cases: [...ADDRESS_CASES, ...ADDRESS_ENTRY_CASES, ...addressCases(tables.address)] },
    { name: "answers", file: "src/operator/probe-display.tsx", header: ANSWERS_HEADER, cases: ANSWERS_CASES },
  ];
}
