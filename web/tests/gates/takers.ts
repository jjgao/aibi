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
