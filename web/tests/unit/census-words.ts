/**
 * The static enumeration of the census (D435, B7.9), by what a module holds, whatever its spelling:
 * from the TypeScript program, every module that `Result` (`src/result/Figures.tsx`) and `Parameters`
 * (`src/result/Provenance.tsx`) reach by their imports inside `src/` (any extension), and, past the
 * index of `src/api/`, the module that declares each value they import from it when that value renders
 * (a function whose return type is an element: `format.tsx`' `Num`, `text.tsx`' `showText`), and the
 * declaration of each constant they import from it that does not (`SHAPE_MISMATCH`). In each,
 * every string literal, template literal part and JSX text that holds a letter is a word, wherever it
 * stands (a private constant, a table, an attribute, a component's prop), but at these positions, which
 * hold no page word: an import or export specifier, a type, a property's name, an operand of an
 * equality or a `case`, an element access's key, an argument of a call that gives no string and no
 * list of strings, and a
 * JSX attribute of the pinned non-word names (`NON_WORD_ATTRIBUTES`). Re-exports (`export … from`,
 * `export *`) are followed as imports are. A word is normalised (white space collapsed and trimmed).
 *
 * This enumeration is the census's static half; its runtime half (`result-sweep.test.tsx`) reads the
 * page as rendered, every text run outside a data or number isolate and every shown attribute, over
 * every body the sweep makes, so that a word in any spelling, here or elsewhere, is a census key on a
 * branch some body reaches. This half holds the modules and constants it reaches as stated and no
 * more: a word a function that does not render gives, imported through the api's index, is the
 * runtime half's alone.
 */
import path from "node:path";

import ts from "typescript";

import { appOptions, WEB } from "../gates/program";

/** The roots: the modules of `Result` and `Parameters`. */
export const ROOTS = ["src/result/Figures.tsx", "src/result/Provenance.tsx"];

/** The JSX attributes that hold no word (pinned, the runtime census's `NON_WORD` in JSX's spelling):
 * every other attribute's literal is a word, a component's prop included. */
export const NON_WORD_ATTRIBUTES = ["className", "role", "key", "id", "type", "name", "href", "rel", "autoComplete", "spellCheck", "disabled", "style"];

const INDEX = path.join(WEB, "src/api/index.ts");

const normal = (text: string): string => text.replace(/\s+/gu, " ").trim();
const wordy = (text: string): boolean => /\p{L}/u.test(text);

/** A JSX text's entities, as the page shows them. */
const ENTITIES: Readonly<Record<string, string>> = { apos: "'", quot: '"', amp: "&", lt: "<", gt: ">", nbsp: "\u00a0" };
const decoded = (text: string): string => text.replace(/&([a-z]+);/gu, (whole, name: string) => ENTITIES[name] ?? whole);

const EQUALITIES = [ts.SyntaxKind.EqualsEqualsEqualsToken, ts.SyntaxKind.ExclamationEqualsEqualsToken, ts.SyntaxKind.EqualsEqualsToken, ts.SyntaxKind.ExclamationEqualsToken];

/** Whether a type is a string's (a string, a string literal, or a union of them). */
function stringy(type: ts.Type): boolean {
  return type.isUnion() ? type.types.some(stringy) : (type.flags & ts.TypeFlags.StringLike) !== 0;
}

/** Whether a call's type gives a string: a string's, or a list (an array or a tuple) of one, at any
 * depth (`Array.of<"…">("…")`, round 3's t2). */
function givesString(type: ts.Type, checker: ts.TypeChecker, depth = 0): boolean {
  if (stringy(type)) {
    return true;
  }
  if (depth > 4) {
    return false;
  }
  if (type.isUnion()) {
    return type.types.some((one) => givesString(one, checker, depth + 1));
  }
  return (checker.isArrayType(type) || checker.isTupleType(type)) && checker.getTypeArguments(type as ts.TypeReference).some((one) => givesString(one, checker, depth + 1));
}

/** Whether a literal stands where no page word does (the module's docstring). */
function exempt(node: ts.Node, checker: ts.TypeChecker): boolean {
  const parent = node.parent;
  if (ts.isImportDeclaration(parent) || ts.isExportDeclaration(parent) || ts.isExternalModuleReference(parent) || ts.isLiteralTypeNode(parent)) {
    return true;
  }
  if ((ts.isPropertyAssignment(parent) || ts.isPropertySignature(parent) || ts.isPropertyDeclaration(parent) || ts.isMethodDeclaration(parent)) && parent.name === node) {
    return true;
  }
  // A call's argument, where the call gives no string and no list of strings (a string it gives may
  // be its argument, shown).
  if ((ts.isCallExpression(parent) || ts.isNewExpression(parent)) && (parent.arguments ?? []).some((argument) => argument === node)) {
    return !givesString(checker.getTypeAtLocation(parent), checker);
  }
  if (ts.isCaseClause(parent) || (ts.isElementAccessExpression(parent) && parent.argumentExpression === node)) {
    return true;
  }
  if (ts.isBinaryExpression(parent) && EQUALITIES.includes(parent.operatorToken.kind)) {
    return true;
  }
  if (ts.isJsxAttribute(parent) && NON_WORD_ATTRIBUTES.includes(parent.name.getText())) {
    return true;
  }
  for (let at: ts.Node = parent; !ts.isSourceFile(at); at = at.parent) {
    if (ts.isTypeNode(at)) {
      return true;
    }
  }
  return false;
}

