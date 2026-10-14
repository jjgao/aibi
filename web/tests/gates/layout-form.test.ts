/**
 * The visible form's context and the door are the layout's (D428). `src/layout/form.ts` is imported
 * by `src/layout/` and `src/api/text.tsx` alone, so that each layout component sets its own form
 * (`tests/unit/layout.test.tsx` holds each component's output equal under every ancestor form) and
 * no screen can set a position's form around a component (round 1 of #117: a screen that wrapped
 * `Text` in `<FORM value="flat">` passed the lint and the compiler, and an escape lost its span).
 * **`showText`**, the one door from a server text to the DOM, is exported by `src/api/text.tsx`
 * and re-exported by the index, which is its pinned export that `src/layout/` alone imports (round
 * 2 of #117: a screen that called it rendered a text with no `<bdi>` and no `.data`, and the form's
 * default was reached by nothing that tested it).
 *
 * Two layers, each with its own controls: the lint (`no-restricted-imports` for the specifiers and
 * the name, and `no-restricted-syntax` for a dynamic or a type import), and a scan of the SYMBOLS
 * the program resolves (`reach.ts`): a file reaches `FORM` or `showText` by any import, re-export,
 * dynamic import or import type, through the alias chain, so that a layout module's re-export
 * (`export { FORM as Form2 } from "./form"`) does not let a screen import it from there (m12), and
 * a second scan holds the modules that OFFER a path to either symbol to the pinned ones.
 */
import { readFileSync } from "node:fs";
import path from "node:path";

import ts from "typescript";
import { describe, expect, it } from "vitest";

import { Linter } from "./linter";
import { check, type Checked, WEB } from "./program";
import { offeredBy, reachedBy, type Target } from "./reach";
import { sources } from "./router-scan";

const FORM_TARGET: Target = { file: path.join(WEB, "src/layout/form.ts") };
const SHOW_TARGET: Target = { file: path.join(WEB, "src/api/text.tsx"), names: ["showText"] };

/** Who may reach the form's context, and the door (the index's pinned export, for the layout). */
const mayForm = (file: string): boolean => file.startsWith("src/layout/") || file === "src/api/text.tsx";
const mayShow = (file: string): boolean => file.startsWith("src/layout/") || file === "src/api/text.tsx" || file === "src/api/index.ts";

const REAL = sources().sort();
const text = (file: string): string => readFileSync(path.join(WEB, file), "utf8");

/** One program: every file of `src/` as it is (or as `replaced` has it), and the probes. */
function program(probes: Readonly<Record<string, string>>, replaced: Readonly<Record<string, string>> = {}): Checked {
  return check({ ...Object.fromEntries(REAL.map((file) => [file, text(file)])), ...replaced, ...probes });
}

/** The files among `files` that reach a target, by project-relative name. */
const reaching = (checked: Checked, files: readonly string[], target: Target): string[] => files.filter((file) => reachedBy(checked, file, [target]).length > 0).sort();

describe("the program's reach of the visible form's context and of the door", () => {
  const checked = program({});

  it("the form's context is reached by src/layout/ and src/api/text.tsx alone, and exactly by the files that import it", () => {
    const found = reaching(checked, REAL, FORM_TARGET);
    expect(found.filter((file) => !mayForm(file))).toEqual([]);
    expect(found).toEqual(["src/api/text.tsx", "src/layout/Option.tsx", "src/layout/Segments.tsx", "src/layout/Text.tsx"]);
  });

  it("the door is reached by the layout alone, through the index (its pinned export), which re-exports it", () => {
    const found = reaching(checked, REAL, SHOW_TARGET);
    expect(found.filter((file) => !mayShow(file))).toEqual([]);
    expect(found).toEqual(["src/api/index.ts", "src/layout/Option.tsx", "src/layout/Segments.tsx", "src/layout/Text.tsx"]);
  });

  it("the modules that offer a path to either are the pinned ones: a re-export in another module is a new path (m12)", () => {
    expect(offeredBy(checked, REAL, [FORM_TARGET])).toEqual(["src/layout/form.ts: FORM", "src/layout/form.ts: Form"]);
    expect(offeredBy(checked, REAL, [SHOW_TARGET])).toEqual(["src/api/index.ts: showText", "src/api/text.tsx: showText"]);
  });
});

