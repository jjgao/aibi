/**
 * The brands of D428 and their one mint each: `DatasetId` (`src/api/text.tsx`'s grammar: `idOf`
 * and `idOfAddress`), `ColumnId` (the same module's, D443: `columnOf` and `columnOfAddress`), `Typed`
 * (`src/layout/Field.tsx`: what the person typed) and `Key` (`src/layout/key.ts`: an index), read
 * from the display lint's own list (`BRANDS` of `scripts/lint/display.mjs`, D429), so that a brand
 * the lint lets reach the DOM is a brand whose mint is checked here, and the two lists cannot drift.
 * Over the app's program, the files that mint one, by any of the
 * ways the checker takes on trust (a type assertion, a type predicate, an overload signature whose
 * implementation gives a wider type, an ambient declaration), are exactly the brand's module: a
 * second mint by one of those ways fails here, whatever its name (the lint refuses an assertion and
 * a predicate outside the named files; an overload or a `declare` it does not see). **A residual,
 * named in D428 (round 1 of #117's `probeMint.ts`):** TypeScript's unsound array covariance (a
 * `Typed[]` read as a `string[]`, then written through) and a method's bivariance mint a brand with
 * none of these ways; they are deliberate code (D419's boundary), and the last describe holds that
 * this check does not see them. And what the brands are for, held
 * by the checker: a `Choice` is no `<select>` or `<option>` value, an `<option>`'s value is a
 * `Key`, an `<input>`'s a `Typed`, a `Link`'s target a `DatasetId`, a `ColumnLink`'s a `DatasetId`
 * and a `ColumnId` (neither taking the other), and a box none of them.
 */
import path from "node:path";

import ts from "typescript";
import { beforeAll, describe, expect, it } from "vitest";

import { BRANDS as LINT_BRANDS } from "../../scripts/lint/display.mjs";
import { isTooling } from "../../scripts/tooling.mjs";
import { appOptions, check, WEB } from "./program";

/** Each brand, the module that declares and mints it: the display lint's list (D429), whole. */
const BRANDS: readonly { readonly name: string; readonly file: string }[] = LINT_BRANDS.map(({ type, file }) => ({ name: type, file }));

/** A brand of the list, by its name. */
function brandNamed(name: string): { readonly name: string; readonly file: string } {
  const found = BRANDS.find((one) => one.name === name);
  if (found === undefined) {
    throw new Error(`no brand ${name}`);
  }
  return found;
}

/** The brand's type, from its module. */
function brandOf(program: ts.Program, checker: ts.TypeChecker, file: string, name: string): ts.Type {
  const source = program.getSourceFile(path.join(WEB, file));
  const module = source === undefined ? undefined : checker.getSymbolAtLocation(source);
  const symbol = module === undefined ? undefined : checker.getExportsOfModule(module).find((member) => member.name === name);
  if (symbol === undefined) {
    throw new Error(`${file} exports no ${name}`);
  }
  return checker.getDeclaredTypeOfSymbol(symbol);
}

/** Whether a type gives the brand: a member of it (but `null`, `undefined`, `any` and `never`) is
 * assignable to the brand. */
function gives(checker: ts.TypeChecker, type: ts.Type, brand: ts.Type): boolean {
  const members = type.isUnion() ? type.types : [type];
  return members.some((member) => (member.flags & (ts.TypeFlags.Any | ts.TypeFlags.Never | ts.TypeFlags.Null | ts.TypeFlags.Undefined)) === 0 && checker.isTypeAssignableTo(member, brand));
}

/** Whether a declaration is ambient (`declare`, or in a `.d.ts`). */
function ambient(node: ts.Node): boolean {
  let at = node;
  while (!ts.isSourceFile(at)) {
    if (ts.canHaveModifiers(at) && (ts.getModifiers(at) ?? []).some((modifier) => modifier.kind === ts.SyntaxKind.DeclareKeyword)) {
      return true;
    }
    at = at.parent;
  }
  return at.isDeclarationFile;
}

