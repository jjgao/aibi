/**
 * The operator client's surface gate (D425): an **allow-list** over every place the client's
 * caller hands it a value, never a list of what is dangerous (round 2 of #115's review, MA2: a
 * check that asks "can a box be assigned to this parameter?" missed `Readonly<{ v: unknown }>`,
 * `NonNullable<T>`, `AbortSignal`, a nested member, a returned function and a `this`, and no check
 * of declared types sees an extra member of an object, so it cannot be complete).
 *
 * **The roots** are the client's: every value `src/api/operator.ts` and `src/api/oracle.ts` export
 * (the modules outside `src/api/` may import besides the index, which `api-graph.test.ts` holds
 * with its own check, `takers.ts`) and `curator.ts`'s `createCurator`. Each root's type is walked
 * as an **output** (what the client gives); the walk reaches every function type from there:
 *
 * - an output that is an object is looked at member by member, its index signatures, its call
 *   signatures and its construct signatures (every overload); a list (`Array`, `ReadonlyArray`), a
 *   tuple and a `Promise` of the default library by their elements; the box (`ServerNumber`, frozen,
 *   its methods take nothing) and a primitive are leaves; any other output (`unknown`, `any`,
 *   `object`, a type parameter, a conditional or an indexed type), and an object type that a
 *   function is assignable to (`{}`, `{ readonly length: number }`, `{ readonly name: string }`,
 *   `{ readonly toString: () => string }`, `Record<never, string>`: a caller narrows it to a function
 *   by `typeof r === "function"` and calls it with a box, which is the compiler's own rule), is a
 *   problem: the walk cannot see through it; a member that is `any` or `unknown` is one;
 * - a signature is a problem if it is generic; its `this` and each parameter (a rest parameter by
 *   its elements, which the call makes) is an **input**, and its return an output;
 * - a member, an index signature or a tuple the caller can write (no `readonly`, a setter, a method
 *   declared as a method, a mutable `Array`) is an input as well, at its written type.
 *
 * **An input** is allowed only if it is a primitive (`string`, `number`, `boolean`, `bigint`,
 * `null`, `undefined`, a literal, an enum, a template literal, and unions of them), or if its place
 * is pinned (`PINS`) and its type is exactly the pin's shape (the printed structure, every member
 * of the program's types expanded; a library type by its name, so a merged declaration of it is
 * printed as structure and differs). Anything else is a problem: `unknown`, `any`, `object`, `{}`,
 * a type parameter, a mapped, conditional or utility type, a library type, a function, an object
 * of strings (whose caller may add a member holding a box: object types are not exact), `void`
 * (a void-typed value can be anything) and `never`.
 *
 * **What a pin leaves:** the body of a pinned member is reviewed code; one that reads more than the
 * pin declares (an undeclared member of a node or of `Targets`, a listener's return value) is
 * deliberate, D419's boundary. The implementation names no `this` and no `arguments` (a function's
 * receiver and its extra arguments are inputs no type declares), which `receivers` holds.
 *
 * **What the walk does not hold, and what does (MA3 of round 3).** The allow-list holds where a
 * value comes from; it cannot hold where a box's digits are read, because any member's body can
 * call whatever its module imports. That is held apart from it: reading a box's text is confined
 * to one function, `provenance.ts`'s `idAt`, which reads only a node the recorder stamped for its
 * feed in its era, by lint rows (`textOf`: `decode.ts` and `provenance.ts`; `requestId`, `originOf`
 * and `stamp`: `provenance.ts`; `idAt`: `curator.ts`), the import-graph test and the build's
 * gate (`request-half.test.ts` (d) and (g)). So a member the allow-list lets pass cannot read a
 * box's digits, and one it does not cannot be added unseen. **`readonly` is held at run time:**
 * assignability ignores it, so `createCurator` freezes the client, and what it retains and hands
 * out, its view, is frozen (`curator.test.ts`).
 */
import path from "node:path";

import ts from "typescript";

import { WEB } from "./program";