/** The value exports of each module of `src/layout/`, as `file: name`, each resolved through its alias
 * chain (a type is no value: `Form`, `Key`, `Typed`). */
function valueExports(checked: Checked): string[] {
  const { checker } = checked;
  return REAL.filter((file) => file.startsWith("src/layout/") && /\.tsx?$/u.test(file))
    .flatMap((file) => {
      const module = checker.getSymbolAtLocation(checked.source(file));
      return module === undefined
        ? []
        : checker
            .getExportsOfModule(module)
            .filter((member) => (((member.flags & ts.SymbolFlags.Alias) !== 0 ? checker.getAliasedSymbol(member) : member).flags & ts.SymbolFlags.Value) !== 0)
            .map((member) => `${file}: ${member.name}`);
    })
    .sort();
}

/** The layout's value exports, pinned per module (m3 of round 3 of #117): neither layer sees a value
 * that holds the door or the context behind an alias a layout module declares (`export const door =
 * showText`, `export const tools = { showText }`, `export const Form2 = FORM`, `FORM.Provider`), which
 * is an owner's edit of `src/layout/`; any new export is a change of this list, so a reviewed one. */
const LAYOUT_EXPORTS = [
  "src/layout/Field.tsx: Field",
  "src/layout/Field.tsx: UNTYPED",
  "src/layout/Link.tsx: Link",
  "src/layout/Option.tsx: Option",
  "src/layout/Segments.tsx: Segments",
  "src/layout/Text.tsx: Ref",
  "src/layout/Text.tsx: Text",
  "src/layout/form.ts: FORM",
  "src/layout/key.ts: NO_INDEX",
  "src/layout/key.ts: keyOf",
];

describe("the layout's value exports are pinned", () => {
  it("are exactly the pinned list", () => {
    expect(valueExports(program({}))).toEqual(LAYOUT_EXPORTS);
  });

  /** An edit of a layout module that hands a screen the door or the context by a value. */
  const HANDS: readonly (readonly [string, string, string])[] = [
    ["a const alias of the door", 'import { showText as door0 } from "../api";\nexport const door = door0;', "door"],
    ["an object holding the door", 'import { showText as door0 } from "../api";\nexport const tools = { door0 };', "tools"],
    ["an export of the door in braces", 'import { showText as door0 } from "../api";\nexport { door0 };', "door0"],
    ["a const alias of the context", 'import { FORM as form0 } from "./form";\nexport const Form2 = form0;', "Form2"],
    ["the context's provider", 'import { FORM as form0 } from "./form";\nexport const Provider = form0.Provider;', "Provider"],
    ["the door by a function that returns it", 'import { showText as door0 } from "../api";\nexport const get = () => door0;', "get"],
  ];

  it.each(HANDS)("sees a new export: %s", (_name, extra, name) => {
    const checked = program({}, { "src/layout/Text.tsx": `${text("src/layout/Text.tsx")}\n${extra}\n` });
    const found = valueExports(checked);
    expect(found).toContain(`src/layout/Text.tsx: ${name}`);
    expect(found).not.toEqual(LAYOUT_EXPORTS);
  });
});

