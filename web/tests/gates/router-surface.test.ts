/**
 * The router's surface (D428), the stopgap kept as defence in depth beside 2b-1L's rules (D429: R90
 * at JSX positions, R91, R92; R90's flow class OPEN until 2b-1L2), in two layers.
 *
 * **First, a walk at run time of the routes the app gives the router** (`catalogueRoutes()`, and each
 * `lazy` result, in both of `lazy`'s forms): every route's own keys must be in an ALLOW-LIST (`path`,
 * `index`, `element`, `lazy`, which must be a function, `children`, `errorElement`: no `Component`,
 * D431, so that no component is handed to the router as a value), and every `element` and
 * `errorElement` must be an element (`isValidElement`) of one of the app's route screens
 * (`ELEMENT_TYPES`: written in place, never a string, a number or another component), so
 * that a `loader`, an `action`, a `middleware`, a `shouldRevalidate` or a `handle` is refused whatever
 * its typing (a route's `loader({ params, request })` and `middleware` receive the decoded address;
 * round 3 of #117 rendered a raw U+202E through an untyped literal's `loader` and a `middleware`, past
 * a scan of names). It sees the routes the app builds, not a route made elsewhere and never handed to
 * `catalogueRoutes`.
 *
 * **Second, a scan of the program's RESOLVED symbols:** only `src/router/address.tsx` and the two
 * entries' mains (`src/catalogue/main.tsx`, `src/operator/main.tsx`) may reference the router object,
 * its `.state` and `.matches`, a route's `loader` and `RouterProvider`'s `onError`. The allow-list of
 * the router package's imports (`router.test.ts`) refuses the names a file imports; it cannot see what
 * the allowed names hand back: the router `createHashRouter` returns holds the decoded address in
 * `state.matches[].params`, and `onError`'s `info` carries it. The scan sees a property or a type
 * DECLARED IN THE ROUTER PACKAGE, whatever its spelling (`router.state`, `router["state"]`,
 * `const { state } = router`, a `loader` in a literal, even a method `loader() {}`, or a literal a
 * `lazy` returns, that the compiler types as a route's, an `onError` attribute), so a `state` of a
 * component's own is no hit; a
 * value the compiler does not type as the router's (an untyped route literal, a `middleware` or a
 * `shouldRevalidate` in one, a router a main passes to a structurally typed parameter) is not
 * seen by it: the walk holds the first, and the rest, with the address's other readers (an element's
 * `baseURI`, the navigation entry's `name`, a `hashchange` listener's `oldURL`), was OPEN until 2b-1L
 * (D428) and is closed by D429's rules where its text lands at a JSX position, a DOM write or call or
 * an address member (`tests/gates/display-lint.ts`'s `OPEN_CASES`, a probe each), and OPEN where it
 * reaches a render slot other than at a JSX position, until 2b-1L2.
 */
import { readFileSync } from "node:fs";
import path from "node:path";

import { createElement, isValidElement } from "react";
import ts from "typescript";
import { describe, expect, it } from "vitest";

import { Catalogue } from "../../src/catalogue/Catalogue";
import { Failure } from "../../src/catalogue/Failure";
import { NotFound } from "../../src/catalogue/NotFound";
import { catalogueRoutes } from "../../src/catalogue/routes";
import { ColumnAddress, DatasetAddress } from "../../src/router/address";
import { check, type Checked, WEB } from "./program";
import { sources } from "./router-scan";

/** The keys of a route the app may give the router (D428, D431); `lazy` only as a function. */
const ROUTE_KEYS: ReadonlySet<PropertyKey> = new Set(["path", "index", "element", "lazy", "children", "errorElement"]);

/** The types an `element` or `errorElement` may be an element of (D431): the app's route screens. */
const ELEMENT_TYPES: ReadonlySet<unknown> = new Set([Catalogue, DatasetAddress, ColumnAddress, NotFound, Failure]);

/** What is wrong with a route's `element` or `errorElement`: none if absent or an element of a type
 * of `ELEMENT_TYPES`. */
function refusedElement(value: unknown, where: string): string[] {
  if (value === undefined) {
    return [];
  }
  if (!isValidElement(value)) {
    return [`${where}: not an element`];
  }
  return ELEMENT_TYPES.has(value.type) ? [] : [`${where}: an element of another type`];
}

type Walked = Readonly<Record<PropertyKey, unknown>>;

const isWalked = (value: unknown): value is Walked => typeof value === "object" && value !== null;

