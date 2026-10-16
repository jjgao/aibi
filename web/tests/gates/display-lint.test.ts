/**
 * The display lint's tables (D429, `scripts/lint/display.mjs`), held by construction against what
 * the packages and the DOM library declare, and its wiring in `eslint.config.mjs`:
 *
 * - **The class of `ReactNode`, at a JSX position.** The members of `@types/react`'s `ReactNode`,
 *   enumerated through the checker from its declaration as written: each, instantiated at `string`
 *   (`ReactNode` and `AwaitedReactNode` written `string`), is refused by R90 as a JSX child, but the
 *   members `ALLOWED_MEMBERS` names
 *   with a reason; the experimental member is pinned to `never`. A release of the types that adds a
 *   member fails here until the rule refuses it or a reason allows it.
 * - **Every `exports` subpath** of `react`, `react-dom` and `react-router`: each is refused as a whole
 *   module and by a name off the list, in every file, but the names `SPECIFIERS` (and the entries'
 *   `react-dom/client`) allow; each allowed name is an export of its module.
 * - **The lists** (`CALLS`, `ADDRESS`, `BRANDS`, `ELEMENTS`, `ATTRIBUTES`, `REF_MODULES`, `SITES`,
 *   `ENTRIES`, the router's names): each names something that exists where it says (a list that names
 *   nothing allows nothing, and hides that it is stale).
 * - **The wiring**: for every file of `src/`, the configuration in force has each rule where D429 says.
 * - **The sites and the exemptions**: a reviewed site that no longer stands, or stands twice, is
 *   reported; the harness's `data-*` and the door's file are exempt from R90 and nothing else is.
 */
import { readFileSync } from "node:fs";
import path from "node:path";

import ts from "typescript";
import { describe, expect, it } from "vitest";

import {
  ADDRESS,
  ATTRIBUTES,
  BRANDS,
  brandsOf,
  CALLS,
  domKey,
  ELEMENTS,
  ENTRIES,
  ENTRY_SPECIFIERS,
  isDomLibrary,
  LITERAL_ATTRIBUTES,
  OPTION_MODULE,
  REF_MODULES,
  ROUTER_SPECIFIERS,
  SITES,
  SPECIFIERS,
} from "../../scripts/lint/display.mjs";
import { ADDRESS_ENTRY_CASES, type Case, displayLinter, lintCases, wrong } from "./display-lint";
import { check, WEB } from "./program";
import { ALLOWED as ROUTER_ALLOWED, MODULE, OWN as ROUTER_OWN, sources } from "./router-scan";

const linter = displayLinter();
const CATALOGUE = "src/catalogue/probe-display.tsx";

/** The members of `ReactNode` the rule lets through, each with its reason. */
const ALLOWED_MEMBERS: Readonly<Record<string, string>> = {
  ReactElement: "Made by JSX alone: every other maker (`createElement`, `cloneElement`, `jsx`) is refused by the specifiers, and JSX's own positions are the rule's.",
  ReactPortal: "Made by `createPortal` alone, which the specifiers refuse (`react-dom`).",
  boolean: "Renders nothing.",
  null: "Renders nothing.",
  undefined: "Renders nothing.",
};

/** The experimental member, pinned to `never`: widened only by a declaration (`declare module`, refused
 * in `src/`) or a `types` entry of the configuration. */