/** Each spelling of an import of the form's context, as a screen might write it. */
const FORM_SPELLINGS: readonly (readonly [string, string])[] = [
  ["an import", 'import { FORM } from "../layout/form";'],
  ["an import with the extension", 'import { FORM } from "../layout/form.ts";'],
  ["an import by its JavaScript twin", 'import { FORM } from "../layout/form.js";'],
  ["an alias", 'import { FORM as Context } from "../layout/form";'],
  ["a type import", 'import type { Form } from "../layout/form";'],
  ["a namespace import", 'import * as form from "../layout/form";'],
  ["a re-export", 'export { FORM } from "../layout/form";'],
  ["a renamed re-export", 'export { FORM as Context } from "../layout/form";'],
  ["an export all", 'export * from "../layout/form";'],
  ["an export all as a namespace", 'export * as form from "../layout/form";'],
  ["a dynamic import", 'export const f = () => import("../layout/form");'],
  ["an import type", 'export type F = typeof import("../layout/form");'],
  ["an import-equals", 'import form = require("../layout/form");'],
  ["a path through another directory", 'import { FORM } from "../api/../layout/form";'],
  ["a path through the layout itself", 'import { FORM } from "../layout/../layout/form";'],
];

/** Each spelling of an import of the door, from the index or by its deep path. */
const SHOW_SPELLINGS: readonly (readonly [string, string])[] = [
  ["an import", 'import { showText } from "../api";'],
  ["an alias", 'import { showText as door } from "../api";'],
  ["an import through the index's own file", 'import { showText } from "../api/index";'],
  ["a namespace import", 'import * as api from "../api";'],
  ["a re-export", 'export { showText } from "../api";'],
  ["a renamed re-export", 'export { showText as door } from "../api";'],
  ["an export all", 'export * from "../api";'],
  ["an export all as a namespace", 'export * as api from "../api";'],
  ["a type import", 'import type { showText } from "../api";\nexport type F = typeof showText;'],
  ["a deep path", 'import { showText } from "../api/text";'],
  ["a deep path with the extension", 'import { showText } from "../api/text.tsx";'],
  ["a deep path through another directory", 'import { showText } from "../layout/../api/text";'],
  ["a dynamic import", 'export const f = () => import("../api");'],
  ["an import type", 'export type F = typeof import("../api").showText;'],
  ["an import-equals", 'import api = require("../api");'],
  // The lint allows both as types and no door; the scan, which resolves symbols, counts them (reach.ts's header).
  ["an import type of the whole index (a type: the lint allows it)", 'export type A = typeof import("../api");'],
  ["an indexed access of an import type (a type: the lint allows it)", 'export type A = (typeof import("../api"))["showText"];'],
];

describe("the scan's own controls: each spelling reaches, from a screen", () => {
  const probes = [
    ...FORM_SPELLINGS.map(([name, body]) => ["form", name, body] as const),
    ...SHOW_SPELLINGS.map(([name, body]) => ["door", name, body] as const),
  ].map(([kind, name, body], index) => ({ kind, name, body, file: `src/catalogue/zz-${kind}-${String(index)}.tsx` }));
  const checked = program({
    ...Object.fromEntries(probes.map(({ file, body }) => [file, body])),
    "src/catalogue/zz-control.tsx": 'import { idOf } from "../api";\nimport { Text } from "../layout/Text";\nimport { keyOf } from "../layout/key";\nexport const f = [idOf, Text, keyOf];',
  });

  it.each(probes.map(({ kind, name, file }) => [`${kind}: ${name}`, kind, file] as const))("sees %s", (_name, kind, file) => {
    expect(reachedBy(checked, file, [kind === "form" ? FORM_TARGET : SHOW_TARGET]).length).toBeGreaterThan(0);
  });

  it("sees nothing in imports of the layout's other modules and of the index's other names (a control)", () => {
    expect(reachedBy(checked, "src/catalogue/zz-control.tsx", [FORM_TARGET, SHOW_TARGET])).toEqual([]);
  });
});

