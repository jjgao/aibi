/**
 * The index's check of the takers (D419, D425), which `api-graph.test.ts` applies to every function
 * `src/api/index.ts` exports and `request-half.test.ts` (b) to each generic of it, instantiated:
 * whether a type can be handed a server-number box or a decoded node that holds one at a slot it
 * declares, decided by the type checker's own assignability and never by a name. It is a deny-check
 * (it lists what takes), kept as defence in depth: the index exports a reviewed list, its routes
 * are generated, and the operator client, whose members a lazy hand adds, is held by an
 * allow-list instead (`surface.ts`), because a deny-check over declared types cannot see a box
 * the caller adds as an extra member of an object (round 2 of #115's review, MA2).
 *
 * The probes are the object-shaped members of `JsonOut` (`json.ts`), as the checker has them: the
 * `ServerNumber` box, a list of nodes and a record of nodes. A type is a taker if it is `unknown`,
 * `any`, `object` or `void` (a function typed `() => void` may give anything), or if any probe is
 * assignable to it (but to a weak type, which a record is assignable to without sharing a member).
 * Beyond that it is looked at through its structure, the default library's as the program's (no
 * library type is cut short, so `AbortSignal`'s `reason: any` and `Error`'s `cause` are takers): a
 * union's members; an intersection's (none, if a primitive is among them: only a primitive
 * inhabits it); a type parameter's, a conditional's, a substitution's and an indexed access's
 * constraint (none, or itself, is a taker); a tuple's, an array's and a generic's type arguments; a
 * mapped type over a type parameter (its members are not known: a taker); every index signature;
 * every property; and every call and construct signature's `this`, parameters and return.
 */
import path from "node:path";

import ts from "typescript";

import type { Shape } from "../../src/api/generated/shapes";
import { WEB } from "./program";

/** An export of a module of the program by its project-relative name. */
function exportOf(program: ts.Program, checker: ts.TypeChecker, file: string, name: string): ts.Symbol {
  const source = program.getSourceFile(path.join(WEB, file));
  const module = source === undefined ? undefined : checker.getSymbolAtLocation(source);
  const found = module === undefined ? undefined : checker.getExportsOfModule(module).find((member) => member.name === name);
  if (found === undefined) {
    throw new Error(`${file} exports no ${name}`);
  }
  return found;
}

/** The probes: each object-shaped member of the declared type `JsonOut`. */
export function probesOf(program: ts.Program, checker: ts.TypeChecker): ts.Type[] {
  const json = checker.getDeclaredTypeOfSymbol(exportOf(program, checker, "src/api/json.ts", "JsonOut"));
  const probes = (json.isUnion() ? json.types : [json]).filter((member) => (member.flags & ts.TypeFlags.Object) !== 0);
  if (probes.length !== 3) {
    throw new Error(`JsonOut has ${String(probes.length)} object-shaped members, not the box, a list and a record`);
  }
  return probes;
}

/** The check: whether a type can be handed a box or a node that holds one. */
export type Takes = (type: ts.Type) => boolean;

/** Whether a type is weak: an object type of properties that are all optional, no index signature
 * and no signature. The record of nodes is assignable to it without sharing a member with it (an
 * index signature is not checked against an optional property), so assignability says nothing:
 * what it declares is looked at instead. */
function weak(checker: ts.TypeChecker, type: ts.Type): boolean {
  const properties = checker.getPropertiesOfType(type);
  return (
    (type.flags & ts.TypeFlags.Object) !== 0 &&
    properties.length > 0 &&
    properties.every((property) => (property.flags & ts.SymbolFlags.Optional) !== 0) &&
    type.getStringIndexType() === undefined &&
    type.getCallSignatures().length === 0 &&
    type.getConstructSignatures().length === 0
  );
}

/** The primitives: no object inhabits an intersection with one. */
const PRIMITIVE =
  ts.TypeFlags.StringLike | ts.TypeFlags.NumberLike | ts.TypeFlags.BooleanLike | ts.TypeFlags.BigIntLike | ts.TypeFlags.ESSymbolLike | ts.TypeFlags.Null | ts.TypeFlags.Undefined;

/** The kinds the walk resolves by their constraint: a conditional, a substitution and an indexed
 * type have no members to look at. */
const CONSTRAINED = ts.TypeFlags.Conditional | ts.TypeFlags.Substitution | ts.TypeFlags.IndexedAccess;