/** Each key of a list of routes, of the `children` and of the `lazy` results (called, awaited) below
 * it, that is not in `ROUTE_KEYS`, as `where: key`; a `lazy` that is no function, as `where: lazy is
 * no function`. `Reflect.ownKeys`: a key that is not enumerable or is a symbol counts. */
export async function refusedKeys(routes: unknown, where = "routes"): Promise<string[]> {
  if (!Array.isArray(routes)) {
    return [`${where}: not a list`];
  }
  const found: string[] = [];
  for (const [at, route] of routes.entries()) {
    found.push(...(await refusedRoute(route, `${where}[${String(at)}]`)));
  }
  return found;
}

async function refusedRoute(route: unknown, where: string): Promise<string[]> {
  if (!isWalked(route)) {
    return [`${where}: not an object`];
  }
  const found = Reflect.ownKeys(route)
    .filter((key) => !ROUTE_KEYS.has(key))
    .map((key) => `${where}: ${String(key)}`);
  const { lazy, children, element, errorElement } = route;
  found.push(...refusedElement(element, `${where}.element`), ...refusedElement(errorElement, `${where}.errorElement`));
  if (typeof lazy === "function") {
    const loaded: unknown = await Reflect.apply(lazy, undefined, []);
    found.push(...(await refusedRoute(loaded, `${where}.lazy()`)));
  } else if (lazy !== undefined) {
    found.push(`${where}: lazy is no function`);
  }
  if (children !== undefined) {
    found.push(...(await refusedKeys(children, `${where}.children`)));
  }
  return found;
}

/** The files that may reference the router object and what it hands back. */
const ALLOWED = new Set(["src/router/address.tsx", "src/catalogue/main.tsx", "src/operator/main.tsx"]);

/** The names a router's value, a route and the provider hand the address through. */
const NAMES = new Set(["state", "matches", "loader", "onError"]);

/** The types of the router object (`createHashRouter` gives a `Router`; the provider takes a `DataRouter`). */
const ROUTER_TYPES = new Set(["Router", "DataRouter"]);

/** Whether a symbol is declared in the router package. */
const ofRouter = (symbol: ts.Symbol | undefined): boolean => symbol?.declarations?.some((declaration) => /[\\/]node_modules[\\/]react-router[\\/]/u.test(declaration.getSourceFile().fileName)) ?? false;

/** The property of a type by name, in a union's members too. */
function property(type: ts.Type | undefined, name: string): ts.Symbol | undefined {
  return type === undefined ? undefined : (type.getProperty(name) ?? (type.isUnion() ? type.types.map((member) => member.getProperty(name)).find((found) => found !== undefined) : undefined));
}

/** What a file references of the router's surface, as the lines `kind: name`. */
export function surface(checked: Checked, file: string): string[] {
  const { checker } = checked;
  const found: string[] = [];
  const named = (name: string, symbol: ts.Symbol | undefined, kind: string): void => {
    if (NAMES.has(name) && ofRouter(symbol)) {
      found.push(`${kind}: ${name}`);
    }
  };
  const routerValue = (node: ts.Node): void => {
    const type = checker.getTypeAtLocation(node);
    const symbols: readonly (ts.Symbol | undefined)[] = [type.aliasSymbol, type.symbol];
    if (symbols.some((symbol) => symbol !== undefined && ROUTER_TYPES.has(symbol.name) && ofRouter(symbol))) {
      found.push(`the router object: ${node.getText(checked.source(file))}`);
    }
  };
  const visit = (node: ts.Node): void => {
    if (ts.isPropertyAccessExpression(node)) {
      named(node.name.text, checker.getSymbolAtLocation(node.name), "property");
    } else if (ts.isElementAccessExpression(node) && ts.isStringLiteralLike(node.argumentExpression)) {
      named(node.argumentExpression.text, property(checker.getTypeAtLocation(node.expression), node.argumentExpression.text), "property");
    } else if ((ts.isPropertyAssignment(node) || ts.isShorthandPropertyAssignment(node) || ts.isMethodDeclaration(node)) && ts.isObjectLiteralExpression(node.parent) && (ts.isIdentifier(node.name) || ts.isStringLiteralLike(node.name))) {
      named(node.name.text, property(checker.getContextualType(node.parent), node.name.text), "route or option");
    } else if (ts.isBindingElement(node) && ts.isObjectBindingPattern(node.parent)) {
      const key = node.propertyName ?? node.name;
      if (ts.isIdentifier(key) || ts.isStringLiteralLike(key)) {
        named(key.text, property(checker.getTypeAtLocation(node.parent), key.text), "destructured");
      }
    } else if (ts.isJsxAttribute(node) && ts.isIdentifier(node.name)) {
      named(node.name.text, property(checker.getContextualType(node.parent), node.name.text), "attribute");
    } else if (ts.isIdentifier(node) || ts.isCallExpression(node)) {
      routerValue(node);
    }
    ts.forEachChild(node, visit);
  };
  visit(checked.source(file));
  return found;
}

