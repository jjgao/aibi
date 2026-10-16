/**
 * The router module (D428): `src/router/address.tsx` is the one file of `src/` that reads the
 * address through the router's `useParams` and `location.pathname` (the readers beyond what these
 * tests name are OPEN until 2b-1L's rule over the sink: `router-surface.test.ts` and D428), and what
 * the app may name of the router package is an ALLOW-LIST, not a list of the
 * readers someone thought of (round 1 of #117: `useMatches`, `useMatch`, `useResolvedPath`,
 * `useHref` and `UNSAFE_LocationContext` put the raw address in the DOM past a gate that named six
 * readers).
 *
 * - **Imports.** Outside the router module, the only names any file of `src/` may import from the
 *   router package are `createHashRouter` and `RouteObject` (from `react-router`) and
 *   `RouterProvider` (from `react-router/dom`); the module's own list is `useParams`. Everything
 *   else is refused: another name, a default or namespace import, a side-effect import, an
 *   export-all, a dynamic import, an import type, another specifier of the package. The lint
 *   (`no-restricted-imports` with `allowImportNames`, and `no-restricted-syntax`) refuses them, and
 *   this test holds the same by a scan of every import, whatever its spelling.
 * - **The class.** The test ENUMERATES the installed package's exports (`react-router`,
 *   `react-router/dom`, and `react-router-dom` if present: runtime keys and the type declarations'
 *   exports) and requires each but the listed to be refused, by the scan and by the lint, so a
 *   release that adds a reader fails here.
 * - **The page's own address.** A member of `location`, `.location` of the window or the document,
 *   `document.URL`, `referrer`, `baseURI` and `documentURI` are refused in every file but the
 *   router module (lint rows, each cell here). A corpus of readers, each through the REAL hash
 *   router at an address with a right-to-left override: each reader puts the address in the DOM raw,
 *   and each is refused.
 *
 * The router module is the one file of the catalogue that writes the address (D428): its one call of
 * `history.replaceState`, in `redirectAddress`, writes `/` or `/#/datasets/` and a `DatasetId`
 * (the lint's `aibi/page-objects` option, checked by type), nothing else and nowhere else.
 */
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";

import ts from "typescript";
import { describe, expect, it } from "vitest";

import { Linter } from "./linter";
import { check, WEB } from "./program";
import { READERS } from "./router-readers";
import { ALLOWED, MODULE, OWN, refused, sources } from "./router-scan";

