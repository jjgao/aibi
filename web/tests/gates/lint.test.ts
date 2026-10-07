/**
 * The ESLint configuration's no-sink rules for the app's source (D418). The lint bans the names
 * of the sinks, not their spellings, so the test is a table: each name of the sink table in each
 * syntactic position (a property, a call, a computed or template key, an object key, a
 * destructuring, a JSX attribute, a JSX spread, an element, a member of the global object, any
 * case) is flagged in `src/`, and the same code outside `src/` is not (the rules are the app's).
 * The names are written out here, not read from the configuration, so that a name dropped from it
 * fails a cell. Trusted Types is the barrier at run time; the lint is defence in depth.
 */
import path from "node:path";

import { ESLint } from "eslint";
import { describe, expect, it } from "vitest";

const WEB = path.join(import.meta.dirname, "../..");

const eslint = new ESLint({
  cwd: WEB,
  overrideConfig: {
    languageOptions: {
      parserOptions: { projectService: { allowDefaultProject: ["src/*.tsx", "probe/*.ts"] } },
    },
  },
});

const POLICY_RULES = new Set([
  "no-restricted-syntax",
  "no-restricted-globals",
  "no-console",
  "no-eval",
  "no-implied-eval",
  "no-new-func",
]);

async function lint(code: string, file = "src/probe.tsx") {
  const [result] = await eslint.lintText(code, { filePath: path.join(WEB, file) });
  const messages = result?.messages ?? [];
  const fatal = messages.filter((message) => message.fatal === true);
  expect(fatal.map((message) => message.message)).toEqual([]);
  return messages;
}

/** The ids of the policy's rules that flag `code`. */
async function flagged(code: string, file = "src/probe.tsx"): Promise<string[]> {
  const messages = await lint(code, file);
  return messages.flatMap((message) => (message.ruleId !== null && POLICY_RULES.has(message.ruleId) ? [message.ruleId] : []));
}

const NAMES = [
  "innerHTML",
  "outerHTML",
  "insertAdjacentHTML",
  "srcdoc",
  "createContextualFragment",
  "setHTMLUnsafe",
  "parseHTMLUnsafe",
  "DOMParser",
  "dangerouslySetInnerHTML",
  "setAttributeNS",
  "createAttribute",
  "createAttributeNS",
  "setAttributeNode",
  "setAttributeNodeNS",
  "script",
  "Reflect",
  "Function",
];

/** Each position a name can be written in, as code over `@N@` (the name) and `@U@` (in capitals).
 * The positions that write it as text (`text`) do not apply to the names a string is too common
 * for (`TEXT_EXEMPT`: `typeof x === 'function'`), which a global is reached without. */
const POSITIONS: [string, string, boolean][] = [
  ["a property", "export const f = (el: any) => el.@N@;", false],
  ["a property in capitals", "export const f = (el: any) => el.@U@;", false],
  ["a call", "export const f = (el: any, s: string) => el.@N@(s);", false],
  ["a computed literal", "export const f = (el: any) => el['@N@'];", true],
  ["a computed literal in capitals", 'export const f = (el: any) => el["@U@"];', true],
  ["a computed template", "export const f = (el: any) => el[`@N@`];", true],
  ["an object key", "export const f = (s: string) => ({ @N@: s });", false],
  ["a string key", "export const f = (s: string) => ({ '@N@': s });", true],
  ["a shorthand key", "export const f = (@N@: string) => ({ @N@ });", false],
  ["a destructuring", "export const f = (el: any) => { const { @N@: x } = el; return x; };", false],
  ["a shorthand destructuring", "export const f = (el: any) => { const { @N@ } = el; return @N@; };", false],
  ["an Object.assign", "export const f = (el: any, s: string) => Object.assign(el, { @N@: s });", false],
  ["a string key of Object.assign", "export const f = (el: any, s: string) => Object.assign(el, { '@N@': s });", true],
  ["a spread object", "export const f = (s: string) => ({ ...{ @N@: s } });", false],
  ["a JSX attribute", "export const f = (s: string) => <p @N@={s} />;", false],
  ["a JSX attribute in capitals", "export const f = (s: string) => <p @U@={s} />;", false],
  ["a JSX spread", "export const f = (s: string) => <p {...{ @N@: s }} />;", false],
  ["a JSX element", "export const f = () => <@N@ />;", false],
  ["a binding", "export const f = () => { const @N@ = 1; return @N@; };", false],
  ["an alias", "export const f = () => { const a = @N@; return a; };", false],
  ["a member of window", "export const f = (w: any) => w.window.@N@;", false],
  ["a computed literal of window", "export const f = () => window['@N@'];", false],
  ["a computed template of globalThis", "export const f = () => globalThis[`@N@`];", false],
  ["a member of globalThis", "export const f = () => globalThis.@N@;", false],
  ["a computed literal of self in capitals", "export const f = () => self['@U@'];", false],
];