/** The check, over a program and its checker. */
export function takes(program: ts.Program, checker: ts.TypeChecker): Takes {
  const probes = probesOf(program, checker);
  const holds = (type: ts.Type, seen: Set<ts.Type>): boolean => {
    if (seen.has(type)) {
      return false;
    }
    seen.add(type);
    // `void` too: a function typed `() => void` may give anything.
    if ((type.flags & (ts.TypeFlags.Any | ts.TypeFlags.Unknown | ts.TypeFlags.NonPrimitive | ts.TypeFlags.Void)) !== 0) {
      return true;
    }
    if ((type.flags & (ts.TypeFlags.TypeParameter | CONSTRAINED)) !== 0) {
      const constraint = checker.getBaseConstraintOfType(type);
      return constraint === undefined || constraint === type || holds(constraint, seen);
    }
    if (type.isUnion()) {
      return type.types.some((member) => holds(member, seen));
    }
    if (type.isIntersection()) {
      // By its members (the intersection's own are a primitive's apparent ones too); with a
      // primitive among them, only a primitive inhabits it, which holds no box.
      return !type.types.some((member) => (member.flags & PRIMITIVE) !== 0) && type.types.some((member) => holds(member, seen));
    }
    if (!weak(checker, type) && probes.some((probe) => checker.isTypeAssignableTo(probe, type))) {
      return true;
    }
    if ((type.flags & ts.TypeFlags.Object) === 0) {
      return false;
    }
    if (checker.isArrayType(type) || checker.isTupleType(type)) {
      return checker.getTypeArguments(type as ts.TypeReference).some((member) => holds(member, seen));
    }
    if (((type as ts.ObjectType).objectFlags & ts.ObjectFlags.Reference) !== 0) {
      if (checker.getTypeArguments(type as ts.TypeReference).some((member) => holds(member, seen))) {
        return true;
      }
    }
    const properties = checker.getPropertiesOfType(type);
    const indexes = checker.getIndexInfosOfType(type);
    if (((type as ts.ObjectType).objectFlags & ts.ObjectFlags.Mapped) !== 0 && properties.length === 0 && indexes.length === 0) {
      // A mapped type over a type parameter: its members are not known, so it is a taker.
      return true;
    }
    if (indexes.some((index) => holds(index.type, seen))) {
      return true;
    }
    if (properties.some((member) => holds(checker.getTypeOfSymbol(member), seen))) {
      return true;
    }
    return [...type.getCallSignatures(), ...type.getConstructSignatures()].some(
      (signature) =>
        (signature.thisParameter !== undefined && holds(checker.getTypeOfSymbol(signature.thisParameter), seen)) ||
        signature.getParameters().some((parameter) => holds(checker.getTypeOfSymbol(parameter), seen)) ||
        holds(signature.getReturnType(), seen),
    );
  };
  return (type) => holds(type, new Set());
}

/**
 * The takers of a server text (D426): whether a type can be handed a `ServerText` or a `BoxedMap`,
 * by the same rule as `takes`, with the two boxes as the probes.
 */
export function takesText(program: ts.Program, checker: ts.TypeChecker): Takes {
  const text = exportOf(program, checker, "src/api/box.ts", "ServerText");
  const map = exportOf(program, checker, "src/api/box.ts", "BoxedMap");
  return takesWith(checker, [checker.getDeclaredTypeOfSymbol(text)], (type) => type.getSymbol() === map);
}

/** `takes`' rule over the given probes (and `named`: a type that is a probe's by its symbol, as
 * every instance of the generic `BoxedMap` is, whatever its type argument). */