describe("the app names the router package's allow-list alone", () => {
  it("no file of src/ imports another name of the package, and the router module imports useParams alone", () => {
    const found = sources().flatMap((file) => refused(file, readFileSync(path.join(WEB, file), "utf8"), file === MODULE ? OWN : ALLOWED).map((line) => `${file}: ${line}`));
    expect(found).toEqual([]);
    expect(refused(MODULE, readFileSync(path.join(WEB, MODULE), "utf8"), {})).toEqual(["react-router: useParams"]);
    expect(sources().flatMap((file) => (file === MODULE ? [] : refused(file, readFileSync(path.join(WEB, file), "utf8"), {}).map((line) => `${file}: ${line}`)))).toEqual([
      "src/catalogue/main.tsx: react-router: createHashRouter",
      "src/catalogue/main.tsx: react-router/dom: RouterProvider",
      "src/catalogue/routes.tsx: react-router: RouteObject",
    ]);
  });

  it.each([
    ["an import", 'import { useMatches } from "react-router";', ["react-router: useMatches"]],
    ["a renamed import", 'import { useLocation as where } from "react-router";', ["react-router: useLocation"]],
    ["a type import", 'import type { Location } from "react-router";', ["react-router: Location"]],
    ["an inline type import", 'import { type Location } from "react-router";', ["react-router: Location"]],
    ["a namespace", 'import * as router from "react-router";\nexport const f = router["useRouteError"];', ["react-router: *"]],
    ["a default import", 'import router from "react-router";', ["react-router: default"]],
    ["a side-effect import", 'import "react-router";', ["react-router: (side effect)"]],
    ["a re-export", 'export { useMatch } from "react-router";', ["react-router: useMatch"]],
    ["an export all", 'export * from "react-router";', ["react-router: *"]],
    ["a dynamic import", 'export const f = () => import("react-router");', ["react-router: import()"]],
    ["a computed dynamic import", "export const f = (n: string) => import(n);", ["<computed>: import()"]],
    ["an import type", 'export type F = typeof import("react-router").useParams;', ["react-router: import()"]],
    ["an import-equals", 'import router = require("react-router");', ["react-router: require"]],
    ["another path of the package", 'import { RouterProvider } from "react-router/other";', ["react-router/other: RouterProvider"]],
    ["the other package", 'import { Link } from "react-router-dom";', ["react-router-dom: Link"]],
    ["a scoped package", 'import { x } from "@react-router/dev";', ["@react-router/dev: x"]],
    ["a name of the other path", 'import { createHashRouter } from "react-router/dom";', ["react-router/dom: createHashRouter"]],
    ["a name of the allowed list, in the module's own file", 'import { createHashRouter } from "react-router";', "own"],
  ] as const)("refuses %s (the scan's own controls)", (_case, text, expected) => {
    if (expected === "own") {
      expect(refused("src/router/address.tsx", text, OWN)).toEqual(["react-router: createHashRouter"]);
    } else {
      expect(refused("src/probe.tsx", text, ALLOWED)).toEqual(expected);
    }
  });

  it("allows each name of the list, and the module's own reader, and nothing of another package", () => {
    expect(refused("src/probe.tsx", 'import { createHashRouter, type RouteObject } from "react-router";\nimport { RouterProvider } from "react-router/dom";\nimport { useState } from "react";', ALLOWED)).toEqual([]);
    expect(refused("src/router/address.tsx", 'import { useParams } from "react-router";', OWN)).toEqual([]);
  });
});

/** The names of a module's exports by the type declarations: values and types. */
function declared(specifiers: readonly string[]): ReadonlyMap<string, string[]> {
  const probe = specifiers.map((specifier, index) => `import * as m${String(index)} from "${specifier}";\nexport { m${String(index)} };`).join("\n");
  const checked = check({ "src/probe-exports.ts": probe });
  const source = checked.source("src/probe-exports.ts");
  const found = new Map<string, string[]>();
  for (const statement of source.statements) {
    if (ts.isImportDeclaration(statement) && ts.isStringLiteralLike(statement.moduleSpecifier)) {
      const symbol = checked.checker.getSymbolAtLocation(statement.moduleSpecifier);
      if (symbol !== undefined) {
        found.set(statement.moduleSpecifier.text, checked.checker.getExportsOfModule(symbol).map((member) => member.name));
      }
    }
  }
  return found;
}

/** The router packages installed: `react-router`, `react-router/dom`, and `react-router-dom` if it
 * is. */
function installed(): string[] {
  const require = createRequire(path.join(WEB, "package.json"));
  const found = ["react-router", "react-router/dom"];
  try {
    require.resolve("react-router-dom");
    found.push("react-router-dom");
  } catch {
    // Not installed: nothing to enumerate.
  }
  return found;
}

/** The runtime exports of an installed module (its namespace's keys). The two packages the app
 * depends on are imported by literal specifiers (D427's gate refuses a computed `import()` in any
 * test file); `react-router-dom`, which only a stray install adds, is loaded by `require`. */
async function runtimeExports(specifier: string): Promise<string[]> {
  const loaded: unknown =
    specifier === "react-router" ? await import("react-router") : specifier === "react-router/dom" ? await import("react-router/dom") : createRequire(path.join(WEB, "package.json"))(specifier);
  return typeof loaded === "object" && loaded !== null ? Object.keys(loaded) : [];
}