/** A place the walk found something at, and what. */
export interface Problem {
  readonly at: string;
  readonly why: string;
}

/** A pinned input: the exact shape a reviewed member takes at one place, and why. */
export interface Pin {
  readonly shape: string;
  readonly why: string;
}

/** The DOM's `Event` as the program has it: `@types/react` merges a declaration into the default
 * library's, so it is printed member by member. */
const EVENT =
  "{ cancelBubble: false | true; method composedPath: composedPath; method initEvent: initEvent; method preventDefault: preventDefault; method stopImmediatePropagation: stopImmediatePropagation; method stopPropagation: stopPropagation; readonly AT_TARGET: 2; readonly BUBBLING_PHASE: 3; readonly CAPTURING_PHASE: 1; readonly NONE: 0; readonly bubbles: false | true; readonly cancelable: false | true; readonly composed: false | true; readonly currentTarget: null | { method addEventListener: addEventListener; method dispatchEvent: dispatchEvent; method removeEventListener: removeEventListener }; readonly defaultPrevented: false | true; readonly eventPhase: number; readonly isTrusted: false | true; readonly srcElement: null | { method addEventListener: addEventListener; method dispatchEvent: dispatchEvent; method removeEventListener: removeEventListener }; readonly target: null | { method addEventListener: addEventListener; method dispatchEvent: dispatchEvent; method removeEventListener: removeEventListener }; readonly timeStamp: number; readonly type: string; returnValue: false | true }";

/** `curator.ts`'s `Listens`: the page's `addEventListener`, which the client calls. */
const LISTENS = `{ method addEventListener: (string, (${EVENT}) => void, ?AddEventListenerOptions | undefined) => void }`;

/** A decoded node, `json.ts`'s `JsonOut`. */
const NODE = "ReadonlyArray<@JsonOut> | ServerNumber | false | null | string | true | { readonly [string]: @JsonOut }";

/** The pinned inputs, derived from the real client's members: each a place (`root.member(#i)`,
 * the i-th parameter) and the exact shape it takes, with why the client takes it. A pin holds at
 * its place alone: the same type at another place is refused. */
export const PINS: Readonly<Record<string, Pin>> = {
  "operator.unlock(#2)": {
    shape: "AbortSignal | undefined",
    why: "the shell's cancel of an unlock: the client hands it to fetch and listens for its abort, and reads nothing of it",
  },
  "operator.subscribe(#0)": {
    shape: "() => void",
    why: "the shell's listener: the client calls it with nothing and ignores what it returns",
  },
  "operator.install(#0)": {
    shape: `{ readonly document: ${LISTENS}; readonly window: ${LISTENS} }`,
    why: "the page's addEventListener of the window and the document (D423): the client calls them with its own listeners",
  },
  "operator.change(#1)": {
    shape: 'ReadonlyArray<{ readonly descriptor: string; readonly evidence?: string | undefined; readonly op: "confirm"; readonly pointers?: ReadonlyArray<string> | undefined } | { readonly descriptor: string; readonly op: "remove"; readonly pointer: string } | { readonly descriptor: string; readonly op: "remove_descriptor" }>',
    why: "a change's edits, those with no number in them (D423): the three generated shapes, of strings and literals alone",
  },
  "operator.takeOver(#0)": { shape: NODE, why: "a decoded node, sent back by its origin (D425)" },
  "operator.rejectProposal(#0)": { shape: NODE, why: "a decoded node, sent back by its origin (D425)" },
  "operator.acceptProposal(#0)": { shape: NODE, why: "a decoded node, sent back by its origin (D425)" },
  "operator.queueAt(#0)": { shape: NODE, why: "a decoded node, sent back by its origin (D425)" },
  "createCurator(#0)": {
    shape: "{ readonly monotonic: () => number; readonly wall: () => number }",
    why: "the clocks, the page's or a test's: called, and read as numbers",
  },
  "createCurator(#1)": {
    shape: "{ readonly clear: (unknown) => void; readonly set: (() => void, number) => unknown }",
    why: "the timer, the page's setInterval and clearInterval or a test's: what set gives is handed back to clear, never read",
  },
};