describe("a layout module's re-export does not let a screen reach the symbol past the scan (m12)", () => {
  const REEXPORT = `${text("src/layout/Text.tsx")}\nexport { FORM as Form2 } from "./form";\nexport { FORM as default } from "./form";\nexport { showText as door } from "../api";\n`;
  const checked = program(
    {
      "src/catalogue/zz-via-form.tsx": 'import { Form2 } from "../layout/Text";\nexport const f = Form2;',
      "src/catalogue/zz-via-door.tsx": 'import { door } from "../layout/Text";\nexport const f = door;',
      "src/catalogue/zz-via-namespace.tsx": 'import * as text from "../layout/Text";\nexport const f = text;',
      "src/catalogue/zz-via-default.tsx": 'import context from "../layout/Text";\nexport const f = context;',
      "src/catalogue/zz-via-text.tsx": 'import { Text } from "../layout/Text";\nexport const f = Text;',
    },
    { "src/layout/Text.tsx": REEXPORT },
  );

  it("is reached by the screen that imports it, by name, by default or by namespace, and not by the one that imports Text", () => {
    const files = ["src/catalogue/zz-via-form.tsx", "src/catalogue/zz-via-default.tsx", "src/catalogue/zz-via-door.tsx", "src/catalogue/zz-via-namespace.tsx", "src/catalogue/zz-via-text.tsx"];
    expect(reaching(checked, files, FORM_TARGET)).toEqual(["src/catalogue/zz-via-default.tsx", "src/catalogue/zz-via-form.tsx", "src/catalogue/zz-via-namespace.tsx"]);
    expect(reaching(checked, files, SHOW_TARGET)).toEqual(["src/catalogue/zz-via-door.tsx", "src/catalogue/zz-via-namespace.tsx"]);
  });

  it("is offered by the layout module that re-exports it, which the pinned list refuses", () => {
    expect(offeredBy(checked, REAL, [FORM_TARGET])).toEqual(["src/layout/Text.tsx: Form2", "src/layout/Text.tsx: default", "src/layout/form.ts: FORM", "src/layout/form.ts: Form"]);
    expect(offeredBy(checked, REAL, [SHOW_TARGET])).toContain("src/layout/Text.tsx: door");
  });
});

const PARSER = {
  languageOptions: {
    parserOptions: {
      projectService: {
        allowDefaultProject: ["src/*.tsx", "src/catalogue/probe.tsx", "src/layout/probe.tsx", "src/operator/probe.tsx", "src/harness/probe.tsx", "src/router/probe.tsx", "src/api/probe.ts"],
        defaultProject: "tsconfig.app.json",
        maximumDefaultProjectFileMatchCount_THIS_WILL_SLOW_DOWN_LINTING: 16,
      },
    },
  },
};

const eslint = new Linter({ cwd: WEB, overrideConfig: PARSER });
const POLICY = new Set(["no-restricted-syntax", "@typescript-eslint/no-restricted-imports"]);

/** The policy rules that flag a text as the file at `file`. */
async function ids(code: string, file: string): Promise<string[]> {
  const [result] = await eslint.lintText(code, { filePath: path.join(WEB, file) });
  const messages = result?.messages ?? [];
  expect(messages.filter((message) => message.fatal === true).map((message) => message.message)).toEqual([]);
  return messages.flatMap((message) => (message.ruleId !== null && POLICY.has(message.ruleId) ? [message.ruleId] : []));
}

/** The relative path from each directory to the layout's form. */
const FROM: readonly (readonly [string, string])[] = [
  ["src/probe.tsx", "./layout/form"],
  ["src/catalogue/probe.tsx", "../layout/form"],
  ["src/operator/probe.tsx", "../layout/form"],
  ["src/harness/probe.tsx", "../layout/form"],
  ["src/router/probe.tsx", "../layout/form"],
  ["src/api/probe.ts", "../layout/form"],
  ["src/api/curator.ts", "../layout/form"],
];