function takesWith(checker: ts.TypeChecker, probes: readonly ts.Type[], named: (type: ts.Type) => boolean): Takes {
  const holds = (type: ts.Type, seen: Set<ts.Type>): boolean => {
    if (seen.has(type)) {
      return false;
    }
    seen.add(type);
    if (named(type)) {
      return true;
    }
    if ((type.flags & (ts.TypeFlags.Any | ts.TypeFlags.Unknown | ts.TypeFlags.NonPrimitive | ts.TypeFlags.Void)) !== 0) {
      return true;
    }
    if ((type.flags & (ts.TypeFlags.TypeParameter | CONSTRAINED)) !== 0) {
      const constraint = checker.getBaseConstraintOfType(type);
      return constraint === undefined || constraint === type || holds(constraint, seen);
    }
    if (type.isUnion()) {
      return type.types.some((member) => holds(member, seen));
    }
    if (type.isIntersection()) {
      return !type.types.some((member) => (member.flags & PRIMITIVE) !== 0) && type.types.some((member) => holds(member, seen));
    }
    if (!weak(checker, type) && probes.some((probe) => checker.isTypeAssignableTo(probe, type))) {
      return true;
    }
    if ((type.flags & ts.TypeFlags.Object) === 0) {
      return false;
    }
    if (checker.isArrayType(type) || checker.isTupleType(type)) {
      return checker.getTypeArguments(type as ts.TypeReference).some((member) => holds(member, seen));
    }
    if (((type as ts.ObjectType).objectFlags & ts.ObjectFlags.Reference) !== 0 && checker.getTypeArguments(type as ts.TypeReference).some((member) => holds(member, seen))) {
      return true;
    }
    const properties = checker.getPropertiesOfType(type);
    const indexes = checker.getIndexInfosOfType(type);
    if (((type as ts.ObjectType).objectFlags & ts.ObjectFlags.Mapped) !== 0 && properties.length === 0 && indexes.length === 0) {
      return true;
    }
    return (
      indexes.some((index) => holds(index.type, seen)) ||
      properties.some((member) => holds(checker.getTypeOfSymbol(member), seen)) ||
      [...type.getCallSignatures(), ...type.getConstructSignatures()].some(
        (signature) => signature.getParameters().some((parameter) => holds(checker.getTypeOfSymbol(parameter), seen)) || holds(signature.getReturnType(), seen),
      )
    );
  };
  return (type) => holds(type, new Set());
}

/**
 * **The gate of what an export gives (D426, m1 and m2 of the typed answers' round 4).** Every place
 * a value of a raw `string` can come out of a type, as paths: the gate over every export of every
 * module a module outside `src/api/` may import (`index.ts`, `operator.ts`, `oracle.ts`) lists them,
 * and each must be a pin, with its reason (`OUTPUT_PINS` in `api-graph.test.ts`).
 *
 * **Its definition.** A type is walked in output position; it gives:
 * - a raw string: `string` (not a literal: a literal type is the code's own word, such as
 *   `REFUSALS`' or a vocabulary's member), a template literal type, a string mapping, and an
 *   intersection with one of them (a branded string, such as `DatasetId`);
 * - a door: `any`, `unknown` and `object` (it may hold anything; `{}`, which `Boxed` makes of the
 *   generated `unknown` conjuncts, holds no member and is not one), and a library type the walk does
 *   not list (below);
 * - nothing: a number, a boolean, a symbol, `null`, `undefined`, `void` (a function typed to give
 *   nothing gives nothing its caller can use without a cast, which the lint refuses), `never`, and a
 *   type parameter, which gives back what its caller gave it (`entriesOf`'s `V`).
 * A union and an intersection give what their members give; an object type of the program gives
 * what its properties give (an ES private member, `#text`, is no property a caller can read and is
 * not walked), its index signatures' values, its call and construct signatures' returns, and, from
 * each parameter that is itself a function, what that function is handed (a listener's argument is
 * an output); a type argument of a reference is walked where the library type is cut short.
 * **Library types are cut short, as named rows:** `Promise`, `PromiseLike`, `Array`,
 * `ReadonlyArray`, `Map`, `ReadonlyMap`, `Set` and `ReadonlySet` give what their type arguments give
 * (their own members, `toString`, `join`, `keys`, are no data); `Error` and its subclasses give their
 * `message`, `name` and `stack` (a raw string each: pinned per export, the client's fixed words);
 * `AbortSignal`, `Headers` and the DOM's event targets give nothing of an answer. Any other library
 * type is a door until it is listed here.
 */
export type Gives = (type: ts.Type, at: string) => string[];

/** The library types cut short to their type arguments. */
const CONTAINERS = new Set(["Promise", "PromiseLike", "Array", "ReadonlyArray", "Map", "ReadonlyMap", "Set", "ReadonlySet"]);

/** The library types that give nothing of an answer. */
const INERT = new Set(["AbortSignal", "Headers", "EventTarget", "Window", "Document"]);

/** The raw-string flags. */
const RAW = ts.TypeFlags.String | ts.TypeFlags.TemplateLiteral | ts.TypeFlags.StringMapping;