/** The files of the app (no tooling, no package) that mint the brand. */
function minters(program: ts.Program, checker: ts.TypeChecker, brand: ts.Type): string[] {
  const found = new Set<string>();
  for (const source of program.getSourceFiles()) {
    const file = path.relative(WEB, source.fileName);
    if (file.startsWith("..") || file.startsWith("node_modules/") || isTooling(file)) {
      continue;
    }
    const visit = (node: ts.Node): void => {
      const mint =
        // `x as Brand` and `<Brand>x` (not `as const`).
        ((ts.isAsExpression(node) || ts.isTypeAssertionExpression(node)) && !(ts.isTypeReferenceNode(node.type) && node.type.getText(source) === "const") && gives(checker, checker.getTypeAtLocation(node), brand)) ||
        // `x is Brand`.
        (ts.isTypePredicateNode(node) && node.type !== undefined && gives(checker, checker.getTypeFromTypeNode(node.type), brand)) ||
        // An overload signature, or a declared function, method or value.
        ((ts.isFunctionDeclaration(node) || ts.isMethodDeclaration(node)) && node.body === undefined && (() => {
          const signature = checker.getSignatureFromDeclaration(node);
          return signature !== undefined && gives(checker, signature.getReturnType(), brand);
        })()) ||
        (ts.isVariableDeclaration(node) && ambient(node) && gives(checker, checker.getTypeAtLocation(node), brand));
      if (mint) {
        found.add(file);
      }
      ts.forEachChild(node, visit);
    };
    visit(source);
  }
  return [...found].sort();
}

let program: ts.Program;
let checker: ts.TypeChecker;

beforeAll(() => {
  const root = path.join(WEB, "src");
  const files = ts.sys.readDirectory(root, [".ts", ".tsx"]);
  program = ts.createProgram(files, appOptions());
  checker = program.getTypeChecker();
}, 120_000);

describe("each brand has one mint", () => {
  it.each(BRANDS)("$name is minted in $file alone", ({ name, file }) => {
    expect(minters(program, checker, brandOf(program, checker, file, name))).toEqual([file]);
  });

  const MINTS: readonly (readonly [string, string, string])[] = [
    ["an overload whose implementation gives a string", "Typed", "import type { Typed } from './layout/Field';\nexport function mint(raw: string): Typed;\nexport function mint(raw: string): string {\n  return raw;\n}\n"],
    ["a method overload", "DatasetId", "import type { DatasetId } from './api';\nexport class M {\n  mint(raw: string): DatasetId;\n  mint(raw: string): string {\n    return raw;\n  }\n}\n"],
    ["a declared function", "Key", "import type { Key } from './layout/key';\ndeclare function mint(index: number): Key;\nexport const k = mint(1);\n"],
    ["a declared value", "Typed", "import type { Typed } from './layout/Field';\ndeclare const typed: Typed;\nexport const t = typed;\n"],
    ["an assertion", "Key", "import type { Key } from './layout/key';\nexport const k = 1 as Key;\n"],
    ["a predicate", "DatasetId", "import type { DatasetId } from './api';\nexport function is(raw: string): raw is DatasetId {\n  return raw.length > 0;\n}\n"],
    ["a nullable overload", "DatasetId", "import type { DatasetId } from './api';\nexport function mint(raw: string): DatasetId | null;\nexport function mint(raw: string): string | null {\n  return raw;\n}\n"],
    ["a nullable overload of a ColumnId (round 1 of the column screen's plan: it passed tsc, the lint and this file while the list was hand-kept)", "ColumnId", "import type { ColumnId } from './api';\nexport function mint(raw: string | null): ColumnId | null;\nexport function mint(raw: string | null): string | null {\n  return raw;\n}\n"],
    ["a predicate of a ColumnId", "ColumnId", "import type { ColumnId } from './api';\nexport function is(raw: string): raw is ColumnId {\n  return raw.includes('.');\n}\n"],
    ["an assertion to a ColumnId", "ColumnId", "import type { ColumnId } from './api';\nexport const c = 'trees.variety' as ColumnId;\n"],
  ];

  it("checks every brand the display lint lets reach the DOM, by its name and module", () => {
    expect(BRANDS.map(({ name }) => name).sort()).toEqual(["ColumnId", "DatasetId", "Key", "Typed"]);
    expect(brandNamed("ColumnId").file).toBe("src/api/text.tsx");
  });

  it.each(MINTS)("sees a second mint: %s (the check's own controls)", (_case, name, text) => {
    const brand = brandNamed(name);
    const checked = check({ "src/probe-mint.tsx": text });
    expect(minters(checked.program, checked.checker, brandOf(checked.program, checked.checker, brand.file, name))).toEqual([brand.file, "src/probe-mint.tsx"].sort());
  });
});

