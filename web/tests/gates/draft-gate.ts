/**
 * The document seam's surface gate, by identity (D432; MA1 of the seam's plan round 2, minor 1 of
 * round 3): what a screen can hand `document.ts`, held over the TypeScript program by the types
 * themselves, never by a printed text (a unique symbol's printed name holds an id of TypeScript's
 * process-global counter, and a tuple's printed text drops its flags: round 2's S2).
 *
 * **The roots** are every value export of `document.ts`, read from the checker's module symbol (no
 * list by hand). Each must be a function of **one call signature, one parameter, no type
 * parameter, no `this`**, no construct signature and no member (an overload, a second parameter,
 * optional or rest, a generic, an exported class or constant is a problem at the root). Their
 * **parameters alone** are walked (strict mode): the outputs are the walk's answer types, held by
 * `takers.ts` and `api-graph.test.ts`.
 *
 * - **`optionsOf`'s** parameter is pinned by **type-object identity**: it is the very type of
 *   `typed.ts`' `describeDataset`'s ok value (`Answered<"describe_dataset">`, by the checker's own
 *   instantiation), never an alias symbol, which the checker does not keep on a parameter.
 * - **Each `*Draft`'s** parameter is walked structurally into a `(path -> kind)` table, compared
 *   exactly with the checked-in `DRAFT_TABLE` (`draft-table.ts`):
 *   - a leaf is one of three brands, each resolved through the checker by its declaring file and
 *     name and compared by identity: `Typed` (`src/layout/Field.tsx`), `DatasetId`
 *     (`src/api/text.tsx`), and `Choice<R>`, a reference to the class declared in `text.tsx` whose
 *     one type argument is a single string literal of the roles;
 *   - a list is the default library's `ReadonlyArray` (a mutable `Array` is a problem);
 *   - a tuple is `readonly`, its element flags read from its target (`Required` and `Rest`; an
 *     `Optional` or a `Variadic` flag is a problem) and printed with its arity;
 *   - a union is each of its members, walked (`|i`, in the order of their kinds, not the checker's
 *     ids);
 *   - an object is its exact member-name set (a unique-symbol key named by its declaration), every
 *     member a `readonly` property and required, with no index, call or construct signature;
 *   - anything else (a primitive, `unknown`, `any`, `never`, an intersection that is no brand, a
 *     mapped or conditional type, a class instance, a library type) is a problem at its path.
 */
import path from "node:path";

import ts from "typescript";

import { WEB } from "./program";
import { declarationOf, memberName } from "./surface";

/** What the gate found: the problems, the `(path -> kind)` table and the roots, each sorted. */
export interface DraftWalk {
  readonly problems: readonly string[];
  readonly table: readonly string[];
  readonly roots: readonly string[];
}

/** The roles a `Choice` may be of. */
const ROLES = ["unit", "leaf", "view"];

/** The roots that take a `Draft`, walked against the table. */
export const DRAFT_ROOTS = ["countDraft", "runDraft", "validateDraft"] as const;

/** The root pinned by identity to `describeDataset`'s ok value. */
export const OPTIONS_ROOT = "optionsOf";

/** An export of a module of the program, its alias resolved. */
function exportOf(program: ts.Program, checker: ts.TypeChecker, file: string, name: string): ts.Symbol {
  const source = program.getSourceFile(path.join(WEB, file));
  const module = source === undefined ? undefined : checker.getSymbolAtLocation(source);
  const found = module === undefined ? undefined : checker.getExportsOfModule(module).find((member) => member.getName() === name);
  if (found === undefined) {
    throw new Error(`${file} exports no ${name}`);
  }
  return (found.flags & ts.SymbolFlags.Alias) !== 0 ? checker.getAliasedSymbol(found) : found;
}

/** The type of an `Outcome`'s `ok` arm's `value`, from a typed function's return type
 * (`Promise<Outcome<T>>`). */