const REAL = sources();

/** One program: every file of `src/` as it is, and the probes. */
function program(probes: Readonly<Record<string, string>>): Checked {
  return check({ ...Object.fromEntries(REAL.map((file) => [file, readFileSync(path.join(WEB, file), "utf8")])), ...probes });
}

describe("only the router module and the entries' mains reference the router object, state, matches, loader and onError", () => {
  const checked = program({});

  it("no other file of src/ does, and the scan sees what the entry does (a control)", () => {
    const found = REAL.filter((file) => !ALLOWED.has(file)).flatMap((file) => surface(checked, file).map((line) => `${file}: ${line}`));
    expect(found).toEqual([]);
    expect(surface(checked, "src/catalogue/main.tsx").length).toBeGreaterThan(0);
  });
});

const HEAD = 'import { createHashRouter, type RouteObject } from "react-router";\nimport { RouterProvider } from "react-router/dom";\ndeclare const routes: RouteObject[];\nconst router = createHashRouter(routes);\n';

describe("the scan's own fixtures (each a probe of round 2 of #117, rendered through the real router there)", () => {
  const PROBES: readonly (readonly [string, string, readonly string[]])[] = [
    ["state.matches[].params", `${HEAD}export const id = router.state.matches.at(-1)?.params["id"];\n`, ["property: state", "property: matches"]],
    ["an element access", `${HEAD}export const s = router["state"];\n`, ["property: state"]],
    ["a destructuring", `${HEAD}export const { state } = router;\n`, ["destructured: state"]],
    ["a loader in a route literal", 'import type { RouteObject } from "react-router";\nexport const routes: RouteObject[] = [{ path: "/", loader: ({ params }) => params["id"] }];\n', ["route or option: loader"]],
    ["a shorthand loader", 'import type { RouteObject } from "react-router";\nconst loader = () => null;\nexport const routes: RouteObject[] = [{ path: "/", loader }];\n', ["route or option: loader"]],
    ["a loader in method syntax", 'import type { RouteObject } from "react-router";\nexport const routes: RouteObject[] = [{ path: "/", loader({ params }) { return params["id"] ?? null; } }];\n', ["route or option: loader"]],
    ["a loader in the literal a lazy function returns", 'import type { RouteObject } from "react-router";\nexport const routes: RouteObject[] = [{ path: "/", lazy: async () => ({ loader: () => null }) }];\n', ["route or option: loader"]],
    ["a loader in the literal a lazy function returns, from a block", 'import type { RouteObject } from "react-router";\nexport const routes: RouteObject[] = [{ path: "/", lazy: async () => { return { loader: () => null }; } }];\n', ["route or option: loader"]],
    ["an element access on a union of the router and undefined", `${HEAD}declare const maybe: typeof router | undefined;\nexport const s = maybe?.["state"];\n`, ["property: state"]],
    ["an onError attribute", `${HEAD}export const view = <RouterProvider router={router} onError={(error, info) => [error, info]} />;\n`, ["attribute: onError"]],
    ["the router object alone", `${HEAD}export const again = router;\n`, ["the router object: router"]],
    ["the object a call gives", 'import { createHashRouter } from "react-router";\nexport const made = createHashRouter([]);\n', ["the router object: createHashRouter([])"]],
  ];

  const checked = program(Object.fromEntries(PROBES.map(([, text], index) => [`src/catalogue/zz-surface-${String(index)}.tsx`, text])));

  it.each(PROBES.map(([name, , expected], index) => [name, index, expected] as const))("sees %s", (_name, index, expected) => {
    const found = surface(checked, `src/catalogue/zz-surface-${String(index)}.tsx`);
    for (const line of expected) {
      expect(found).toContain(line);
    }
  });

  it("sees nothing in a state, matches, loader and onError of its own (the controls)", () => {
    const own = program({
      "src/catalogue/zz-surface-own.tsx": [
        'import { useState } from "react";',
        "const own = { state: 1, matches: 2, loader: 3, onError: 4 };",
        "const { state, matches } = own;",
        "export function Own({ onError }: { readonly onError: () => void }) {",
        "  const [loader] = useState(0);",
        "  return <button onClick={onError}>{state + matches + own.loader + loader + own.onError}</button>;",
        "}",
        "",
      ].join("\n"),
    });
    expect(surface(own, "src/catalogue/zz-surface-own.tsx")).toEqual([]);
  });
});