describe("every export of the installed router package but the allow-list is refused (the class, not the six)", () => {
  const specifiers = installed();
  const types = declared(specifiers);

  it("enumerates the packages, with a corpus of the names round 1 found among them", async () => {
    const values = await import("react-router");
    for (const name of ["useMatches", "useMatch", "useResolvedPath", "useHref", "UNSAFE_LocationContext", "useNavigation", "useBlocker", "useFormAction", "matchPath", "matchRoutes", "useParams", "useLocation", "useSearchParams", "useRouteError", "useAsyncError", "isRouteErrorResponse"]) {
      expect(Object.keys(values), name).toContain(name);
    }
    expect(Object.keys(values).length).toBeGreaterThan(100);
    expect(types.get("react-router")).toEqual(expect.arrayContaining(["RouteObject", "Location", "createHashRouter"]));
  });

  it("lists the allow-list's names among the exports, so that the list names something that exists", async () => {
    const dom = await import("react-router/dom");
    const values = await import("react-router");
    expect(Object.keys(values)).toContain("createHashRouter");
    expect(Object.keys(dom)).toContain("RouterProvider");
    expect(types.get("react-router")).toContain("RouteObject");
  });

  it("refuses each export, by the scan, but the listed", async () => {
    const wrong: string[] = [];
    let counted = 0;
    for (const specifier of specifiers) {
      const values = await runtimeExports(specifier);
      for (const name of new Set([...values, ...(types.get(specifier) ?? [])])) {
        counted += 1;
        const refusedHere = refused("src/probe.tsx", `import { ${name} as probe } from "${specifier}";`, ALLOWED).length > 0;
        if (refusedHere === (ALLOWED[specifier] ?? []).includes(name)) {
          wrong.push(`${specifier}: ${name}`);
        }
      }
    }
    expect(wrong).toEqual([]);
    expect(counted).toBeGreaterThan(150);
  });

  it("refuses each export, by the lint, but the listed (one import statement of every name, each reported)", async () => {
    for (const specifier of specifiers) {
      const values = await runtimeExports(specifier);
      const names = [...new Set([...values, ...(types.get(specifier) ?? [])])].filter((name) => !(ALLOWED[specifier] ?? []).includes(name));
      const text = `import { ${names.map((name, index) => `${name} as probe${String(index)}`).join(", ")} } from "${specifier}";\nexport const used = [${names.map((_name, index) => `probe${String(index)}`).join(", ")}];\n`;
      const [result] = await eslint.lintText(text, { filePath: path.join(WEB, "src/catalogue/probe.tsx") });
      const reported = (result?.messages ?? []).filter((message) => message.ruleId === "@typescript-eslint/no-restricted-imports");
      expect(reported.map((message) => message.message).filter((message) => /restricted/u.test(message)).length, specifier).toBe(names.length);
      for (const name of ALLOWED[specifier] ?? []) {
        const [allowed] = await eslint.lintText(`import { ${name} as probe } from "${specifier}";\nexport const used = probe;\n`, { filePath: path.join(WEB, "src/catalogue/probe.tsx") });
        expect((allowed?.messages ?? []).filter((message) => message.ruleId === "@typescript-eslint/no-restricted-imports"), `${specifier}: ${name}`).toEqual([]);
      }
    }
  }, 120_000);
});

const PARSER = {
  languageOptions: {
    parserOptions: {
      projectService: {
        allowDefaultProject: ["src/catalogue/probe.tsx", "src/layout/probe.tsx", "src/api/probe.ts", "src/operator/probe.tsx", "src/harness/probe.tsx", "src/*.tsx"],
        defaultProject: "tsconfig.app.json",
        maximumDefaultProjectFileMatchCount_THIS_WILL_SLOW_DOWN_LINTING: 16,
      },
    },
  },
};