const EXPERIMENTAL = /^DO_NOT_USE_OR_YOU_WILL_BE_FIRED_EXPERIMENTAL_REACT_NODES\[/u;

describe("every member of ReactNode, instantiated at string, is refused at a JSX position but the allowed (the class test)", () => {
  const checked = check({ "src/probe-reactnode.tsx": 'import type { ReactNode } from "react";\nexport type Shown = ReactNode;\n' });
  const statement = checked.source("src/probe-reactnode.tsx").statements[1];
  if (statement === undefined || !ts.isTypeAliasDeclaration(statement) || !ts.isTypeReferenceNode(statement.type)) {
    throw new Error("the probe's alias is not where it was written");
  }
  const named = checked.checker.getSymbolAtLocation(statement.type.typeName);
  const symbol = named === undefined ? undefined : (named.flags & ts.SymbolFlags.Alias) !== 0 ? checked.checker.getAliasedSymbol(named) : named;
  const declaration = symbol?.declarations?.find(ts.isTypeAliasDeclaration);
  if (declaration === undefined || !ts.isUnionTypeNode(declaration.type)) {
    throw new Error("ReactNode is no union as written");
  }
  const members = declaration.type.types.map((member) => ({ text: member.getText().replace(/\s+/gu, " "), type: checked.checker.getTypeFromTypeNode(member) }));

  it("reads ReactNode from @types/react, as written, with its members", () => {
    expect(declaration.getSourceFile().fileName.replaceAll("\\", "/")).toMatch(/\/node_modules\/@types\/react\/index\.d\.ts$/u);
    expect(members.length).toBeGreaterThanOrEqual(11);
    for (const name of Object.keys(ALLOWED_MEMBERS)) {
      expect(members.map(({ text }) => text)).toContain(name);
    }
  });

  it("pins the experimental member to never", () => {
    const experimental = members.filter(({ text }) => EXPERIMENTAL.test(text));
    expect(experimental).toHaveLength(1);
    expect(experimental.every(({ type }) => (type.flags & ts.TypeFlags.Never) !== 0)).toBe(true);
  });

  it("refuses every other member at string, and lets the allowed through", async () => {
    const others = members.filter(({ text }) => !EXPERIMENTAL.test(text));
    const cases: Case[] = others.map(({ text }, index) => {
      const at = text.replace(/\b(Awaited)?ReactNode\b/gu, "string");
      return { id: text, code: `declare const m${String(index)}: ${at};\nexport const e${String(index)} = <p>{m${String(index)}}</p>;`, refused: Object.hasOwn(ALLOWED_MEMBERS, text) ? [] : ["raw-text"] };
    });
    const linted = await lintCases(linter, CATALOGUE, 'import type { ReactElement, ReactPortal } from "react";', cases);
    expect(linted.fatal).toEqual([]);
    expect(wrong(linted, cases)).toEqual([]);
    // What the class came to today: five members refused (a change of the types is a change here).
    expect(cases.filter(({ refused }) => refused.length > 0).map(({ id }) => id)).toEqual(["string", "number", "bigint", "Iterable<ReactNode>", "Promise<AwaitedReactNode>"]);
  });
});

/** The specifiers of a package's `exports`, each subpath made a specifier (`.` the package itself). */
function subpaths(name: string): string[] {
  const manifest: unknown = JSON.parse(readFileSync(path.join(WEB, "node_modules", name, "package.json"), "utf8"));
  const exported = typeof manifest === "object" && manifest !== null && "exports" in manifest ? manifest.exports : null;
  if (typeof exported !== "object" || exported === null) {
    throw new Error(`${name} has no exports map`);
  }
  return Object.keys(exported).map((key) => (key === "." ? name : `${name}/${key.replace(/^\.\//u, "")}`));
}

const PACKAGES = ["react", "react-dom", "react-router"];
const ALL_SUBPATHS = PACKAGES.flatMap(subpaths);

describe("every exports subpath of react, react-dom and react-router is refused but the listed names", () => {
  it("enumerates the subpaths (25 today: a release that adds one is read here)", () => {
    expect(ALL_SUBPATHS).toEqual(expect.arrayContaining(["react/jsx-runtime", "react/jsx-dev-runtime", "react-dom/server", "react-dom/static", "react-dom/profiling", "react-dom/client", "react-router/dom"]));
    expect(ALL_SUBPATHS.length).toBeGreaterThanOrEqual(25);
  });

  /** One case per subpath: a whole module, and a name off every list. */
  const refusedEverywhere: Case[] = ALL_SUBPATHS.flatMap((specifier, index) => [
    { id: `${specifier} whole`, code: `import * as w${String(index)} from "${specifier}";\nexport const uw${String(index)} = w${String(index)};`, refused: ["specifiers"] },
    { id: `${specifier} by a name off the list`, code: `import { offTheList as n${String(index)} } from "${specifier}";\nexport const un${String(index)} = n${String(index)};`, refused: ["specifiers"] },
    { id: `${specifier} dynamically`, code: `export const d${String(index)} = () => import("${specifier}");`, refused: ["specifiers"] },
  ]);

  it.each([CATALOGUE, "src/layout/probe-display.tsx", "src/api/probe-display.ts", "src/harness/probe-display.tsx"])("in %s", async (file) => {
    const linted = await lintCases(linter, file, "", refusedEverywhere);
    expect(linted.fatal).toEqual([]);
    expect(wrong(linted, refusedEverywhere)).toEqual([]);
  });

  it("in the entries and the router module (their own lists)", async () => {
    for (const file of [...ENTRIES, MODULE]) {
      const linted = await lintCases(linter, file, "", refusedEverywhere);
      expect(wrong(linted, refusedEverywhere), file).toEqual([]);
    }
  });

  it("allows each listed name of each listed specifier, and react-dom/client's createRoot in the entries alone", async () => {
    const allowed = (table: Readonly<Record<string, { readonly values: readonly string[] }>>): Case[] =>
      Object.entries(table).flatMap(([specifier, { values }]) => values.map((name, index) => ({ id: `${specifier}: ${name}`, code: `import { ${name} as a${String(index)}${specifier.replace(/\W/gu, "_")} } from "${specifier}";\nexport const ua${String(index)}${specifier.replace(/\W/gu, "_")} = a${String(index)}${specifier.replace(/\W/gu, "_")};`, refused: [] })));
    const app = await lintCases(linter, CATALOGUE, "", allowed(SPECIFIERS));
    expect(wrong(app, allowed(SPECIFIERS))).toEqual([]);
    for (const entry of ENTRIES) {
      const linted = await lintCases(linter, entry, "", allowed(ENTRY_SPECIFIERS));
      expect(wrong(linted, allowed(ENTRY_SPECIFIERS)), entry).toEqual([]);
    }
    const own = await lintCases(linter, MODULE, "", allowed(ROUTER_SPECIFIERS));
    expect(wrong(own, allowed(ROUTER_SPECIFIERS))).toEqual([]);
    const elsewhere = await lintCases(linter, CATALOGUE, "", [{ id: "createRoot", code: 'import { createRoot } from "react-dom/client";\nexport const cr = createRoot;', refused: ["specifiers"] }]);
    expect(elsewhere.cases.get("createRoot")).toContain("specifiers");
  });

  it("lists only names its modules export, and specifiers of the packages' exports", () => {
    const tables = [SPECIFIERS, ENTRY_SPECIFIERS, ROUTER_SPECIFIERS];
    const specifiers = [...new Set(tables.flatMap((table) => Object.keys(table)))];
    expect(specifiers.every((specifier) => ALL_SUBPATHS.includes(specifier))).toBe(true);
    const probe = specifiers.map((specifier, index) => `import * as m${String(index)} from "${specifier}";\nexport { m${String(index)} };`).join("\n");
    const checked = check({ "src/probe-specifiers.ts": probe });
    const exported = new Map<string, string[]>();
    for (const statement of checked.source("src/probe-specifiers.ts").statements) {
      if (ts.isImportDeclaration(statement) && ts.isStringLiteralLike(statement.moduleSpecifier)) {
        const symbol = checked.checker.getSymbolAtLocation(statement.moduleSpecifier);
        exported.set(statement.moduleSpecifier.text, symbol === undefined ? [] : checked.checker.getExportsOfModule(symbol).map((member) => member.name));
      }
    }
    for (const table of tables) {
      for (const [specifier, { values, types }] of Object.entries(table)) {
        for (const name of [...values, ...(types === "*" ? [] : types)]) {
          expect(exported.get(specifier), `${specifier}: ${name}`).toContain(name);
        }
      }
    }
  });

  it("holds the router's names equal to the router module's lists (D428)", () => {
    const names = (table: Readonly<Record<string, { readonly values: readonly string[]; readonly types: readonly string[] | "*" }>>): Record<string, string[]> =>
      Object.fromEntries(Object.entries(table).filter(([specifier]) => specifier.startsWith("react-router")).map(([specifier, { values, types }]) => [specifier, [...values, ...(types === "*" ? ["*"] : types)].sort()]));
    expect(names(SPECIFIERS)).toEqual(Object.fromEntries(Object.entries(ROUTER_ALLOWED).map(([specifier, list]) => [specifier, [...list].sort()])));
    expect(names(ROUTER_SPECIFIERS)).toEqual(Object.fromEntries(Object.entries(ROUTER_OWN).map(([specifier, list]) => [specifier, [...list].sort()])));
  });
});

/** The symbol a key names in the DOM library, through a probe. */
function domMember(checked: ReturnType<typeof check>, file: string, index: number): ts.Symbol | undefined {
  const statement = checked.source(file).statements[index];
  if (statement === undefined || !ts.isVariableStatement(statement)) {
    return undefined;
  }
  const initializer = statement.declarationList.declarations[0]?.initializer;
  if (initializer === undefined) {
    return undefined;
  }
  return ts.isPropertyAccessExpression(initializer) ? checked.checker.getSymbolAtLocation(initializer.name) : checked.checker.getSymbolAtLocation(initializer);
}

describe("each list names what exists where it says", () => {
  const keys = [...CALLS.keys(), ...ADDRESS];
  /** Each key's probe: its statements, the last of which reads the member. */
  const probes = keys.map((key, index) => {
    const dot = key.indexOf(".");
    return dot < 0 ? [`export const k${String(index)} = ${key};`] : [`declare const o${String(index)}: ${key.slice(0, dot)};`, `export const k${String(index)} = o${String(index)}.${key.slice(dot + 1)};`];
  });
  const checked = check({ "src/probe-dom-keys.ts": probes.flat().join("\n") });

  it("CALLS and ADDRESS: each key is a member (or a global) the DOM library declares, by that key", () => {
    const missing: string[] = [];
    let at = -1;
    keys.forEach((key, index) => {
      at += probes[index]?.length ?? 0;
      const symbol = domMember(checked, "src/probe-dom-keys.ts", at);
      const declared = (symbol?.declarations ?? []).filter((declaration) => isDomLibrary(checked.program, declaration.getSourceFile())).map((declaration) => domKey(declaration, String(symbol?.getName())));
      if (!declared.includes(key)) {
        missing.push(`${key} (${declared.join(", ")})`);
      }
    });
    expect(checked.source("src/probe-dom-keys.ts").statements.length).toBe(at + 1);
    expect(missing).toEqual([]);
  });

  it("ADDRESS: one hand-written row for each entry, naming it (a removed entry fails its row, a new one needs a row)", () => {
    expect(ADDRESS_ENTRY_CASES.map(({ said }) => said).sort()).toEqual([...ADDRESS].sort());
    expect(ADDRESS_ENTRY_CASES.every(({ refused }) => refused.length === 1 && refused[0] === "address")).toBe(true);
  });

  it("CALLS: each with a reason", () => {
    expect([...CALLS.values()].every((reason) => reason.length > 10)).toBe(true);
  });

  it("BRANDS: each brand is the unique symbol its file declares, and the type its file exports carries it", () => {
    const brandProbe = BRANDS.map(({ type, file }, index) => `import type { ${type} } from "./${file.replace(/^src\//u, "").replace(/\.tsx?$/u, "")}";\ndeclare const b${String(index)}: ${type};\nexport const v${String(index)} = b${String(index)};`).join("\n");
    const brands = check({ "src/probe-brands.ts": brandProbe });
    BRANDS.forEach(({ type, brand, file, reason }, index) => {
      expect(readFileSync(path.join(WEB, file), "utf8")).toContain(`declare const ${brand}: unique symbol;`);
      expect(reason.length).toBeGreaterThan(10);
      const statement = brands.source("src/probe-brands.ts").statements[index * 3 + 2];
      const declared = statement !== undefined && ts.isVariableStatement(statement) ? statement.declarationList.declarations[0] : undefined;
      const found = declared === undefined ? undefined : brands.checker.getTypeAtLocation(declared.name);
      expect(found?.isIntersection(), type).toBe(true);
      const carried = found !== undefined && found.isIntersection() ? found.types.flatMap((member) => brandsOf(brands.checker, member)) : [];
      expect(carried, type).toEqual([type]);
    });
  });

  it("ELEMENTS and ATTRIBUTES: each an intrinsic element, each an attribute of one", () => {
    const elements = check({ "src/probe-elements.tsx": `import type { JSX } from "react";\nexport type Elements = JSX.IntrinsicElements;\nexport type Props = ${ELEMENTS.map((name) => `JSX.IntrinsicElements["${name}"]`).join(" | ")};\n` });
    const source = elements.source("src/probe-elements.tsx");
    const [, first, second] = source.statements;
    if (first === undefined || second === undefined || !ts.isTypeAliasDeclaration(first) || !ts.isTypeAliasDeclaration(second)) {
      throw new Error("the probe's aliases are not where they were written");
    }
    const intrinsic = elements.checker.getTypeAtLocation(first.name).getProperties().map((property) => property.name);
    expect(ELEMENTS.filter((name) => !intrinsic.includes(name))).toEqual([]);
    const props = elements.checker.getTypeAtLocation(second.name);
    const all = new Set((props.isUnion() ? props.types : [props]).flatMap((member) => member.getProperties().map((property) => property.name)));
    expect(ATTRIBUTES.filter((name) => !all.has(name))).toEqual([]);
    expect(LITERAL_ATTRIBUTES.filter((name) => !ATTRIBUTES.includes(name))).toEqual([]);
  });

  it("REF_MODULES, OPTION_MODULE and SITES: each file exists and holds what it is listed for, with a reason", () => {
    for (const file of REF_MODULES) {
      expect(readFileSync(path.join(WEB, file), "utf8"), file).toMatch(/\bref=\{/u);
    }
    expect(readFileSync(path.join(WEB, OPTION_MODULE), "utf8")).toContain("<option ");
    for (const { file, code, reason } of SITES) {
      expect(readFileSync(path.join(WEB, file), "utf8"), `${file}: ${code}`).toContain(code);
      expect(reason.length).toBeGreaterThan(10);
    }
  });

  it("ENTRIES: the two documents' scripts (index.html, operator.html)", () => {
    const scripts = ["index.html", "operator.html"].flatMap((document) => [...readFileSync(path.join(WEB, document), "utf8").matchAll(/<script[^>]*\bsrc="\/([^"]+)"/gu)].map((match) => match[1]));
    expect([...scripts].sort()).toEqual([...ENTRIES].sort());
  });
});

/** A rule's severity and options in the configuration in force for a file. */
async function ruleIn(file: string, rule: string): Promise<readonly unknown[]> {
  const config: unknown = await linter.calculateConfigForFile(path.join(WEB, file));
  const rules = typeof config === "object" && config !== null && "rules" in config ? config.rules : null;
  const entry: unknown = typeof rules === "object" && rules !== null ? Object.entries(rules).find(([name]) => name === rule)?.[1] : undefined;
  if (Array.isArray(entry)) {
    const listed: readonly unknown[] = entry;
    return listed;
  }
  return entry === undefined ? [0] : [entry];
}

describe("the configuration wires each rule where D429 says (every file of src/)", () => {
  const files = sources().filter((file) => /\.(ts|tsx)$/u.test(file));

  it("raw-text, elements, address and specifiers in every file (raw-text but the door's); dom and answers outside src/api/", async () => {
    const wrongly: string[] = [];
    for (const file of files) {
      const api = file.startsWith("src/api/");
      const expected: Record<string, boolean> = {
        "display/raw-text": file !== "src/api/text.tsx",
        "display/elements": true,
        "display/address": true,
        "display/specifiers": true,
        "display/dom": !api,
        "display/answers": !api,
      };
      for (const [rule, on] of Object.entries(expected)) {
        const [severity] = await ruleIn(file, rule);
        if ((severity === 2 || severity === "error") !== on) {
          wrongly.push(`${file}: ${rule} ${on ? "off" : "on"}`);
        }
      }
      const [, options] = await ruleIn(file, "display/specifiers");
      const table = ENTRIES.includes(file) ? ENTRY_SPECIFIERS : file === MODULE ? ROUTER_SPECIFIERS : SPECIFIERS;
      if (JSON.stringify(options) !== JSON.stringify({ allowed: table })) {
        wrongly.push(`${file}: display/specifiers' table`);
      }
      const [, address] = await ruleIn(file, "display/address");
      if (JSON.stringify(address ?? null) !== JSON.stringify(file === MODULE ? { allow: ["Location.pathname"] } : null)) {
        wrongly.push(`${file}: display/address's option`);
      }
    }
    expect(files.length).toBeGreaterThan(30);
    expect(wrongly).toEqual([]);
  }, 120_000);
});

describe("the reviewed sites stand once, and the exemptions are where D429 says", () => {
  const SHELL = "src/operator/Shell.tsx";
  const shell = readFileSync(path.join(WEB, SHELL), "utf8");
  const HARNESS_FILE = "src/harness/Harness.tsx";
  const harness = readFileSync(path.join(WEB, HARNESS_FILE), "utf8");

  async function displayIds(file: string, text: string): Promise<string[]> {
    const [result] = await linter.lintText(text, { filePath: path.join(WEB, file) });
    return (result?.messages ?? []).flatMap((message) => (message.ruleId?.startsWith("display/") === true ? [`${message.ruleId}: ${message.message.slice(0, 40)}`] : []));
  }

  it("the real files hold their sites, once each (the control)", async () => {
    expect(await displayIds(SHELL, shell)).toEqual([]);
    expect(await displayIds(HARNESS_FILE, harness)).toEqual([]);
  });

  it("a site that no longer stands is reported, and so is one that stands twice", async () => {
    const write = 'field.value = "";';
    expect(shell).toContain(write);
    expect((await displayIds(SHELL, shell.replace(write, ""))).join("\n")).toMatch(/display\/dom: A reviewed site/u);
    expect((await displayIds(SHELL, shell.replace(write, `${write}\n      ${write}`))).join("\n")).toMatch(/display\/dom: A reviewed site/u);
    const read = "found.body";
    expect(harness).toContain(read);
    expect((await displayIds(HARNESS_FILE, harness.replace(read, `${read} ?? ${read}`))).join("\n")).toMatch(/display\/answers: A reviewed site/u);
  });

  it("a site is its text exactly: a write whose text holds the site's is a write of its own", async () => {
    const write = 'field.value = "";';
    const messages = await displayIds(SHELL, shell.replace(write, `${write}\n      const box: { readonly field: HTMLInputElement } = { field: document.createElement("input") };\n      box.field.value = "";`));
    expect(messages.join("\n")).toMatch(/display\/dom: No write to the DOM/u);
    expect(messages.join("\n")).not.toMatch(/A reviewed site/u);
  });

  it("names what a specifier refusal is: a whole module, a side effect, the names", async () => {
    const [result] = await linter.lintText('import * as whole from "react";\nimport "react";\nimport { createElement } from "react";\nexport const used = [whole, createElement];\n', { filePath: path.join(WEB, CATALOGUE) });
    const said = (result?.messages ?? []).filter((message) => message.ruleId === "display/specifiers").map((message) => message.message);
    expect(said).toEqual([expect.stringContaining("(a whole module)"), expect.stringContaining("(a side-effect import)"), expect.stringContaining("{ createElement }")]);
  });

  it("a site's code elsewhere is refused (a site is its file's)", async () => {
    const cases: Case[] = [
      { id: "Shell's write, in another module", code: "export function f(field: HTMLInputElement): void { field.value = \"\"; }", refused: ["dom"] },
      { id: "the harness's read, in another module", code: 'import type { Outcome } from "../api/operator";\nexport const f = (found: Outcome) => (found.kind === "answer" ? found.body : null);', refused: ["answers"] },
    ];
    const linted = await lintCases(linter, "src/operator/probe-display.tsx", "", cases);
    expect(wrong(linted, cases)).toEqual([]);
  });

  it("the harness's data attributes are exempt from R90 and the literal rule, and nothing else of it", async () => {
    const header = "declare const raw: string;\ndeclare const n: number;";
    const exempt: Case[] = [{ id: "a data attribute of a string and a number", code: "export const d = <output data-codes={raw} data-runs={n} />;", refused: [] }];
    const held: Case[] = [
      { id: "an aria-label", code: "export const a = <p aria-label={raw}>x</p>;", refused: ["raw-text", "elements"] },
      { id: "a child", code: "export const c = <p>{raw}</p>;", refused: ["raw-text"] },
      { id: "a DOM write", code: "export const w = (el: HTMLElement) => { el.title = raw; };", refused: ["dom"] },
    ];
    const inHarness = await lintCases(linter, "src/harness/probe-display.tsx", header, [...exempt, ...held]);
    expect(wrong(inHarness, [...exempt, ...held])).toEqual([]);
    const elsewhere = await lintCases(linter, CATALOGUE, header, [{ ...exempt[0], refused: ["raw-text", "elements"] } as Case]);
    expect(wrong(elsewhere, [{ ...exempt[0], refused: ["raw-text", "elements"] } as Case])).toEqual([]);
  });

  it("a row's said is asserted: a refusal whose message does not say it is wrong (the negative control)", async () => {
    const refused: Case = { id: "a raw child", code: "declare const raw: string;\nexport const shown = <p>{raw}</p>;", refused: ["raw-text"], said: "as a JSX child" };
    const linted = await lintCases(linter, CATALOGUE, "", [refused]);
    expect(wrong(linted, [refused])).toEqual([]);
    expect(wrong(linted, [{ ...refused, said: "words no message holds" }])).toEqual([expect.stringContaining('no message says "words no message holds"')]);
    expect(wrong(linted, [{ ...refused, refused: ["raw-text", "eslint:no-such-rule"] }])).toEqual([expect.stringContaining("not refused by eslint:no-such-rule")]);
  });

  it("a member of an any value is refused by strictTypeChecked, which R91 relies on", async () => {
    const [result] = await linter.lintText("declare const anyv: any;\ndeclare const raw: string;\nanyv.textContent = raw;\nanyv.append(raw);\n", { filePath: path.join(WEB, CATALOGUE) });
    const ids = (result?.messages ?? []).map((message) => message.ruleId);
    expect(ids).toEqual(expect.arrayContaining(["@typescript-eslint/no-unsafe-member-access", "@typescript-eslint/no-unsafe-call"]));
  });

  it("src/api/ is held to R90 (but the door's file), and not to R91 or R93", async () => {
    const cases: Case[] = [
      { id: "a JSX child in the seam", code: "declare const raw: string;\nexport const shown = <p>{raw}</p>;", refused: ["raw-text"] },
      { id: "the seam's own DOM", code: 'document.addEventListener("visibilitychange", () => undefined);', refused: [] },
    ];
    const linted = await lintCases(linter, "src/api/probe-display.tsx", "", cases);
    expect(wrong(linted, cases)).toEqual([]);
  });

  it("the router module reads location.pathname, and no other member of the address", async () => {
    const own = readFileSync(path.join(WEB, MODULE), "utf8");
    expect(own).toContain("location.pathname");
    expect(await displayIds(MODULE, own)).toEqual([]);
    expect((await displayIds(MODULE, own.replace("DATASET_PATH.exec(location.pathname)", "DATASET_PATH.exec(location.search)"))).join("\n")).toMatch(/display\/address/u);
  });
});

describe("the run-time cases of #119 (tests/unit/lint-display-runtime.test.tsx renders each raw)", () => {
  const text = readFileSync(path.join(WEB, "tests/unit/lint-display-runtime.test.tsx"), "utf8");
  const cases: Case[] = [...text.matchAll(/\/\/ (case|open): (.+)\n([\s\S]*?)\/\/ end/gu)].map(([, kind, id, code]) => ({ id: String(id), code: String(code).trim(), refused: kind === "case" ? ["raw-text"] : [] }));

  it("reads five cases: the lint refuses the exemption's two as a file of src/, and the flow's three stay OPEN (D429, 2b-1L2)", async () => {
    expect(cases.map(({ id, refused }) => `${refused.length > 0 ? "refused" : "open"}: ${id}`)).toEqual([
      "refused: own children with a default",
      "refused: own children reassigned",
      "open: a route's element built apart",
      "open: a getter named children, in a direct call",
      "open: a class field named children, in a direct call",
    ]);
    const header = 'import type { ReactNode } from "react";\nimport type { RouteObject } from "react-router";\ndeclare const RAW: string;';
    const linted = await lintCases(linter, CATALOGUE, header, cases);
    expect(linted.fatal).toEqual([]);
    expect(wrong(linted, cases)).toEqual([]);
  });
});