describe("the lint refuses the visible form's context outside src/layout/ and src/api/text.tsx", () => {
  const FORMS: readonly (readonly [string, (specifier: string) => string])[] = [
    ["an import", (specifier) => `import { FORM } from "${specifier}";\nexport const f = FORM;\n`],
    ["an import with its extension", (specifier) => `import { FORM } from "${specifier}.ts";\nexport const f = FORM;\n`],
    ["an import with another case", (specifier) => `import { FORM } from "${specifier.replace("layout/form", "Layout/FORM")}";\nexport const f = FORM;\n`],
    ["a type import", (specifier) => `import type { Form } from "${specifier}";\nexport type F = Form;\n`],
    ["a re-export", (specifier) => `export { FORM } from "${specifier}";\n`],
    ["a dynamic import", (specifier) => `export const f = () => import("${specifier}");\n`],
    ["an import type", (specifier) => `export type F = typeof import("${specifier}");\n`],
  ];

  it.each(FROM.flatMap(([file, specifier]) => FORMS.map(([form, text]) => [`${file}: ${form}`, file, text(specifier)] as const)))("refuses %s", async (_case, file, text) => {
    expect((await ids(text, file)).length).toBeGreaterThan(0);
  });

  it("refuses the screen that wraps Text in a flat form (round 1's probe)", async () => {
    const text = [
      'import type { ReactElement } from "react";',
      'import type { ServerText } from "../api";',
      'import { FORM } from "../layout/form";',
      'import { Text } from "../layout/Text";',
      "export function Flat({ value }: { readonly value: ServerText }): ReactElement {",
      '  return <FORM value="flat"><Text value={value} /></FORM>;',
      "}",
      "",
    ].join("\n");
    expect(await ids(text, "src/catalogue/probe.tsx")).toContain("@typescript-eslint/no-restricted-imports");
  });

  it("accepts src/layout/'s own import, in any of its spellings, and src/api/text.tsx's, as it is", async () => {
    expect(await ids('import { FORM } from "./form";\nexport const f = FORM;\n', "src/layout/probe.tsx")).toEqual([]);
    expect(await ids('import { FORM } from "./form.ts";\nexport const f = FORM;\n', "src/layout/probe.tsx")).toEqual([]);
    expect(await ids('export const f = () => import("./form");\nexport type F = typeof import("./form");\n', "src/layout/probe.tsx")).toEqual([]);
    expect(await ids(readFileSync(path.join(WEB, "src/api/text.tsx"), "utf8"), "src/api/text.tsx")).toEqual([]);
  });

  it("holds the layout to the rest of the app's rules (a number rule still refuses there)", async () => {
    expect((await ids("export const f = (t: string) => Number.parseInt(t);\n", "src/layout/probe.tsx")).length).toBeGreaterThan(0);
  });
});

/** The index, as each directory spells it. */
const API_FROM: readonly (readonly [string, string])[] = [
  ["src/probe.tsx", "./api"],
  ["src/catalogue/probe.tsx", "../api"],
  ["src/operator/probe.tsx", "../api"],
  ["src/harness/probe.tsx", "../api"],
  ["src/router/probe.tsx", "../api"],
];