/** The primitives an input may be: none can hold an object, so no box. */
const PRIMITIVE =
  ts.TypeFlags.String |
  ts.TypeFlags.Number |
  ts.TypeFlags.Boolean |
  ts.TypeFlags.BigInt |
  ts.TypeFlags.Null |
  ts.TypeFlags.Undefined |
  ts.TypeFlags.StringLiteral |
  ts.TypeFlags.NumberLiteral |
  ts.TypeFlags.BooleanLiteral |
  ts.TypeFlags.BigIntLiteral |
  ts.TypeFlags.EnumLiteral |
  ts.TypeFlags.Enum |
  ts.TypeFlags.TemplateLiteral;

/** Whether a type is a primitive of the allow-list, or a union of them. */
export function primitive(type: ts.Type): boolean {
  if (type.isUnion()) {
    return type.types.every(primitive);
  }
  return (type.flags & PRIMITIVE) !== 0 && (type.flags & ~PRIMITIVE) === 0;
}

/** Outputs that are leaves: nothing the caller is given through them takes anything. */
const LEAF = PRIMITIVE | ts.TypeFlags.Void | ts.TypeFlags.Never | ts.TypeFlags.ESSymbol | ts.TypeFlags.UniqueESSymbol;

/** How deep the output walk goes before it gives up (a problem): far beyond the client's own. */
const DEPTH = 24;

/** How many outputs one walk looks at before it gives up (a problem, never a crash: a walk that
 * went into library types, whose methods nest without end, once ran a test out of memory). Thirty
 * times what the brute force's thousand members need together (`Surface.visits`). */
export const BUDGET = 500_000;

/** Whether every declaration of a symbol is the default library's (a merged declaration of the
 * program's makes it not). */
function defaultLibrary(program: ts.Program, symbol: ts.Symbol | undefined): boolean {
  const declarations = symbol?.declarations ?? [];
  return declarations.length > 0 && declarations.every((declaration) => program.isSourceFileDefaultLibrary(declaration.getSourceFile()));
}

/** The default library's container a type is an instance of, by name (`Array`, `ReadonlyArray`,
 * `Promise`), or `undefined`. */
function container(program: ts.Program, type: ts.Type): string | undefined {
  if ((type.flags & ts.TypeFlags.Object) === 0 || ((type as ts.ObjectType).objectFlags & ts.ObjectFlags.Reference) === 0) {
    return undefined;
  }
  const symbol = (type as ts.TypeReference).target.getSymbol();
  const name = symbol?.getName();
  return name !== undefined && ["Array", "ReadonlyArray", "Promise"].includes(name) && defaultLibrary(program, symbol) ? name : undefined;
}

/** Whether a declaration carries `readonly`. */
function readonlyDeclaration(declaration: ts.Declaration): boolean {
  return (ts.getCombinedModifierFlags(declaration) & ts.ModifierFlags.Readonly) !== 0;
}

/** The type a caller can write to a member, or `undefined` if it is read-only: a property declared
 * `readonly` or a getter without a setter. A setter is written at its parameter's type; anything
 * else (a property without `readonly`, a method, a member without a declaration the walk can
 * read, such as a mapped type's) is written at its type. */
function writeType(checker: ts.TypeChecker, member: ts.Symbol): ts.Type | undefined {
  const declarations = member.declarations ?? [];
  const setter = declarations.find(ts.isSetAccessorDeclaration);
  if (setter !== undefined) {
    const [parameter] = setter.parameters;
    return parameter === undefined ? checker.getTypeOfSymbol(member) : checker.getTypeAtLocation(parameter);
  }
  if (declarations.length > 0 && declarations.every((declaration) => ts.isGetAccessorDeclaration(declaration))) {
    return undefined;
  }
  if (declarations.length > 0 && declarations.every((declaration) => (ts.isPropertySignature(declaration) || ts.isPropertyDeclaration(declaration)) && readonlyDeclaration(declaration))) {
    return undefined;
  }
  return checker.getTypeOfSymbol(member);
}