/** What gives nothing. */
const NOTHING =
  ts.TypeFlags.NumberLike |
  ts.TypeFlags.BooleanLike |
  ts.TypeFlags.BigIntLike |
  ts.TypeFlags.ESSymbolLike |
  ts.TypeFlags.Null |
  ts.TypeFlags.Undefined |
  ts.TypeFlags.Void |
  ts.TypeFlags.Never |
  ts.TypeFlags.StringLiteral;

export function gives(program: ts.Program, checker: ts.TypeChecker): Gives {
  const library = (type: ts.Type): string | null => {
    // A named class or interface of a library (an anonymous or mapped type, `Readonly<{...}>`, is
    // walked by its members, whoever declared its alias).
    const symbol = type.getSymbol();
    const file = symbol?.declarations?.[0]?.getSourceFile();
    if (file === undefined || symbol === undefined || (symbol.flags & (ts.SymbolFlags.Class | ts.SymbolFlags.Interface)) === 0) {
      return null;
    }
    return program.isSourceFileDefaultLibrary(file) || file.fileName.includes("/node_modules/") ? symbol.getName() : null;
  };
  const isError = (type: ts.Type): boolean => {
    const message = checker.getPropertyOfType(type, "message");
    const stack = checker.getPropertyOfType(type, "stack");
    return message !== undefined && stack !== undefined && checker.getPropertyOfType(type, "name") !== undefined;
  };
  const walk = (type: ts.Type, at: string, seen: ReadonlySet<ts.Type>, found: string[]): void => {
    if (seen.has(type)) {
      return;
    }
    const next = new Set(seen).add(type);
    if ((type.flags & RAW) !== 0) {
      found.push(`${at}: string`);
      return;
    }
    if ((type.flags & (ts.TypeFlags.Any | ts.TypeFlags.Unknown | ts.TypeFlags.NonPrimitive)) !== 0) {
      found.push(`${at}: ${checker.typeToString(type)}`);
      return;
    }
    if ((type.flags & (NOTHING | ts.TypeFlags.TypeParameter)) !== 0) {
      return;
    }
    if (type.isUnion()) {
      type.types.forEach((member) => {
        walk(member, at, next, found);
      });
      return;
    }
    if (type.isIntersection()) {
      if (type.types.some((member) => (member.flags & RAW) !== 0)) {
        found.push(`${at}: ${checker.typeToString(type)}`);
        return;
      }
      type.types.forEach((member) => {
        walk(member, at, next, found);
      });
      return;
    }
    if ((type.flags & (ts.TypeFlags.Conditional | ts.TypeFlags.Substitution | ts.TypeFlags.IndexedAccess)) !== 0) {
      const constraint = checker.getBaseConstraintOfType(type);
      if (constraint === undefined || constraint === type) {
        found.push(`${at}: ${checker.typeToString(type)}`);
        return;
      }
      walk(constraint, at, next, found);
      return;
    }
    if ((type.flags & ts.TypeFlags.Object) === 0) {
      found.push(`${at}: ${checker.typeToString(type)}`);
      return;
    }
    const named = library(type);
    if (named !== null) {
      if (CONTAINERS.has(named) || checker.isArrayType(type) || checker.isTupleType(type)) {
        checker.getTypeArguments(type as ts.TypeReference).forEach((member, index) => {
          walk(member, `${at}<${String(index)}>`, next, found);
        });
        return;
      }
      if (INERT.has(named)) {
        return;
      }
      if (isError(type)) {
        found.push(`${at}: Error`);
        return;
      }
      found.push(`${at}: ${named} (an unlisted library type)`);
      return;
    }
    if (checker.isTupleType(type)) {
      checker.getTypeArguments(type as ts.TypeReference).forEach((member, index) => {
        walk(member, `${at}[${String(index)}]`, next, found);
      });
      return;
    }
    if (isError(type)) {
      found.push(`${at}: Error`);
      return;
    }
    for (const property of checker.getPropertiesOfType(type)) {
      const declaration = property.valueDeclaration ?? property.declarations?.[0];
      const declared = declaration === undefined ? undefined : ts.getNameOfDeclaration(declaration);
      if (declared !== undefined && ts.isPrivateIdentifier(declared)) {
        continue;
      }
      if (property.getName() === "prototype") {
        continue;
      }
      walk(checker.getTypeOfSymbol(property), `${at}.${property.getName()}`, next, found);
    }
    for (const index of checker.getIndexInfosOfType(type)) {
      walk(index.type, `${at}[key]`, next, found);
    }
    for (const signature of [...type.getCallSignatures(), ...type.getConstructSignatures()]) {
      walk(signature.getReturnType(), `${at}()`, next, found);
      for (const parameter of signature.getParameters()) {
        const handed = checker.getTypeOfSymbol(parameter);
        for (const inner of (handed.isUnion() ? handed.types : [handed]).flatMap((member) => member.getCallSignatures())) {
          for (const argument of inner.getParameters()) {
            walk(checker.getTypeOfSymbol(argument), `${at}(${parameter.getName()}(${argument.getName()}))`, next, found);
          }
        }
      }
    }
  };
  return (type, at) => {
    const found: string[] = [];
    walk(type, at, new Set(), found);
    return found;
  };
}