const TEXT_EXEMPT = new Set(["Function", "Reflect"]);

const CELLS = NAMES.flatMap((name) =>
  POSITIONS.filter(([, , text]) => !(text && TEXT_EXEMPT.has(name))).map(
    ([position, code]) => [name, position, code.replaceAll("@N@", name).replaceAll("@U@", name.toUpperCase())] as const,
  ),
);

describe("the app's source may not write the name of a sink, in any position", () => {
  it.each(CELLS)("%s as %s", async (_name, _position, code) => {
    expect(await flagged(code)).toContain("no-restricted-syntax");
  });

  it("flags nothing outside src/ (the rules are the app's)", async () => {
    expect(await flagged("export const f = (el: any, s: string) => { el.innerHTML = s; };", "probe/x.ts")).toEqual([]);
    expect(await flagged("export const f = (el: any, s: string) => { Reflect.set(el, 'x', s); console.log(s); };", "probe/x.ts")).toEqual([]);
  });
});

describe("the globals the table names are refused as references too", () => {
  it.each(["Function", "Reflect", "DOMParser"])("%s", async (name) => {
    expect(await flagged(`export const f = () => { const a = ${name}; return a; };`)).toEqual(
      expect.arrayContaining(["no-restricted-globals", "no-restricted-syntax"]),
    );
  });
});

describe("the sinks that are not a name alone", () => {
  it.each([
    ["document.write", "export const f = (s: string) => { document.write(s); };"],
    ["document.writeln", "export const f = (s: string) => { document.writeln(s); };"],
    ["document['write']", "export const f = (s: string) => { document['write'](s); };"],
    ["window.document.write", "export const f = (s: string) => { window.document.write(s); };"],
    ["a member of window by a computed expression", "export const f = (n: string) => window[n];"],
    ["a member of globalThis by a computed expression", "export const f = () => globalThis['Func' + 'tion'];"],
    ["a member of self by a computed expression", "export const f = (n: string) => self[n];"],
    ["a member of top by a computed expression", "export const f = (n: string) => top?.[n];"],
    ["a member of parent by a computed expression", "export const f = (n: string) => parent[n];"],
    ["a member of frames by a computed expression", "export const f = (n: string) => frames[n];"],
    ["setAttribute onclick", "export const f = (el: Element, s: string) => { el.setAttribute('onclick', s); };"],
    ["setAttribute OnError", "export const f = (el: Element, s: string) => { el.setAttribute('OnError', s); };"],
    ["setAttribute srcdoc", "export const f = (el: Element, s: string) => { el.setAttribute('srcdoc', s); };"],
    ["setAttribute with a name given at run time", "export const f = (el: Element, n: string, s: string) => { el.setAttribute(n, s); };"],
    ["setAttribute with a template", "export const f = (el: Element, s: string) => { el.setAttribute(`onclick`, s); };"],
    ["setAttribute by a computed name", "export const f = (el: Element, s: string) => { el['setAttribute']('onclick', s); };"],
    ["setAttribute by a computed name and a run-time one", "export const f = (el: Element, n: string, s: string) => { el['setAttribute'](n, s); };"],
    ["setAttribute called through call", "export const f = (el: Element, s: string) => { el.setAttribute.call(el, 'onclick', s); };"],
    ["setAttribute taken as a value", "export const f = (el: Element) => { const set = el.setAttribute; return set; };"],
    ["createElement('script')", "export const f = () => document.createElement('script');"],
    ["createElement('SCRIPT')", "export const f = () => document.createElement('SCRIPT');"],
    ["createElement of a template", "export const f = () => document.createElement(`script`);"],
    ["createElement of a name given at run time", "export const f = (n: string) => document.createElement(n);"],
    ["createElement of a template with a substitution", "export const f = (n: string) => document.createElement(`${n}`);"],
    ["createElementNS('script')", "export const f = (ns: string) => document.createElementNS(ns, 'script');"],
    ["createElementNS of a template", "export const f = (ns: string) => document.createElementNS(ns, `Script`);"],
    ["createElementNS of a name given at run time", "export const f = (ns: string, n: string) => document.createElementNS(ns, n);"],
    ["createElement by a computed name", "export const f = (n: string) => document['createElement'](n);"],
    ["a script element in JSX", "export const f = () => <script src='x.js' />;"],
    ["a javascript: href", "export const f = () => <a href=\"javascript:void(0)\">x</a>;"],
    ["a javascript: string", "export const f = (a: HTMLAnchorElement) => { a.href = '  JavaScript:alert(1)'; };"],
    ["a javascript: template", "export const f = (a: HTMLAnchorElement, x: string) => { a.href = `javascript:${x}`; };"],
    [
      "script.src",
      "export const f = (s: string) => { const el = document.createElement('script'); el.src = s; document.head.append(el); };",
    ],
  ])("flags %s", async (_form, code) => {
    expect(await flagged(code)).toContain("no-restricted-syntax");
  });

  it.each([
    ["console", "export const f = (s: string) => { console.log(s); };", "no-console"],
    ["eval", "export const f = (s: string) => eval(s);", "no-eval"],
    ["indirect eval", "export const f = (s: string) => (0, eval)(s);", "no-eval"],
    ["new Function", "export const f = (s: string) => new Function(s);", "no-new-func"],
  ])("flags %s, by its own rule", async (_form, code, rule) => {
    expect(await flagged(code)).toContain(rule);
  });
});