/** Whether a member is declared as a method (its shape says so: a method is writable). */
function isMethod(member: ts.Symbol): boolean {
  return (member.declarations ?? []).some((declaration) => ts.isMethodSignature(declaration) || ts.isMethodDeclaration(declaration));
}

/** The exact shape of a type: its structure printed, the program's types expanded member by member
 * (names of parameters left out, members sorted), a library type by its name and type arguments,
 * and the box (`box.ts`'s class, whose members are private or throw) by its name.
 * A type met again while it is printed is `@` and its name. Kinds without a structure (a
 * conditional type, an indexed one) are printed as `?` and the checker's text, never equal to a
 * pin. */
export function shapeOf(program: ts.Program, checker: ts.TypeChecker, type: ts.Type): string {
  const box = boxOf(program, checker);
  const stack: ts.Type[] = [];
  const signature = (found: ts.Signature, kind: "" | "new "): string => {
    const parameters = found.getParameters().map((parameter) => {
      const declaration = parameter.valueDeclaration;
      const optional = declaration !== undefined && ts.isParameter(declaration) && (declaration.questionToken !== undefined || declaration.initializer !== undefined);
      const rest = declaration !== undefined && ts.isParameter(declaration) && declaration.dotDotDotToken !== undefined;
      return `${rest ? "..." : ""}${optional ? "?" : ""}${print(checker.getTypeOfSymbol(parameter))}`;
    });
    const self = found.thisParameter === undefined ? [] : [`this: ${print(checker.getTypeOfSymbol(found.thisParameter))}`];
    const generics = (found.typeParameters ?? []).map((parameter) => checker.typeToString(parameter));
    return `${kind}${generics.length > 0 ? `<${generics.join(", ")}>` : ""}(${[...self, ...parameters].join(", ")}) => ${print(found.getReturnType())}`;
  };
  const print = (found: ts.Type): string => {
    if (stack.includes(found)) {
      return `@${found.aliasSymbol?.getName() ?? found.getSymbol()?.getName() ?? "cycle"}`;
    }
    if (found === box) {
      return "ServerNumber";
    }
    if (found.isUnion() || found.isIntersection()) {
      stack.push(found);
      const members = found.types.map(print).sort();
      stack.pop();
      return members.join(found.isUnion() ? " | " : " & ");
    }
    if ((found.flags & ts.TypeFlags.Object) === 0) {
      const text = checker.typeToString(found, undefined, ts.TypeFormatFlags.NoTruncation);
      const plain = ts.TypeFlags.Any | ts.TypeFlags.Unknown | ts.TypeFlags.NonPrimitive | LEAF;
      return (found.flags & ~plain) === 0 ? text : `?${text}`;
    }
    const reference = ((found as ts.ObjectType).objectFlags & ts.ObjectFlags.Reference) !== 0 ? (found as ts.TypeReference) : undefined;
    stack.push(found);
    try {
      if (reference !== undefined && checker.isTupleType(found)) {
        const tuple = reference.target as ts.TupleType;
        return `${tuple.readonly ? "readonly " : ""}[${checker.getTypeArguments(reference).map(print).join(", ")}]`;
      }
      const symbol = (reference?.target ?? found).getSymbol();
      if (defaultLibrary(program, symbol)) {
        const typeArguments = reference === undefined ? [] : checker.getTypeArguments(reference);
        return `${symbol?.getName() ?? "?"}${typeArguments.length > 0 ? `<${typeArguments.map(print).join(", ")}>` : ""}`;
      }
      const members = checker
        .getPropertiesOfType(found)
        .map((member) => {
          const optional = (member.flags & ts.SymbolFlags.Optional) !== 0 ? "?" : "";
          const kind = isMethod(member) ? "method " : writeType(checker, member) === undefined ? "readonly " : "";
          return `${kind}${member.getName()}${optional}: ${print(checker.getTypeOfSymbol(member))}`;
        })
        .sort();
      const indexes = checker.getIndexInfosOfType(found).map((info) => `${info.isReadonly ? "readonly " : ""}[${print(info.keyType)}]: ${print(info.type)}`);
      const calls = found.getCallSignatures().map((one) => signature(one, ""));
      const constructs = found.getConstructSignatures().map((one) => signature(one, "new "));
      if (members.length === 0 && indexes.length === 0 && constructs.length === 0 && calls.length === 1) {
        return calls[0] ?? "";
      }
      return `{ ${[...members, ...indexes, ...calls, ...constructs].join("; ")} }`;
    } finally {
      stack.pop();
    }
  };
  return print(type);
}