/**
 * **The gate of a map's keys (D426, m5 of the typed answers' round 1).** Every index signature of a
 * type whose key is a `string`, as paths: a map of data is a `BoxedMap` (its keys boxes in a
 * private field), so a walked answer's type holds none. A type that declares `{ [key: string]: V }`
 * for a map the walk makes a `BoxedMap` compiles code (`Object.keys(hit.facets)`) that gives `[]`
 * at run time. It walks unions, arrays, tuples, properties and index values, and stops at the three
 * boxes (`BoxedMap`, `ServerText`, `ServerNumber`).
 */
export function rawKeyed(checker: ts.TypeChecker): Gives {
  const boxes = new Set(["BoxedMap", "ServerText", "ServerNumber"]);
  const walk = (type: ts.Type, at: string, seen: ReadonlySet<ts.Type>, found: string[]): void => {
    if (seen.has(type)) {
      return;
    }
    const next = new Set(seen).add(type);
    if (type.isUnionOrIntersection()) {
      type.types.forEach((member) => {
        walk(member, at, next, found);
      });
      return;
    }
    if ((type.flags & ts.TypeFlags.Object) === 0 || boxes.has(type.getSymbol()?.getName() ?? "")) {
      return;
    }
    if (checker.isArrayType(type) || checker.isTupleType(type)) {
      checker.getTypeArguments(type as ts.TypeReference).forEach((member) => {
        walk(member, `${at}[]`, next, found);
      });
      return;
    }
    for (const index of checker.getIndexInfosOfType(type)) {
      if ((index.keyType.flags & RAW) !== 0) {
        found.push(`${at}[key]: a raw key`);
      }
      walk(index.type, `${at}[key]`, next, found);
    }
    for (const property of checker.getPropertiesOfType(type)) {
      walk(checker.getTypeOfSymbol(property), `${at}.${property.getName()}`, next, found);
    }
  };
  return (type, at) => {
    const found: string[] = [];
    walk(type, at, new Set(), found);
    return found;
  };
}

/**
 * **The conformance of the table and the types (D426, m2 of the typed answers' round 2).** The walk
 * gives a value its `Boxed` type by a type predicate the compiler takes on trust (`isAnswered`); the
 * table that the walk reads and the types that say what it gives are both generated from one document,
 * and this holds them to each other, node by node, with the checker's own reading of the type.
 *
 * It reads what a table node is by its **kind** (`text`: a string, an enum, a string const; `number`;
 * `boolean`; `null`; `list`; `record`; `map`; `segment`; `json`: any JSON value, read as the kinds of a
 * `BoxedJson`) and what a type is by the same words (`ServerText` is `text`, `ServerNumber` `number`,
 * `BoxedMap` `map`, an object with a literal `kind` of `text` or `data` a `segment`, an array or tuple a
 * `list`, any other object a `record`; a raw `string` or `number` is a kind of its own, which no
 * table node has, and so a mismatch). At each node the kinds of the alternatives of the table (an `or`'s
 * arms, an `allOf`'s first member, a `ref`'s component, read by its kinds alone) must be the kinds
 * of the union of the type, both ways; a `record` has the members of the table's, both ways, each
 * optional exactly when the table does not require it; a `map` is a `BoxedMap` whose values are of
 * the kinds of the table's `additionalProperties` and `patternProperties`; a `list`'s items are of its
 * `items`' kinds. So a map the walk makes a `BoxedMap` that the types declare as a record of named
 * optional members, which compiles `x.unknown_by_reason.NO_ROWS` and gives `undefined` at run time, is
 * a finding.
 */