describe("what the brands are for, held by the checker", () => {
  const PROBES: readonly (readonly [string, string, number[]])[] = [
    ["a Choice as a select's value", "<select value={choice} onChange={() => undefined} />", [2322]],
    ["a Choice as an option's value", "<option value={choice} />", [2322]],
    ["a box as an option's value", "<option value={box} />", [2322]],
    ["a box as an input's value", "<input value={box} onChange={() => undefined} />", [2322]],
    ["a number as an Option's index", "<Option label={box} name={null} at={0} />", [2322]],
    ["a string as an Option's name", "<Option label={box} name={'x'} at={keyOf(0)} />", [2322]],
    ["a string as a Field's value", "<Field value={'x'} onChange={() => undefined}>x</Field>", [2322]],
    ["a string as a Link's target", "<Link to={'orchard'}>x</Link>", [2322]],
    ["a box as a Link's target", "<Link to={box}>x</Link>", [2322]],
    ["a Key as an Option's index", "<Option label={box} name={box} at={keyOf(0)} />", []],
    ["an Option without a name (it is required, null for none)", "<Option label={box} at={keyOf(0)} />", [2741]],
    ["no name for an Option", "<Option label={box} name={null} at={keyOf(0)} />", []],
    ["a Typed as a Field's value", "<Field value={UNTYPED} onChange={() => undefined}>x</Field>", []],
    ["a DatasetId as a Link's target", "id === null ? null : <Link to={id}>x</Link>", []],
    ["a ColumnId as a Link's target", "column === null ? null : <Link to={column}>x</Link>", [2322]],
    ["a string as a ColumnLink's column", "id === null ? null : <ColumnLink dataset={id} column={'trees.variety'}>x</ColumnLink>", [2322]],
    ["a box as a ColumnLink's column", "id === null ? null : <ColumnLink dataset={id} column={box}>x</ColumnLink>", [2322]],
    ["a DatasetId as a ColumnLink's column", "id === null ? null : <ColumnLink dataset={id} column={id}>x</ColumnLink>", [2322]],
    ["a ColumnId as a ColumnLink's dataset", "column === null ? null : <ColumnLink dataset={column} column={column}>x</ColumnLink>", [2322]],
    ["a DatasetId and a ColumnId as a ColumnLink's", "id === null || column === null ? null : <ColumnLink dataset={id} column={column}>x</ColumnLink>", []],
  ];

  it.each(PROBES)("%s", (_case, expression, codes) => {
    const text = [
      "import { type Choice, type ServerText, columnOfAddress, idOfAddress } from './api';",
      "import { Field, UNTYPED } from './layout/Field';",
      "import { keyOf } from './layout/key';",
      "import { ColumnLink, Link } from './layout/Link';",
      "import { Option } from './layout/Option';",
      "declare const choice: Choice<\"unit\">;",
      "declare const box: ServerText;",
      "const id = idOfAddress('orchard');",
      "const column = columnOfAddress('trees.variety');",
      `export const probe = ${expression};`,
      "export const used = [Field, UNTYPED, keyOf, Link, ColumnLink, Option, id, column, choice, box];",
      "",
    ].join("\n");
    expect(check({ "src/probe-brand.tsx": text }).codes("src/probe-brand.tsx")).toEqual(codes);
  });
});

describe("a residual: the brands' unsound spots mint with no assertion, predicate, overload or declare (D428, deliberate code)", () => {
  const COVARIANCE = [
    'import { type Typed, UNTYPED } from "./layout/Field";',
    "export function mintTyped(raw: string): Typed {",
    "  const held: Typed[] = [UNTYPED];",
    "  const wide: string[] = held;",
    "  wide[0] = raw;",
    "  return held[0] ?? UNTYPED;",
    "}",
    "",
  ].join("\n");

  it("array covariance mints a Typed, and the checker accepts it: the mint check does not see it", () => {
    const checked = check({ "src/probe-covariance.ts": COVARIANCE });
    expect(checked.codes("src/probe-covariance.ts")).toEqual([]);
    const brand = brandNamed("Typed");
    expect(minters(checked.program, checked.checker, brandOf(checked.program, checked.checker, brand.file, brand.name))).toEqual([brand.file]);
  });
});