/** The client's roots: each value a root module exports, by its name, and `createCurator`. */
export const ROOT_MODULES = ["src/api/operator.ts", "src/api/oracle.ts"] as const;

/** The walk of the client's surface over a program: the problems, and what it looked at. */
export interface Surface {
  readonly problems: readonly Problem[];
  /** The places of every input the walk met, allowed or not. */
  readonly inputs: readonly string[];
  /** How many members the operator client has. */
  readonly members: number;
  /** The roots walked. */
  readonly roots: readonly string[];
  /** How many outputs the walk looked at (`BUDGET` bounds it). */
  readonly visits: number;
}

/** An export of a module of the program, its alias resolved. */
function exportsOf(program: ts.Program, checker: ts.TypeChecker, file: string): ts.Symbol[] {
  const source = program.getSourceFile(path.join(WEB, file));
  const module = source === undefined ? undefined : checker.getSymbolAtLocation(source);
  if (module === undefined) {
    throw new Error(`no module ${file}`);
  }
  return checker.getExportsOfModule(module).map((symbol) => ((symbol.flags & ts.SymbolFlags.Alias) !== 0 ? checker.getAliasedSymbol(symbol) : symbol));
}

/** The box's type, `box.ts`'s `ServerNumber`. */
function boxOf(program: ts.Program, checker: ts.TypeChecker): ts.Type {
  const box = exportsOf(program, checker, "src/api/box.ts").find((symbol) => symbol.getName() === "ServerNumber");
  if (box === undefined) {
    throw new Error("box.ts exports no ServerNumber");
  }
  return checker.getDeclaredTypeOfSymbol(box);
}