const eslint = new Linter({ cwd: WEB, overrideConfig: PARSER });
const POLICY = new Set(["no-restricted-syntax", "no-restricted-globals", "no-restricted-properties", "@typescript-eslint/no-restricted-imports", "aibi/page-objects"]);

async function ids(code: string, file: string): Promise<string[]> {
  const [result] = await eslint.lintText(code, { filePath: path.join(WEB, file) });
  const messages = result?.messages ?? [];
  expect(messages.filter((message) => message.fatal === true).map((message) => message.message)).toEqual([]);
  return messages.flatMap((message) => (message.ruleId !== null && POLICY.has(message.ruleId) ? [message.ruleId] : []));
}


describe("the lint holds the router package by its name wherever the specifier spells it (m4 of round 2 of #117)", () => {
  it.each([
    ["a path into node_modules", 'import { useMatches } from "../../node_modules/react-router/dist/development/index.js";\nexport const f = useMatches;\n'],
    ["a path into node_modules, the DOM entry", 'import { RouterProvider } from "../../node_modules/react-router/dist/development/dom-export.js";\nexport const f = RouterProvider;\n'],
    ["a path into node_modules by the allowed name", 'import { createHashRouter } from "../../node_modules/react-router/dist/development/index.js";\nexport const f = createHashRouter;\n'],
    ["a re-export of such a path", 'export { useMatches } from "../../node_modules/react-router/dist/development/index.js";\n'],
    ["the other package", 'import { Link } from "react-router-dom";\nexport const f = Link;\n'],
    ["a scoped package", 'import { x } from "@react-router/dev";\nexport const f = x;\n'],
    ["another case", 'import { useMatches } from "React-Router";\nexport const f = useMatches;\n'],
  ])("refuses %s", async (_case, code) => {
    expect((await ids(code, "src/catalogue/probe.tsx")).length).toBeGreaterThan(0);
  });

  it("accepts the allowed names of the package by its own specifiers (a control)", async () => {
    expect(await ids('import { createHashRouter, type RouteObject } from "react-router";\nimport { RouterProvider } from "react-router/dom";\nexport const f = [createHashRouter, RouterProvider];\nexport type R = RouteObject;\n', "src/catalogue/probe.tsx")).toEqual([]);
  });
});