function okValue(checker: ts.TypeChecker, returned: ts.Type): ts.Type | undefined {
  const [outcome] = checker.getTypeArguments(returned as ts.TypeReference);
  const arms = outcome === undefined ? [] : outcome.isUnion() ? outcome.types : [outcome];
  for (const arm of arms) {
    const kind = checker.getPropertyOfType(arm, "kind");
    const value = checker.getPropertyOfType(arm, "value");
    const literal = kind === undefined ? undefined : checker.getTypeOfSymbol(kind);
    if (value !== undefined && literal?.isStringLiteral() === true && literal.value === "ok") {
      return checker.getTypeOfSymbol(value);
    }
  }
  return undefined;
}

/** The brands and the pinned type, resolved by their declaring file and name. */
interface Brands {
  readonly typed: ts.Type;
  readonly dataset: ts.Type;
  readonly choice: ts.Type;
  readonly described: ts.Type | undefined;
}

function brandsOf(program: ts.Program, checker: ts.TypeChecker): Brands {
  const [describe] = checker.getTypeOfSymbol(exportOf(program, checker, "src/api/typed.ts", "describeDataset")).getCallSignatures();
  return {
    typed: checker.getDeclaredTypeOfSymbol(exportOf(program, checker, "src/layout/Field.tsx", "Typed")),
    dataset: checker.getDeclaredTypeOfSymbol(exportOf(program, checker, "src/api/text.tsx", "DatasetId")),
    choice: checker.getDeclaredTypeOfSymbol(exportOf(program, checker, "src/api/text.tsx", "Choice")),
    described: describe === undefined ? undefined : okValue(checker, describe.getReturnType()),
  };
}

/** Whether every declaration of a symbol is the default library's or a package's. */
function library(program: ts.Program, symbol: ts.Symbol | undefined): boolean {
  const declarations = symbol?.declarations ?? [];
  return declarations.length > 0 && declarations.every((declaration) => program.isSourceFileDefaultLibrary(declaration.getSourceFile()) || declaration.getSourceFile().fileName.includes("/node_modules/"));
}

/** Whether a member is a property declared `readonly` (every declaration of it). */
function readonlyProperty(member: ts.Symbol): boolean {
  const declarations = member.declarations ?? [];
  return declarations.length > 0 && declarations.every((declaration) => (ts.isPropertySignature(declaration) || ts.isPropertyDeclaration(declaration)) && (ts.getCombinedModifierFlags(declaration) & ts.ModifierFlags.Readonly) !== 0);
}

/** A tuple's flags, one letter an element: `R` required, `S` rest, `O` optional, `V` variadic. */
function flagsOf(target: ts.TupleType): string[] {
  return target.elementFlags.map((flag) => ((flag & ts.ElementFlags.Required) !== 0 ? "R" : (flag & ts.ElementFlags.Rest) !== 0 ? "S" : (flag & ts.ElementFlags.Optional) !== 0 ? "O" : "V"));
}

/** One step of the walk: a type's kind, its problems and its children (a path suffix and a type). */
interface Step {
  readonly kind: string;
  readonly problems: readonly string[];
  readonly children: readonly (readonly [string, ts.Type])[];
}

/** The walk (the module's docstring) over a program, of `file` (`document.ts`; the brute force
 * hands it copies of it). */
