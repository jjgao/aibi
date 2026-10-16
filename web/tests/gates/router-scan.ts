/**
 * The scan of the app's imports of the router package (D428), shared by `router.test.ts` and the
 * corpus of readers: what a file imports of the package that its list does not allow.
 */
import { readdirSync, statSync } from "node:fs";
import path from "node:path";

import ts from "typescript";

import { WEB } from "./program";

/** The router module. */
export const MODULE = "src/router/address.tsx";

/** What the app may import of the router package, by specifier (the lint's `ROUTER_ALLOWED`). */
export const ALLOWED: Readonly<Record<string, readonly string[]>> = {
  "react-router": ["createHashRouter", "RouteObject"],
  "react-router/dom": ["RouterProvider"],
};

/** What the router module may import: its one reader. */
export const OWN: Readonly<Record<string, readonly string[]>> = { "react-router": ["useParams"] };

/** Every file of `src/`, by its project-relative name. */
export function sources(directory = path.join(WEB, "src")): string[] {
  return readdirSync(directory).flatMap((name) => {
    const full = path.join(directory, name);
    return statSync(full).isDirectory() ? sources(full) : /\.(ts|tsx|js|jsx|mts|cts)$/u.test(name) ? [path.relative(WEB, full)] : [];
  });
}

/** Whether a specifier is of the router package: `react-router`, `react-router/dom`,
 * `react-router-dom`, `@react-router/...`. */
export const ofRouter = (specifier: string): boolean => /react-router/iu.test(specifier);

/** The names a source imports of the router package that its list does not allow, as
 * `specifier: name` (`*` a namespace or an export-all, `default`, `(side effect)`, `import()`). */
export function refused(file: string, text: string, allowed: Readonly<Record<string, readonly string[]>>): string[] {
  const source = ts.createSourceFile(file, text, ts.ScriptTarget.ES2023, true, file.endsWith("x") ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
  const found: string[] = [];
  const name = (specifier: string, imported: string): void => {
    if (!(allowed[specifier] ?? []).includes(imported)) {
      found.push(`${specifier}: ${imported}`);
    }
  };
  const visit = (node: ts.Node): void => {
    if (ts.isImportDeclaration(node) && ts.isStringLiteralLike(node.moduleSpecifier) && ofRouter(node.moduleSpecifier.text)) {
      const specifier = node.moduleSpecifier.text;
      const clause = node.importClause;
      if (clause === undefined) {
        found.push(`${specifier}: (side effect)`);
      } else {
        if (clause.name !== undefined) {
          found.push(`${specifier}: default`);
        }
        const bindings = clause.namedBindings;
        if (bindings !== undefined && ts.isNamespaceImport(bindings)) {
          found.push(`${specifier}: *`);
        } else if (bindings !== undefined) {
          for (const element of bindings.elements) {
            name(specifier, (element.propertyName ?? element.name).text);
          }
        }
      }
    } else if (ts.isExportDeclaration(node) && node.moduleSpecifier !== undefined && ts.isStringLiteralLike(node.moduleSpecifier) && ofRouter(node.moduleSpecifier.text)) {
      const specifier = node.moduleSpecifier.text;
      if (node.exportClause !== undefined && ts.isNamedExports(node.exportClause)) {
        for (const element of node.exportClause.elements) {
          name(specifier, (element.propertyName ?? element.name).text);
        }
      } else {
        found.push(`${specifier}: *`);
      }
    } else if (ts.isCallExpression(node) && node.expression.kind === ts.SyntaxKind.ImportKeyword) {
      const [first] = node.arguments;
      if (first === undefined || !ts.isStringLiteralLike(first) || ofRouter(first.text)) {
        found.push(`${first !== undefined && ts.isStringLiteralLike(first) ? first.text : "<computed>"}: import()`);
      }
    } else if (ts.isImportTypeNode(node) && ts.isLiteralTypeNode(node.argument) && ts.isStringLiteralLike(node.argument.literal) && ofRouter(node.argument.literal.text)) {
      found.push(`${node.argument.literal.text}: import()`);
    } else if (ts.isExternalModuleReference(node) && ts.isStringLiteralLike(node.expression) && ofRouter(node.expression.text)) {
      found.push(`${node.expression.text}: require`);
    }
    ts.forEachChild(node, visit);
  };
  visit(source);
  return found;
}