describe("the page's own address is the router module's alone (lint rows)", () => {
  const READS: readonly (readonly [string, string])[] = [
    ["location.pathname", "export const f = () => location.pathname;"],
    ["location.search", "export const f = () => location.search;"],
    ["location.hash", "export const f = () => location.hash;"],
    ["location.href", "export const f = () => location.href;"],
    ["location.toString()", "export const f = () => location.toString();"],
    ["location?.pathname", "export const f = () => location?.pathname;"],
    ["location['pathname']", "export const f = () => location['pathname'];"],
    ["window.location.pathname", "export const f = () => window.location.pathname;"],
    ["globalThis.location.search", "export const f = () => globalThis.location.search;"],
    ["self.location.href", "export const f = () => self.location.href;"],
    ["top.location.pathname", "export const f = () => top?.location.pathname;"],
    ["window.location, as a value", "export const f = () => window.location;"],
    ["window.location.origin (any member of the window's location)", "export const f = () => window.location.origin;"],
    ["globalThis.location.protocol", "export const f = () => globalThis.location.protocol;"],
    ["document.location.host", "export const f = () => document.location.host;"],
    ["document.location.pathname", "export const f = () => document.location.pathname;"],
    ["document.location, as a value", "export const f = () => document.location;"],
    ["document.URL", "export const f = () => document.URL;"],
    ["document.documentURI", "export const f = () => document.documentURI;"],
    ["document.baseURI", "export const f = () => document.baseURI;"],
    ["document.referrer", "export const f = () => document.referrer;"],
    ["document['URL']", "export const f = () => document['URL'];"],
    ["window.document.URL", "export const f = () => window.document.URL;"],
  ];

  it.each(["src/probe.tsx", "src/catalogue/probe.tsx", "src/layout/probe.tsx", "src/operator/probe.tsx", "src/harness/probe.tsx", "src/api/probe.ts", "src/api/curator.ts", "src/api/client.ts"].flatMap((file) => READS.map(([name, code]) => [file, name, code] as const)))(
    "%s refuses %s",
    async (file, _name, code) => {
      expect((await ids(code, file)).length).toBeGreaterThan(0);
    },
  );

  it.each([
    ["a dynamic import of the package", 'export const f = () => import("react-router");'],
    ["a dynamic import of another path of it", 'export const f = () => import("react-router/dom");'],
    ["an import type of it", 'export type F = typeof import("react-router").useParams;'],
    ["a dynamic import of the other package", 'export const f = () => import("react-router-dom");'],
  ])("refuses %s, in every layer", async (_case, code) => {
    for (const file of ["src/probe.tsx", "src/catalogue/probe.tsx", "src/layout/probe.tsx", "src/operator/probe.tsx", "src/api/probe.ts", "src/api/client.ts", MODULE]) {
      expect((await ids(code, file)).length, file).toBeGreaterThan(0);
    }
  });

  it("accepts the router module's own read, the location's server-given members, and a document's other members", async () => {
    expect(await ids("export const f = () => [location.origin, location.host, location.protocol];", "src/catalogue/probe.tsx")).not.toContain("no-restricted-syntax");
    expect(await ids(OWN_TEXT, MODULE)).toEqual([]);
    expect(await ids("export const f = () => document.title.length + document.body.childElementCount;", "src/catalogue/probe.tsx")).not.toContain("no-restricted-syntax");
  });

  it("refuses the router module the channels it does not need (hash, href: the same row as elsewhere)", async () => {
    const own = (read: string): string => OWN_TEXT.replace("DATASET_PATH.exec(location.pathname)", `DATASET_PATH.exec(${read})`);
    expect(await ids(own("location.pathname"), MODULE)).toEqual([]);
    expect((await ids(own("location.hash"), MODULE)).length).toBeGreaterThan(0);
    expect((await ids(own("location.href"), MODULE)).length).toBeGreaterThan(0);
  });

  it("holds the router module to its one import: useParams (the lint's own list for it)", async () => {
    expect(await ids(OWN_TEXT.replace("{ useParams }", "{ useParams, useMatches }"), MODULE)).toContain("@typescript-eslint/no-restricted-imports");
    expect(await ids(OWN_TEXT.replace("{ useParams }", "{ useLocation }").replace("useParams()", "useLocation()"), MODULE)).toContain("@typescript-eslint/no-restricted-imports");
    expect(await ids(OWN_TEXT.replace('import type { ReactElement } from "react";', 'import type { ReactElement } from "react";\nimport { RouterProvider } from "react-router/dom";'), MODULE)).toContain("@typescript-eslint/no-restricted-imports");
  });
});