export function draftGate(program: ts.Program, checker: ts.TypeChecker, file = "src/api/document.ts"): DraftWalk {
  const brands = brandsOf(program, checker);
  const problems: string[] = [];
  const table: string[] = [];
  const text = (type: ts.Type): string => checker.typeToString(type, undefined, ts.TypeFormatFlags.NoTruncation);

  const step = (type: ts.Type): Step => {
    if (type === brands.typed) {
      return { kind: "Typed", problems: [], children: [] };
    }
    if (type === brands.dataset) {
      return { kind: "DatasetId", problems: [], children: [] };
    }
    if (type.isUnion()) {
      return { kind: `union of ${String(type.types.length)}`, problems: [], children: type.types.map((member) => ["|", member] as const) };
    }
    if ((type.flags & ts.TypeFlags.Object) === 0) {
      return { kind: `not a brand: ${text(type)}`, problems: [`not one of the three brands or a structure: ${text(type)}`], children: [] };
    }
    const objectFlags = (type as ts.ObjectType).objectFlags;
    const reference = (objectFlags & ts.ObjectFlags.Reference) !== 0 ? (type as ts.TypeReference) : undefined;
    if (reference !== undefined && reference.target === brands.choice) {
      const parameters = checker.getTypeArguments(reference);
      const [role] = parameters;
      if (parameters.length === 1 && role?.isStringLiteral() === true && ROLES.includes(role.value)) {
        return { kind: `Choice<${role.value}>`, problems: [], children: [] };
      }
      return { kind: `Choice of ${text(type)}`, problems: [`a Choice whose role is not one string literal of the roles: ${text(type)}`], children: [] };
    }
    if (reference !== undefined && checker.isTupleType(type)) {
      const target = reference.target as ts.TupleType;
      const flags = flagsOf(target);
      const found: string[] = [];
      if (!target.readonly) {
        found.push("a mutable tuple");
      }
      if (flags.some((flag) => flag === "O" || flag === "V")) {
        found.push(`a tuple with an optional or variadic element: [${flags.join(",")}]`);
      }
      return {
        kind: `${target.readonly ? "readonly" : "MUTABLE"} tuple [${flags.join(",")}]`,
        problems: found,
        children: checker.getTypeArguments(reference).map((element, index) => [`[${String(index)}]`, element] as const),
      };
    }
    if (reference !== undefined && library(program, reference.target.getSymbol())) {
      const name = reference.target.getSymbol()?.getName();
      const [element] = checker.getTypeArguments(reference);
      if (name === "ReadonlyArray" && element !== undefined) {
        return { kind: "readonly list", problems: [], children: [["[]", element]] };
      }
      return { kind: `library ${name ?? "?"}`, problems: [`a library type (${name === "Array" ? "a mutable list" : text(type)})`], children: [] };
    }
    if (library(program, type.getSymbol()) || library(program, type.aliasSymbol)) {
      return { kind: `library ${text(type)}`, problems: [`a library type: ${text(type)}`], children: [] };
    }
    if ((objectFlags & ts.ObjectFlags.Mapped) !== 0) {
      return { kind: `mapped ${text(type)}`, problems: [`a mapped type: ${text(type)}`], children: [] };
    }
    if ((objectFlags & ts.ObjectFlags.Class) !== 0 || (reference !== undefined && (reference.target.objectFlags & ts.ObjectFlags.Class) !== 0)) {
      return { kind: `class ${text(type)}`, problems: [`a class instance that is no Choice: ${text(type)}`], children: [] };
    }
    const found: string[] = [];
    if (checker.getIndexInfosOfType(type).length > 0) {
      found.push("an index signature");
    }
    if (type.getCallSignatures().length > 0 || type.getConstructSignatures().length > 0) {
      found.push("a call or construct signature");
    }
    const members = checker.getPropertiesOfType(type).map((member) => [memberName(program, checker, member), member] as const);
    for (const [name, member] of members) {
      if ((member.flags & ts.SymbolFlags.Optional) !== 0) {
        found.push(`.${name}: optional`);
      }
      if (!readonlyProperty(member)) {
        found.push(`.${name}: not a readonly property`);
      }
    }
    return {
      kind: `{${members.map(([name]) => name).sort().join(",")}}`,
      problems: found,
      children: members.map(([name, member]) => [`.${name}`, checker.getTypeOfSymbol(member)] as const),
    };
  };

  /** A type's own kind, for ordering a union's members (never the checker's ids). */
  const kindOf = (type: ts.Type): string => step(type).kind;

  const walk = (type: ts.Type, at: string, ancestors: readonly ts.Type[]): void => {
    if (ancestors.includes(type) || ancestors.length > 16) {
      problems.push(`${at}: a type that recurs or nests deeper than a draft does`);
      return;
    }
    const found = step(type);
    table.push(`${at} -> ${found.kind}`);
    for (const problem of found.problems) {
      problems.push(`${at}: ${problem}`);
    }
    const seen = [...ancestors, type];
    if (type.isUnion()) {
      const arms = [...type.types].map((member) => [kindOf(member), member] as const).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
      arms.forEach(([, member], index) => {
        walk(member, `${at}|${String(index)}`, seen);
      });
      return;
    }
    for (const [suffix, child] of found.children) {
      walk(child, `${at}${suffix}`, seen);
    }
  };

  const source = program.getSourceFile(path.join(WEB, file));
  const module = source === undefined ? undefined : checker.getSymbolAtLocation(source);
  if (module === undefined) {
    throw new Error(`no module ${file}`);
  }
  const roots: string[] = [];
  for (const exported of checker.getExportsOfModule(module)) {
    const symbol = (exported.flags & ts.SymbolFlags.Alias) !== 0 ? checker.getAliasedSymbol(exported) : exported;
    if ((symbol.flags & ts.SymbolFlags.Value) === 0) {
      continue;
    }
    const name = exported.getName();
    roots.push(name);
    const type = checker.getTypeOfSymbol(symbol);
    const signatures = type.getCallSignatures();
    const constructs = type.getConstructSignatures().length;
    const members = checker.getPropertiesOfType(type).length;
    if (signatures.length !== 1 || constructs > 0 || members > 0) {
      problems.push(`${name}: ${String(signatures.length)} call signatures, ${String(constructs)} construct signatures, ${String(members)} members (a root is a function of one call signature)`);
    }
    const pinned = name === OPTIONS_ROOT || (DRAFT_ROOTS as readonly string[]).includes(name);
    if (!pinned) {
      problems.push(`${name}: a root no pin names`);
    }
    signatures.forEach((signature, index) => {
      const at = signatures.length === 1 ? name : `${name}[${String(index)}]`;
      if ((signature.typeParameters?.length ?? 0) > 0) {
        problems.push(`${at}: a generic signature`);
      }
      if (signature.thisParameter !== undefined) {
        problems.push(`${at}: a this parameter`);
      }
      const parameters = signature.getParameters();
      table.push(`${at} -> ${String(parameters.length)} parameters`);
      if (parameters.length !== 1) {
        problems.push(`${at}: ${String(parameters.length)} parameters (a root takes one)`);
      }
      parameters.forEach((parameter, position) => {
        const place = `${at}(#${String(position)})`;
        const declaration = parameter.valueDeclaration;
        if (declaration !== undefined && ts.isParameter(declaration) && (declaration.questionToken !== undefined || declaration.initializer !== undefined || declaration.dotDotDotToken !== undefined)) {
          problems.push(`${place}: an optional or rest parameter`);
        }
        const taken = checker.getTypeOfSymbol(parameter);
        if (name === OPTIONS_ROOT) {
          const same = brands.described !== undefined && taken === brands.described;
          table.push(`${place} -> ${same ? "describeDataset's ok value" : `NOT describeDataset's ok value: ${text(taken).slice(0, 120)}`}`);
          if (!same) {
            problems.push(`${place}: not the very type of describeDataset's ok value`);
          }
          return;
        }
        walk(taken, place, []);
      });
    });
  }
  return { problems: [...problems].sort(), table: [...table].sort(), roots: roots.sort() };
}

/** The declaration of a brand, as the gate resolves it (for the gate's own cells). */
export function brandDeclarations(program: ts.Program, checker: ts.TypeChecker): readonly string[] {
  return [
    declarationOf(program, exportOf(program, checker, "src/layout/Field.tsx", "Typed")),
    declarationOf(program, exportOf(program, checker, "src/api/text.tsx", "DatasetId")),
    declarationOf(program, exportOf(program, checker, "src/api/text.tsx", "Choice")),
  ];
}