/** The walk (module docstring). */
export function surface(program: ts.Program, checker: ts.TypeChecker, pins: Readonly<Record<string, Pin>> = PINS, budget: number = BUDGET): Surface {
  const problems: Problem[] = [];
  const inputs: string[] = [];
  const met = new Set<string>();
  const boxType = boxOf(program, checker);
  const problem = (at: string, why: string): void => {
    problems.push({ at, why });
  };
  const text = (type: ts.Type): string => checker.typeToString(type);
  // The default library's `Function`, what `typeof r === "function"` narrows to.
  const callableFrom = ((): ts.Type | undefined => {
    const symbol = checker.resolveName("Function", undefined, ts.SymbolFlags.Type, false);
    return symbol === undefined ? undefined : checker.getDeclaredTypeOfSymbol(symbol);
  })();
  if (callableFrom === undefined) {
    throw new Error("the default library declares no Function");
  }

  const input = (type: ts.Type, at: string): void => {
    inputs.push(at);
    const pin = pins[at];
    if (pin !== undefined) {
      met.add(at);
      const shape = shapeOf(program, checker, type);
      if (shape !== pin.shape) {
        problem(at, `pinned to ${pin.shape}, takes ${shape}`);
      }
      return;
    }
    if (!primitive(type)) {
      problem(at, `takes ${text(type)}: neither a primitive nor pinned`);
    }
  };

  const signature = (found: ts.Signature, at: string, seen: readonly ts.Type[]): void => {
    if ((found.typeParameters?.length ?? 0) > 0) {
      problem(at, "a generic signature");
    }
    if (found.thisParameter !== undefined) {
      input(checker.getTypeOfSymbol(found.thisParameter), `${at}(this)`);
    }
    found.getParameters().forEach((parameter, index) => {
      const declaration = parameter.valueDeclaration;
      const type = checker.getTypeOfSymbol(parameter);
      const place = `${at}(#${String(index)})`;
      if (declaration !== undefined && ts.isParameter(declaration) && declaration.dotDotDotToken !== undefined) {
        // A rest parameter's list is made by the call: what the caller gives are its elements.
        if (checker.isArrayType(type) || checker.isTupleType(type)) {
          checker.getTypeArguments(type as ts.TypeReference).forEach((element, position) => {
            input(element, `${place}[${String(position)}]`);
          });
          return;
        }
      }
      input(type, place);
    });
    output(found.getReturnType(), `${at}()`, seen);
  };

  let visits = 0;
  const output = (type: ts.Type, at: string, ancestors: readonly ts.Type[]): void => {
    visits += 1;
    if (visits > budget) {
      if (visits === budget + 1) {
        problem(at, "the walk exceeded its budget of outputs");
      }
      return;
    }
    // A type met again on its own path is walked already, up the path; a type met on another path
    // is walked again there, so that a member typed as another's (`Curator["takeOver"]`) is a
    // place of its own.
    if (ancestors.includes(type) || (type.flags & ~LEAF) === 0 || type === boxType) {
      return;
    }
    const seen = [...ancestors, type];
    if (type.isUnion() || type.isIntersection()) {
      type.types.forEach((member) => {
        output(member, at, seen);
      });
      return;
    }
    if ((type.flags & ts.TypeFlags.Object) === 0) {
      problem(at, `gives ${text(type)}: an output the walk cannot see through`);
      return;
    }
    if (checker.isTupleType(type)) {
      const tuple = (type as ts.TypeReference).target as ts.TupleType;
      checker.getTypeArguments(type as ts.TypeReference).forEach((element, position) => {
        output(element, `${at}[${String(position)}]`, seen);
        if (!tuple.readonly) {
          input(element, `${at}[${String(position)}]=`);
        }
      });
      return;
    }
    if (seen.length > DEPTH) {
      problem(at, "an output nested deeper than the walk looks");
      return;
    }
    const kind = container(program, type);
    if (kind !== undefined) {
      const [element] = checker.getTypeArguments(type as ts.TypeReference);
      if (element !== undefined) {
        output(element, kind === "Promise" ? `${at}<>` : `${at}[]`, seen);
        if (kind === "Array") {
          input(element, `${at}[]=`);
        }
      }
      return;
    }
    if (defaultLibrary(program, (((type as ts.ObjectType).objectFlags & ts.ObjectFlags.Reference) !== 0 ? (type as ts.TypeReference).target : type).getSymbol())) {
      problem(at, `gives ${text(type)}: a library type the walk does not look into`);
      return;
    }
    // An object type a function is assignable to (`{}`, `NonNullable<unknown>`, `{ readonly length:
    // number }`, `{ readonly name: string }`, `{ readonly toString: () => string }`, `Record<never,
    // string>`) is one a caller narrows to a function by `typeof r === "function"` and calls with a
    // box: the very rule the compiler's narrowing applies (found by the brute force's output forms in
    // round 3 for `{}`, and for the class in round 4, S1b). The walk cannot see through it, so it is
    // refused (an object with a member a function lacks narrows to `never`, and is walked).
    if (checker.isTypeAssignableTo(callableFrom, type)) {
      problem(at, `gives ${text(type)}: an object type a function is assignable to, which a caller narrows to a function and calls with a box`);
      return;
    }
    for (const member of checker.getPropertiesOfType(type)) {
      const place = `${at}.${member.getName()}`;
      output(checker.getTypeOfSymbol(member), place, seen);
      const written = writeType(checker, member);
      if (written !== undefined) {
        input(written, `${place}=`);
      }
    }
    for (const info of checker.getIndexInfosOfType(type)) {
      const place = `${at}[${text(info.keyType)}]`;
      output(info.type, place, seen);
      if (!info.isReadonly) {
        input(info.type, `${place}=`);
      }
    }
    const calls = type.getCallSignatures();
    calls.forEach((found, index) => {
      signature(found, calls.length === 1 ? at : `${at}[${String(index)}]`, seen);
    });
    const constructs = type.getConstructSignatures();
    constructs.forEach((found, index) => {
      signature(found, `${at}.new${constructs.length === 1 ? "" : `[${String(index)}]`}`, seen);
    });
  };

  const roots: string[] = [];
  for (const file of ROOT_MODULES) {
    for (const symbol of exportsOf(program, checker, file)) {
      if ((symbol.flags & ts.SymbolFlags.Value) !== 0) {
        roots.push(symbol.getName());
        output(checker.getTypeOfSymbol(symbol), symbol.getName(), []);
      }
    }
  }
  // The client's maker: its parameters are inputs; what it gives is `operator`'s type, walked there
  // (anything else it gives is walked here).
  const maker = exportsOf(program, checker, "src/api/curator.ts").find((symbol) => symbol.getName() === "createCurator");
  const client = exportsOf(program, checker, "src/api/operator.ts").find((symbol) => symbol.getName() === "operator");
  if (maker === undefined || client === undefined) {
    throw new Error("no createCurator or no operator");
  }
  const clientType = checker.getTypeOfSymbol(client);
  roots.push("createCurator");
  const makerType = checker.getTypeOfSymbol(maker);
  output(makerType, "createCurator", [clientType]);
  if (makerType.getCallSignatures().some((found) => found.getReturnType() !== clientType)) {
    problem("createCurator()", "gives another type than operator's");
  }

  for (const at of Object.keys(pins)) {
    if (!met.has(at)) {
      problem(at, "a pin no input meets");
    }
  }
  const members = checker.getPropertiesOfType(clientType).length;
  return { problems, inputs, members, roots, visits };
}