describe("the app may use none of the corpus's readers of the address (each is shown raw through the real hash router: tests/unit/readers.test.tsx)", () => {
  it.each(READERS)("$label is refused", async ({ source }) => {
    if (source.startsWith("import")) {
      expect(refused("src/catalogue/probe.tsx", source, ALLOWED).length).toBeGreaterThan(0);
    }
    expect((await ids(source, "src/catalogue/probe.tsx")).length).toBeGreaterThan(0);
  });

  it("round 1's probe, as the review wrote it, is refused: it reads useMatches, useMatch, useResolvedPath, useHref, UNSAFE_LocationContext, location and document.URL", async () => {
    const text = [
      'import { useContext, type ReactElement } from "react";',
      'import { UNSAFE_LocationContext, useHref, useMatch, useMatches, useResolvedPath } from "react-router";',
      "export function Leak(): ReactElement {",
      "  const matches = useMatches();",
      '  const match = useMatch("/datasets/:id");',
      '  const resolved = useResolvedPath(".");',
      '  const href = useHref(".");',
      "  const where = useContext(UNSAFE_LocationContext);",
      "  return <ul><li>{matches.at(-1)?.pathname}</li><li>{match?.params.id}</li><li>{resolved.pathname}</li><li>{href}</li><li>{where.location.pathname}</li><li>{location.pathname}{document.URL}</li></ul>;",
      "}",
      "",
    ].join("\n");
    expect(refused("src/catalogue/ProbeAddress.tsx", text, ALLOWED)).toHaveLength(5);
    const found = await eslint.lintText(text, { filePath: path.join(WEB, "src/catalogue/probe.tsx") });
    const messages = (found[0]?.messages ?? []).filter((message) => message.ruleId !== null && POLICY.has(message.ruleId));
    expect(messages.filter((message) => message.ruleId === "@typescript-eslint/no-restricted-imports")).toHaveLength(5);
    // `location.pathname`, `document.URL`, and `where.location.pathname` (any `.location` member).
    expect(messages.filter((message) => message.ruleId === "no-restricted-syntax")).toHaveLength(3);
  });
});

const OWN_TEXT = readFileSync(path.join(WEB, MODULE), "utf8");
const CALL = 'history.replaceState(null, "", id === null ? "/" : `/#/datasets/${id}`);';

/** The module with its one call replaced. */
const calling = (call: string): string => {
  expect(OWN_TEXT).toContain(CALL);
  return OWN_TEXT.replace(CALL, call);
};

describe("the router module writes the address in its one call, to / or /#/datasets/<DatasetId> (D428)", () => {
  it("accepts the module as it is", async () => {
    expect(await ids(OWN_TEXT, MODULE)).toEqual([]);
  });

  it.each([
    ["the raw path", 'history.replaceState(null, "", location.pathname);'],
    ["the raw segment, concatenated", 'history.replaceState(null, "", "/#/datasets/" + (found[1] ?? ""));'],
    ["the raw segment, in the template", "history.replaceState(null, \"\", `/#/datasets/${found[1] ?? \"\"}`);"],
    ["a DatasetId, concatenated", 'history.replaceState(null, "", id === null ? "/" : "/#/datasets/" + id);'],
    ["a DatasetId after another prefix", "history.replaceState(null, \"\", id === null ? \"/\" : `//${id}`);"],
    ["a DatasetId alone", "history.replaceState(null, \"\", id === null ? \"/\" : `${id}`);"],
    ["a DatasetId and more", "history.replaceState(null, \"\", id === null ? \"/\" : `/#/datasets/${id}/x`);"],
    ["a template with two expressions (the raw segment after the id)", "history.replaceState(null, \"\", id === null ? \"/\" : `/#/datasets/${id}${found[1] ?? \"\"}`);"],
    [
      "a local alias named DatasetId (a brand of its own, minted from the raw segment)",
      'type DatasetId = string & { readonly local: true };\n  const local = (found[1] ?? "") as DatasetId;\n  history.replaceState(null, "", `/#/datasets/${local}`);',
    ],
    ["another literal", 'history.replaceState(null, "", id === null ? "/curate" : `/#/datasets/${id}`);'],
    ["another origin", 'history.replaceState(null, "", "https://example.org/");'],
    ["a state", 'history.replaceState({ id }, "", "/");'],
    ["a title", 'history.replaceState(null, "x", "/");'],
    ["two calls", `${CALL}\n  ${CALL}`],
    ["pushState", 'history.pushState(null, "", "/");'],
    ["location.replace", 'location.replace("/");'],
    ["location.hash", 'location.hash = "/";'],
  ])("refuses %s", async (_case, call) => {
    expect((await ids(calling(call), MODULE)).length).toBeGreaterThan(0);
  });

  it("refuses the same function anywhere else", async () => {
    expect((await ids(OWN_TEXT, "src/catalogue/probe.tsx")).length).toBeGreaterThan(0);
  });
});