describe("no inline configuration silences a rule", () => {
  const SINK = "export const f = (el: Element, s: string) => { el.innerHTML = s; };";

  it.each([
    ["a directive for the next line", `// eslint-disable-next-line no-restricted-syntax\n${SINK}`],
    ["a directive for the line", `${SINK} // eslint-disable-line no-restricted-syntax`],
    ["a directive for all rules of the next line", `// eslint-disable-next-line\n${SINK}`],
    ["a block directive for the file", `/* eslint-disable */\n${SINK}`],
    ["a block directive naming the rule", `/* eslint-disable no-restricted-syntax */\n${SINK}`],
    ["a rule set by a comment", `/* eslint no-restricted-syntax: off */\n${SINK}`],
  ])("%s", async (_form, code) => {
    const messages = await lint(code);
    expect(messages.some((message) => message.ruleId === "no-restricted-syntax")).toBe(true);
    const ignored = messages.filter((message) => message.ruleId === null && message.message.includes("noInlineConfig"));
    expect(ignored.length).toBeGreaterThan(0);
    expect(ignored.every((message) => message.severity >= 1)).toBe(true);
  });

  it("silences console as little", async () => {
    expect(await flagged("// eslint-disable-next-line no-console\nexport const f = () => { console.log('x'); };")).toContain("no-console");
  });
});

describe("ordinary code passes, so that a flag is the rule's", () => {
  it.each([
    [
      "ordinary DOM",
      "export const f = (el: Element) => { el.setAttribute('title', 'x'); el.textContent = 'javascript'; return document.createElement('div'); };",
    ],
    ["a template element", "export const f = () => document.createElement(`div`);"],
    ["a namespaced element", "export const f = (ns: string) => document.createElementNS(ns, 'svg');"],
    ["a link", "export const f = () => <a href=\"/curate\" rel=\"noopener noreferrer\">Curate</a>;"],
    ["a call of setAttribute", "export const f = (el: Element) => { el.setAttribute(\"class\", 'x'); };"],
    ["names that only contain a sink's", "export const f = (el: any) => { const scripts = el.scripts; const innerHTMLs = 1; const description = 2; return [scripts, innerHTMLs, description]; };"],
    ["computed access to an ordinary object", "export const f = (a: string[], i: number) => a[i];"],
    ["the global object, by name", "export const f = () => window.location.pathname + globalThis.name + self.name;"],
    ["words that are not sinks", "export const f = (x: unknown) => typeof x === 'function' && 'script tag' + 'reflect' + 'Function';"],
  ])("%s", async (_form, code) => {
    expect(await flagged(code)).toEqual([]);
  });
});