describe("the routes the app gives the router hold the allow-list's keys alone (the walk)", () => {
  it("catalogueRoutes() does, with its lazy results", async () => {
    expect(await refusedKeys(catalogueRoutes())).toEqual([]);
  });

  /** The probes of round 3 of #117: rendered through the real hash router there, each put a raw U+202E in the DOM. */
  const NOT: readonly (readonly [string, unknown, readonly string[]])[] = [
    ["an untyped literal's loader", [{ path: "leak/:id", loader: ({ params }: { readonly params: Record<string, string | undefined> }) => params["id"] ?? "", element: createElement(NotFound) }], ["routes[0]: loader"]],
    [
      "a loader in method syntax",
      [
        {
          path: "m/:id",
          loader() {
            return null;
          },
        },
      ],
      ["routes[0]: loader"],
    ],
    ["a middleware", [{ path: "mw/:id", middleware: [async (_context: unknown, next: () => Promise<unknown>) => next()], element: createElement(NotFound) }], ["routes[0]: middleware"]],
    ["an action, a shouldRevalidate and a handle", [{ action: () => null, shouldRevalidate: () => true, handle: {} }], ["routes[0]: action", "routes[0]: shouldRevalidate", "routes[0]: handle"]],
    ["a loader that is undefined", [{ loader: undefined }], ["routes[0]: loader"]],
    ["a loader a lazy function returns", [{ path: "l/:id", lazy: () => Promise.resolve({ loader: () => null }) }], ["routes[0].lazy(): loader"]],
    ["a middleware a lazy function returns, synchronously", [{ lazy: () => ({ middleware: [] }) }], ["routes[0].lazy(): middleware"]],
    ["the lazy object form", [{ path: "z", lazy: { middleware: () => Promise.resolve([]) } }], ["routes[0]: lazy is no function"]],
    ["a loader in a child, and in a child's lazy result", [{ children: [{ index: true }, { path: "c", loader: () => null, lazy: () => Promise.resolve({ action: () => null }) }] }], ["routes[0].children[1]: loader", "routes[0].children[1].lazy(): action"]],
    ["a symbol key", [{ [Symbol("loader")]: () => null }], ["routes[0]: Symbol(loader)"]],
    ["a key that is not enumerable", [Object.defineProperty({ path: "n" }, "loader", { value: () => null, enumerable: false })], ["routes[0]: loader"]],
    ["a route that is no object", [() => null], ["routes[0]: not an object"]],
    ["routes that are no list", { path: "x" }, ["routes: not a list"]],
    ["a Component (D431: a component handed as a value)", [{ path: "a", Component: () => null }], ["routes[0]: Component"]],
    ["a Component a lazy function returns", [{ path: "b", lazy: () => Promise.resolve({ Component: NotFound }) }], ["routes[0].lazy(): Component"]],
    ["an element that is a string", [{ index: true, element: "\u202e raw" }], ["routes[0].element: not an element"]],
    ["an element that is a number", [{ index: true, element: 3 }], ["routes[0].element: not an element"]],
    ["an element that is null", [{ index: true, element: null }], ["routes[0].element: not an element"]],
    ["an element of another type", [{ index: true, element: createElement("p", null, "raw") }], ["routes[0].element: an element of another type"]],
    ["an errorElement of another component", [{ errorElement: createElement(() => null), children: [] }], ["routes[0].errorElement: an element of another type"]],
    ["a lazy element of another type", [{ path: "c", lazy: () => Promise.resolve({ element: createElement("span") }) }], ["routes[0].lazy().element: an element of another type"]],
  ];

  it.each(NOT)("refuses %s", async (_name, routes, expected) => {
    expect(await refusedKeys(routes)).toEqual(expected);
  });

  it("accepts every key of the allow-list, and an element of each route screen, in place or from a lazy function (the control)", async () => {
    const routes = [
      {
        errorElement: createElement(Failure),
        children: [
          { index: true, element: createElement(Catalogue) },
          { path: "a", lazy: () => Promise.resolve({ element: createElement(DatasetAddress, { screen: () => createElement(NotFound) }) }) },
          { path: "b", lazy: () => Promise.resolve({ element: createElement(NotFound) }) },
        ],
      },
    ];
    expect(await refusedKeys(routes)).toEqual([]);
  });
});
