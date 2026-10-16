/**
 * Which files of `src/` reach a symbol, by the symbols the program resolves and not by the
 * specifiers they spell (D428): the visible form's context (`FORM`) and the door (`showText`) are
 * confined to the layout, and a file that imports a layout module which re-exports one reaches it
 * as surely as a file that imports its module (round 2 of #117, m12). A file reaches a symbol by an
 * import of any kind (named, aliased, default, namespace, type), a re-export (named, renamed, `export *`,
 * `export * as`), a dynamic import, an import type, or an `import x = require(...)`, each resolved
 * through the alias chain to the symbol it names; a namespace, a dynamic import and an import type
 * reach every export of the module they name.
 *
 * The scan is a gate of the tests, and no more: a symbol reached by a way a program does not
 * resolve (a `require` of an untyped module, a computed `import(name)`) is for the lint's rows.
 *
 * One rule, where the lint and the scan meet (m4 of round 3 of #117): the scan counts every reference
 * the program resolves to a module that holds a target, a type too (`typeof import("../api")` and its
 * indexed form `(typeof import("../api"))["showText"]` reach the index's `showText`), where the lint
 * allows the first as a type and no door. The scan is the stricter layer: nothing of `src/` writes
 * either, a cell holds that the scan sees them, and a file that did would be a reviewed change.
 */
import path from "node:path";

import ts from "typescript";

import type { Checked } from "./program";

/** A symbol the scan looks for: `names` of the module `file` (absolute), or every export. */
export interface Target {
  readonly file: string;
  readonly names?: readonly string[];
}

/** The symbol an alias names, through its chain. */
const resolved = (checker: ts.TypeChecker, symbol: ts.Symbol): ts.Symbol => ((symbol.flags & ts.SymbolFlags.Alias) !== 0 ? checker.getAliasedSymbol(symbol) : symbol);

/** Whether a symbol is a target, or a module that exports one (`export * as ns`, a namespace import). */
function reaches(checker: ts.TypeChecker, symbol: ts.Symbol, targets: readonly Target[], seen = new Set<ts.Symbol>()): boolean {
  const found = resolved(checker, symbol);
  if (seen.has(found)) {
    return false;
  }
  seen.add(found);
  if ((found.flags & ts.SymbolFlags.Module) !== 0) {
    return checker.getExportsOfModule(found).some((member) => reaches(checker, member, targets, seen));
  }
  return (found.declarations ?? []).some((declaration) =>
    targets.some((target) => path.resolve(declaration.getSourceFile().fileName) === target.file && (target.names === undefined || target.names.includes(found.name))),
  );
}

/** The module a specifier names, as the program resolves it. */
function moduleOf(checker: ts.TypeChecker, specifier: ts.Expression): ts.Symbol | undefined {
  return ts.isStringLiteralLike(specifier) ? checker.getSymbolAtLocation(specifier) : undefined;
}

/** Whether the file reaches any target, and the specifier of each import that does. */
export function reachedBy(checked: Checked, file: string, targets: readonly Target[]): string[] {
  const { checker } = checked;
  const found: string[] = [];
  const everything = (specifier: ts.Expression): void => {
    const module = moduleOf(checker, specifier);
    if (module !== undefined && reaches(checker, module, targets)) {
      found.push((specifier as ts.StringLiteralLike).text);
    }
  };
  const named = (specifier: ts.Expression, name: string): void => {
    const module = moduleOf(checker, specifier);
    const member = module === undefined ? undefined : checker.getExportsOfModule(module).find((candidate) => candidate.name === name);
    if (member !== undefined && reaches(checker, member, targets)) {
      found.push(`${(specifier as ts.StringLiteralLike).text}: ${name}`);
    }
  };
  const visit = (node: ts.Node): void => {
    if (ts.isImportDeclaration(node) && node.importClause !== undefined) {
      const clause = node.importClause;
      if (clause.name !== undefined) {
        named(node.moduleSpecifier, "default");
      }
      const bindings = clause.namedBindings;
      if (bindings !== undefined && ts.isNamespaceImport(bindings)) {
        everything(node.moduleSpecifier);
      } else if (bindings !== undefined) {
        for (const element of bindings.elements) {
          named(node.moduleSpecifier, (element.propertyName ?? element.name).text);
        }
      }
    } else if (ts.isExportDeclaration(node) && node.moduleSpecifier !== undefined) {
      if (node.exportClause !== undefined && ts.isNamedExports(node.exportClause)) {
        for (const element of node.exportClause.elements) {
          named(node.moduleSpecifier, (element.propertyName ?? element.name).text);
        }
      } else {
        everything(node.moduleSpecifier);
      }
    } else if (ts.isImportEqualsDeclaration(node) && ts.isExternalModuleReference(node.moduleReference)) {
      everything(node.moduleReference.expression);
    } else if (ts.isCallExpression(node) && node.expression.kind === ts.SyntaxKind.ImportKeyword && node.arguments[0] !== undefined) {
      everything(node.arguments[0]);
    } else if (ts.isImportTypeNode(node) && ts.isLiteralTypeNode(node.argument)) {
      everything(node.argument.literal);
    }
    ts.forEachChild(node, visit);
  };
  visit(checked.source(file));
  return found;
}

/** The exports of the modules of `files` that are a target, or an alias of one, as `file: name`:
 * who offers a path to the symbol, which is what a re-export in a layout module adds. */
export function offeredBy(checked: Checked, files: readonly string[], targets: readonly Target[]): string[] {
  const { checker } = checked;
  return files
    .flatMap((file) => {
      const module = checker.getSymbolAtLocation(checked.source(file));
      return module === undefined
        ? []
        : checker
            .getExportsOfModule(module)
            .filter((member) => reaches(checker, member, targets))
            .map((member) => `${file}: ${member.name}`);
    })
    .sort();
}