export type Conforms = (type: ts.Type, shape: Shape, at: string) => string[];

/** What a node of the table, or of a type, is. */
type Kind = "text" | "number" | "boolean" | "null" | "list" | "record" | "map" | "segment" | "rawstring" | "rawnumber" | "any";

/** The kinds of the members of a `BoxedJson` (a value read as any JSON). */
const JSON_KINDS: readonly Kind[] = ["text", "number", "boolean", "null", "list", "map"];

/** A node of the table, one alternative: `opaque` when it was reached through a `ref` (its members
 * are checked where the component is, as a component of its own). */
interface Leaf {
  readonly shape: Shape;
  readonly opaque: boolean;
}

/** A member of a type's union: `opaque` when it stands for a `BoxedJson`'s. */
interface Member {
  readonly type: ts.Type;
  readonly kind: Kind;
  readonly opaque: boolean;
}

export function conformance(checker: ts.TypeChecker, table: Readonly<Record<string, Shape>>): Conforms {
  const kindOfType = (type: ts.Type): Kind => {
    if ((type.flags & ts.TypeFlags.Null) !== 0) {
      return "null";
    }
    if ((type.flags & ts.TypeFlags.BooleanLike) !== 0) {
      return "boolean";
    }
    if ((type.flags & (ts.TypeFlags.Any | ts.TypeFlags.Unknown)) !== 0) {
      return "any";
    }
    if ((type.flags & ts.TypeFlags.StringLike) !== 0) {
      return "rawstring";
    }
    if ((type.flags & ts.TypeFlags.NumberLike) !== 0) {
      return "rawnumber";
    }
    const name = type.getSymbol()?.getName();
    if (name === "ServerText") {
      return "text";
    }
    if (name === "ServerNumber") {
      return "number";
    }
    if (name === "BoxedMap") {
      return "map";
    }
    if (checker.isArrayType(type) || checker.isTupleType(type)) {
      return "list";
    }
    const kind = checker.getPropertyOfType(type, "kind");
    const literal = kind === undefined ? undefined : checker.getTypeOfSymbol(kind);
    if (literal !== undefined && literal.isStringLiteral() && (literal.value === "text" || literal.value === "data")) {
      return "segment";
    }
    return "record";
  };

  /** The members of a type's union (an optional member's `undefined` is no member). */
  const membersOf = (type: ts.Type, out: Member[]): void => {
    if (type.aliasSymbol?.getName() === "BoxedJson") {
      for (const kind of JSON_KINDS) {
        out.push({ type, kind, opaque: true });
      }
      return;
    }
    if (type.isUnion()) {
      for (const member of type.types) {
        membersOf(member, out);
      }
      return;
    }
    if ((type.flags & (ts.TypeFlags.Undefined | ts.TypeFlags.Void)) === 0) {
      out.push({ type, kind: kindOfType(type), opaque: false });
    }
  };

  /** The alternatives of a table node: an `or`'s arms, an `allOf`'s first member, a `ref`'s component
   * (opaque), a constant object as a record of constants. */
  const leavesOf = (shape: Shape, opaque: boolean, seen: ReadonlySet<string>): Leaf[] => {
    switch (shape.t) {
      case "ref": {
        const found = table[shape.n];
        return found === undefined || seen.has(shape.n) ? [{ shape, opaque: true }] : leavesOf(found, true, new Set(seen).add(shape.n));
      }
      case "or":
        return shape.o.flatMap((arm) => leavesOf(arm, opaque, seen));
      case "all": {
        const [first] = shape.o;
        return first === undefined ? [] : leavesOf(first, opaque, seen);
      }
      case "const": {
        const value = shape.v;
        if (typeof value === "object" && value !== null && !Array.isArray(value)) {
          const members: Record<string, Shape> = Object.fromEntries(Object.entries(value).map(([name, member]) => [name, { t: "const", v: member }]));
          return [{ shape: { t: "rec", p: members, r: Object.keys(value) }, opaque }];
        }
        return [{ shape, opaque }];
      }
      default:
        return [{ shape, opaque }];
    }
  };

  const kindsOf = (shape: Shape): Kind[] => {
    switch (shape.t) {
      case "str":
      case "enum":
        return ["text"];
      case "num":
        return ["number"];
      case "bool":
        return ["boolean"];
      case "null":
        return ["null"];
      case "arr":
        return ["list"];
      case "rec":
        return ["record"];
      case "map":
        return ["map"];
      case "seg":
        return ["segment"];
      case "json":
        return [...JSON_KINDS];
      case "const": {
        const value = shape.v;
        return [typeof value === "string" ? "text" : typeof value === "number" ? "number" : typeof value === "boolean" ? "boolean" : value === null ? "null" : Array.isArray(value) ? "list" : "record"];
      }
      case "any":
      case "ref":
        return ["any"];
      default:
        return [];
    }
  };

  const check = (leaves: readonly Leaf[], types: readonly ts.Type[], at: string, found: string[], depth: number): void => {
    if (depth > 16) {
      return;
    }
    const members: Member[] = [];
    for (const type of types) {
      membersOf(type, members);
    }
    const tableKinds = new Set(leaves.flatMap((leaf) => kindsOf(leaf.shape)));
    if (tableKinds.has("any")) {
      return;
    }
    const typeKinds = new Set(members.map((member) => member.kind));
    if (tableKinds.size !== typeKinds.size || [...tableKinds].some((kind) => !typeKinds.has(kind))) {
      found.push(`${at}: the table has [${[...tableKinds].sort().join(", ")}], the type [${[...typeKinds].sort().join(", ")}]`);
      return;
    }
    const records = members.filter((member) => member.kind === "record" && !member.opaque);
    const named = (type: ts.Type): string => checker.getPropertiesOfType(type).map((property) => property.getName()).sort().join(",");
    for (const leaf of leaves) {
      const shape = leaf.shape;
      if (leaf.opaque) {
        continue;
      }
      if (shape.t === "rec") {
        const names = Object.keys(shape.p).sort().join(",");
        const member = records.find((one) => named(one.type) === names);
        if (member === undefined) {
          found.push(`${at}: no record of the members [${names}] in the type (it has ${records.map((one) => `[${named(one.type)}]`).join(" | ")})`);
          continue;
        }
        for (const [name, node] of Object.entries(shape.p)) {
          const property = checker.getPropertyOfType(member.type, name);
          if (property === undefined) {
            continue;
          }
          const optional = (property.flags & ts.SymbolFlags.Optional) !== 0;
          if (optional === shape.r.includes(name)) {
            found.push(`${at}.${name}: the table ${optional ? "requires" : "does not require"} it, the type ${optional ? "makes it optional" : "requires it"}`);
          }
          check(leavesOf(node, false, new Set()), [checker.getTypeOfSymbol(property)], `${at}.${name}`, found, depth + 1);
        }
      } else if (shape.t === "arr") {
        const items = members.filter((member) => member.kind === "list" && !member.opaque && !checker.isTupleType(member.type)).map((member) => checker.getTypeArguments(member.type as ts.TypeReference)[0]);
        if (shape.pi === undefined && items.length > 0 && !items.includes(undefined)) {
          check(leavesOf(shape.i, false, new Set()), items as ts.Type[], `${at}[]`, found, depth + 1);
        }
      } else if (shape.t === "map") {
        const values = members.filter((member) => member.kind === "map" && !member.opaque).map((member) => checker.getTypeArguments(member.type as ts.TypeReference)[0]);
        const nodes = [...(shape.v.t === "never" ? [] : [shape.v]), ...(shape.pp ?? []).map(([, one]) => one)];
        if (values.length > 0 && !values.includes(undefined) && nodes.length > 0) {
          check(
            nodes.flatMap((node) => leavesOf(node, false, new Set())),
            values as ts.Type[],
            `${at}{}`,
            found,
            depth + 1,
          );
        }
      }
    }
    for (const member of records) {
      if (!leaves.some((leaf) => !leaf.opaque && leaf.shape.t === "rec" && Object.keys(leaf.shape.p).sort().join(",") === named(member.type)) && !leaves.some((leaf) => leaf.shape.t === "rec" && leaf.opaque)) {
        found.push(`${at}: a record of the members [${named(member.type)}] that the table has not`);
      }
    }
  };

  return (type, shape, at) => {
    const found: string[] = [];
    check(leavesOf(shape, false, new Set()), [type], at, found, 0);
    return found;
  };
}
