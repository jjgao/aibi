/**
 * The app's TypeScript program for the gates that ask the type checker (D419): the compiler
 * options of `tsconfig.app.json` (no `skipLibCheck`), and files given as text placed in the
 * project (`src/...`), so that they import the real `src/api/` as the app does.
 */
import path from "node:path";

import ts from "typescript";

export const WEB = path.join(import.meta.dirname, "../..");

let options: ts.CompilerOptions | undefined;

/** `tsconfig.app.json`'s compiler options, as `tsc -p` reads them. */
export function appOptions(): ts.CompilerOptions {
  if (options === undefined) {
    const parsed = ts.getParsedCommandLineOfConfigFile(path.join(WEB, "tsconfig.app.json"), {}, {
      ...ts.sys,
      onUnRecoverableConfigFileDiagnostic: (diagnostic) => {
        throw new Error(ts.flattenDiagnosticMessageText(diagnostic.messageText, "\n"));
      },
    });
    if (parsed === undefined) {
      throw new Error("tsconfig.app.json is unreadable");
    }
    options = parsed.options;
  }
  return options;
}

/** "Declared but never read": a probe's declarations need not all be used. */
const UNUSED = new Set([6133, 6192, 6196, 6198, 6199, 6205]);

export interface Checked {
  readonly program: ts.Program;
  readonly checker: ts.TypeChecker;
  /** The diagnostics of one of the given files, by its project-relative name. */
  diagnostics(file: string): readonly ts.Diagnostic[];
  /** The codes of a given file's diagnostics, but those of an unused declaration. */
  codes(file: string): number[];
  source(file: string): ts.SourceFile;
}

/** A program of the given files (project-relative names, such as `src/probe-1.tsx`, to their
 * text) and every file they import, under the app's options. */
export function check(files: Readonly<Record<string, string>>, extra: ts.CompilerOptions = {}): Checked {
  const compilerOptions = { ...appOptions(), ...extra };
  const given = new Map(Object.entries(files).map(([name, text]) => [path.join(WEB, name), text]));
  const host = ts.createCompilerHost(compilerOptions, true);
  const getSourceFile = host.getSourceFile.bind(host);
  host.getSourceFile = (fileName, languageVersion, onError, shouldCreate) => {
    const text = given.get(path.resolve(fileName));
    return text === undefined
      ? getSourceFile(fileName, languageVersion, onError, shouldCreate)
      : ts.createSourceFile(fileName, text, languageVersion, true);
  };
  const fileExists = host.fileExists.bind(host);
  host.fileExists = (fileName) => given.has(path.resolve(fileName)) || fileExists(fileName);
  const readFile = host.readFile.bind(host);
  host.readFile = (fileName) => given.get(path.resolve(fileName)) ?? readFile(fileName);
  const program = ts.createProgram([...given.keys()], compilerOptions, host);
  const source = (file: string): ts.SourceFile => {
    const found = program.getSourceFile(path.join(WEB, file));
    if (found === undefined) {
      throw new Error(`${file} is not in the program`);
    }
    return found;
  };
  const diagnostics = (file: string): readonly ts.Diagnostic[] => {
    const found = source(file);
    return [...program.getSyntacticDiagnostics(found), ...program.getSemanticDiagnostics(found)];
  };
  return {
    program,
    checker: program.getTypeChecker(),
    diagnostics,
    codes: (file) => diagnostics(file).flatMap((diagnostic) => (UNUSED.has(diagnostic.code) ? [] : [diagnostic.code])),
    source,
  };
}

/** The node of `file` that starts right after the first occurrence of `marker` (a comment such
 * as `/*here*\/`). */
export function nodeAfter(checked: Checked, file: string, marker: string): ts.Node {
  const found = checked.source(file);
  const at = found.text.indexOf(marker);
  if (at < 0) {
    throw new Error(`no ${marker} in ${file}`);
  }
  const position = at + marker.length;
  let deepest: ts.Node | undefined;
  const visit = (node: ts.Node): void => {
    if (node.getStart(found) === position && deepest === undefined) {
      deepest = node;
    }
    if (node.pos <= position && position < node.end) {
      ts.forEachChild(node, visit);
    }
  };
  visit(found);
  if (deepest === undefined) {
    throw new Error(`no node after ${marker} in ${file}`);
  }
  return deepest;
}