/** The receivers no type declares, in the client's implementation: each `this` and `arguments` of
 * `src/api/curator.ts` and `src/api/operator.ts`, by file and line. */
export function receivers(program: ts.Program): string[] {
  const found: string[] = [];
  for (const file of ["src/api/curator.ts", "src/api/operator.ts"]) {
    const source = program.getSourceFile(path.join(WEB, file));
    if (source === undefined) {
      throw new Error(`no ${file}`);
    }
    const visit = (node: ts.Node): void => {
      if (node.kind === ts.SyntaxKind.ThisKeyword || node.kind === ts.SyntaxKind.ThisType || (ts.isIdentifier(node) && node.text === "arguments")) {
        found.push(`${file}:${String(source.getLineAndCharacterOfPosition(node.getStart(source)).line + 1)}`);
      }
      ts.forEachChild(node, visit);
    };
    visit(source);
  }
  return found;
}

/** The modules of `src/api/` that a file of the program outside it imports or re-exports, by
 * project-relative name: the client's entry points. */
export function entries(program: ts.Program, checker: ts.TypeChecker): string[] {
  const api = path.join(WEB, "src/api");
  const found = new Set<string>();
  for (const source of program.getSourceFiles()) {
    const relative = path.relative(WEB, source.fileName);
    if (relative.startsWith("..") || relative.startsWith("node_modules/") || source.fileName.startsWith(`${api}/`)) {
      continue;
    }
    const visit = (node: ts.Node): void => {
      const specifier =
        (ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) && node.moduleSpecifier !== undefined
          ? node.moduleSpecifier
          : ts.isCallExpression(node) && node.expression.kind === ts.SyntaxKind.ImportKeyword
            ? node.arguments[0]
            : undefined;
      const target = specifier === undefined ? undefined : checker.getSymbolAtLocation(specifier)?.declarations?.[0]?.getSourceFile().fileName;
      if (target?.startsWith(`${api}/`) === true) {
        found.add(path.relative(WEB, target));
      }
      ts.forEachChild(node, visit);
    };
    visit(source);
  }
  return [...found].sort();
}