/** The words of one source file of the program, or of one of its declarations (its parents set by the
 * checker's binding). */
export function wordsOf(source: ts.Node, checker: ts.TypeChecker): string[] {
  const found: string[] = [];
  const visit = (node: ts.Node): void => {
    if (ts.isJsxText(node)) {
      found.push(normal(decoded(node.text)));
    } else if ((ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) && !exempt(node, checker)) {
      found.push(normal(node.text));
    } else if (ts.isTemplateExpression(node) && !exempt(node, checker)) {
      found.push(normal(node.head.text), ...node.templateSpans.map((span) => normal(span.literal.text)));
    }
    ts.forEachChild(node, visit);
  };
  visit(source);
  return found.filter(wordy);
}

/** The program of the roots (the result screen's, or another screen's: the column screen's census,
 * D443), under the app's options. */
export function censusProgram(roots: readonly string[] = ROOTS): ts.Program {
  return ts.createProgram(
    roots.map((root) => path.join(WEB, root)),
    appOptions(),
  );
}

/** Whether a symbol renders: a function whose return type, made non-nullable, is an element. */
function renders(checker: ts.TypeChecker, symbol: ts.Symbol, at: ts.Node): boolean {
  return checker.getSignaturesOfType(checker.getTypeOfSymbolAtLocation(symbol, at), ts.SignatureKind.Call).some((signature) => /\b(ReactElement|Element)\b/u.test(checker.typeToString(checker.getNonNullableType(signature.getReturnType()))));
}

/** The modules the census reads, project-relative, and past the api's index each constant imported
 * that does not render (a variable's declaration: its words are read, not its module's). */
function reached(program: ts.Program, roots: readonly string[]): { readonly modules: string[]; readonly constants: ts.VariableDeclaration[] } {
  const checker = program.getTypeChecker();
  const seen = new Set<string>();
  const constants: ts.VariableDeclaration[] = [];
  const queue = roots.map((root) => path.join(WEB, root));
  const src = path.join(WEB, "src") + path.sep;
  while (queue.length > 0) {
    const file = queue.shift() ?? "";
    if (seen.has(file)) {
      continue;
    }
    seen.add(file);
    const source = program.getSourceFile(file);
    if (source === undefined) {
      throw new Error(`${file} is not in the program`);
    }
    for (const statement of source.statements) {
      // An import, and a re-export (`export … from`, `export *`): both reach a module.
      if (!(ts.isImportDeclaration(statement) || ts.isExportDeclaration(statement)) || statement.moduleSpecifier === undefined || !ts.isStringLiteral(statement.moduleSpecifier)) {
        continue;
      }
      // Resolved by the program itself (a module given as text resolves too).
      const declared = checker.getSymbolAtLocation(statement.moduleSpecifier)?.declarations?.[0];
      const resolved = declared !== undefined && ts.isSourceFile(declared) ? declared.fileName : undefined;
      if (resolved === undefined || !resolved.startsWith(src)) {
        continue;
      }
      if (resolved !== INDEX) {
        queue.push(resolved);
        continue;
      }
      // Past the index: the declaring module of each value imported that renders.
      const bindings = ts.isImportDeclaration(statement) ? statement.importClause?.namedBindings : undefined;
      for (const element of bindings !== undefined && ts.isNamedImports(bindings) ? bindings.elements : []) {
        const local = checker.getSymbolAtLocation(element.name);
        const symbol = local === undefined ? undefined : checker.getAliasedSymbol(local);
        const declaration = symbol?.declarations?.[0];
        if (symbol === undefined || declaration === undefined || element.isTypeOnly) {
          continue;
        }
        if (renders(checker, symbol, element)) {
          seen.add(declaration.getSourceFile().fileName);
        } else if (ts.isVariableDeclaration(declaration)) {
          constants.push(declaration);
        }
      }
    }
  }
  return { modules: [...seen].map((file) => path.relative(WEB, file)).sort(), constants };
}

/** The modules the census reads, project-relative. */
export function censusModules(program: ts.Program, roots: readonly string[] = ROOTS): string[] {
  return reached(program, roots).modules;
}

/** Every word of the census's modules, by module. */
export function enumerated(program: ts.Program = censusProgram(), roots: readonly string[] = ROOTS): Map<string, string[]> {
  const checker = program.getTypeChecker();
  const out = new Map<string, string[]>();
  const { modules, constants } = reached(program, roots);
  for (const module of modules) {
    const source = program.getSourceFile(path.join(WEB, module));
    if (source !== undefined) {
      out.set(module, wordsOf(source, checker));
    }
  }
  // A constant's words, under its module (the module itself is not read).
  for (const constant of constants) {
    const module = path.relative(WEB, constant.getSourceFile().fileName);
    out.set(module, [...(out.get(module) ?? []), ...wordsOf(constant, checker)]);
  }
  return out;
}