describe("the lint refuses the door outside src/layout/ (the index's pinned export that the layout alone imports)", () => {
  const DOORS: readonly (readonly [string, (specifier: string) => string])[] = [
    ["an import", (specifier) => `import { showText } from "${specifier}";\nexport const f = showText;\n`],
    ["an alias", (specifier) => `import { showText as door } from "${specifier}";\nexport const f = door;\n`],
    ["an import through the index's file", (specifier) => `import { showText } from "${specifier}/index";\nexport const f = showText;\n`],
    ["an import through the index's file with its extension", (specifier) => `import { showText } from "${specifier}/index.ts";\nexport const f = showText;\n`],
    ["a namespace import", (specifier) => `import * as api from "${specifier}";\nexport const f = api;\n`],
    ["a type import", (specifier) => `import type { showText } from "${specifier}";\nexport type F = typeof showText;\n`],
    ["a re-export", (specifier) => `export { showText } from "${specifier}";\n`],
    ["a renamed re-export", (specifier) => `export { showText as door } from "${specifier}";\n`],
    ["an export all", (specifier) => `export * from "${specifier}";\n`],
    ["a dynamic import", (specifier) => `export const f = () => import("${specifier}");\n`],
    ["an import type that names it", (specifier) => `export type F = typeof import("${specifier}").showText;\n`],
    ["a deep path", (specifier) => `import { showText } from "${specifier}/text";\nexport const f = showText;\n`],
  ];

  it.each(API_FROM.flatMap(([file, specifier]) => DOORS.map(([door, code]) => [`${file}: ${door}`, file, code(specifier)] as const)))("refuses %s", async (_case, file, code) => {
    expect((await ids(code, file)).length).toBeGreaterThan(0);
  });

  it("refuses a module of src/api/ that imports it from ./text, and accepts the index's own re-export, as it is", async () => {
    expect((await ids('import { showText } from "./text";\nexport const f = showText;\n', "src/api/curator.ts")).length).toBeGreaterThan(0);
    expect(await ids(readFileSync(path.join(WEB, "src/api/index.ts"), "utf8"), "src/api/index.ts")).toEqual([]);
  });

  const SIBLING: readonly (readonly [string, (specifier: string) => string])[] = [
    ["an import", (specifier) => `import { showText } from "${specifier}";\nexport const f = showText;\n`],
    ["an alias", (specifier) => `import { showText as door } from "${specifier}";\nexport const f = door;\n`],
    ["a namespace import", (specifier) => `import * as api from "${specifier}";\nexport const f = api;\n`],
    ["a type import", (specifier) => `import type { showText } from "${specifier}";\nexport type F = typeof showText;\n`],
    ["a re-export", (specifier) => `export { showText } from "${specifier}";\n`],
    ["an export all", (specifier) => `export * from "${specifier}";\n`],
  ];

  // m4 of round 3 of #117: `import { showText } from "./index"` (or `"."`) in a module of src/api/ passed the lint.
  it.each(
    ["src/api/probe.ts", "src/api/curator.ts"].flatMap((file) =>
      [".", "./", "./index", "./index.ts", "./index.js"].flatMap((specifier) => SIBLING.map(([spelling, code]) => [`${file}: ${spelling} of "${specifier}"`, file, code(specifier)] as const)),
    ),
  )("refuses %s", async (_case, file, code) => {
    expect((await ids(code, file)).length).toBeGreaterThan(0);
  });

  it("accepts the index's other names from a sibling, a sibling's own modules, and the index's own re-export (the controls)", async () => {
    expect(await ids('import { idOf } from ".";\nexport const f = idOf;\n', "src/api/probe.ts")).toEqual([]);
    expect(await ids('import { isRequestIdText } from "./ids";\nexport const f = isRequestIdText;\n', "src/api/probe.ts")).toEqual([]);
    expect(await ids('export { showText } from "./text";\n', "src/api/index.ts")).toEqual([]);
  });

  it("accepts the layout's own import of it, in each spelling, and the index's other names anywhere, and a type of the whole index (not a door)", async () => {
    expect(await ids('export type C = typeof import("../api");\n', "src/catalogue/probe.tsx")).toEqual([]);
    expect(await ids('import { showText } from "../api";\nexport const f = showText;\n', "src/layout/probe.tsx")).toEqual([]);
    expect(await ids('import * as api from "../api";\nexport const f = () => import("../api");\nexport type F = typeof import("../api").showText;\nexport const g = api;\n', "src/layout/probe.tsx")).toEqual([]);
    expect(await ids('import { idOf, type ServerText } from "../api";\nexport const f = [idOf];\nexport type T = ServerText;\n', "src/catalogue/probe.tsx")).toEqual([]);
  });
});
