/**
 * The gates' linter (m8 of round 2 of #115's review): ESLint, whose every `lintText` leaves the
 * project service as it found it. typescript-eslint's project service is shared by every lint of a
 * test file, and a `lintText` at a path makes that text the file's for every later lint: a real
 * file of `src/api/` given other text leaves a later file's import of `./api` without types (every
 * `no-unsafe-*` rule then fires whatever the code), so a lint's result depended on the lints run
 * before it, and on the order of the tests. This one keeps the paths it gave a text that is not
 * their own and that another file can see (`seenByOthers`), and before every lint at another path
 * it gives each of them its own text again (a probe's path, with no file on disk, an empty
 * module): so every lint sees the package's own text at every path but its own, whatever was
 * linted before.
 */
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";

import { ESLint } from "eslint";
import ts from "typescript";
import tseslint from "typescript-eslint";

import { WEB } from "./program";

/** The text a probe's path is left with: an empty module, which declares nothing global. */
export const EMPTY = "export {};\n";

/** The text the project service is to hold at a path: the file's own, or an empty module. */
export function ownText(file: string): string {
  const at = path.resolve(WEB, file);
  return existsSync(at) ? readFileSync(at, "utf8") : EMPTY;
}

/** Whether a text at a path can change what another file sees: it is a file of the package given
 * other text, or a probe (a path with no file) that declares into the global scope (a script, or a
 * module with `declare global` or `declare module`). A probe that is a module and declares nothing
 * global is seen by no other file, so it is left as it is. */
export function seenByOthers(file: string, code: string): boolean {
  if (existsSync(path.resolve(WEB, file))) {
    return true;
  }
  const source = ts.createSourceFile(file, code, ts.ScriptTarget.Latest, false, file.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
  return !ts.isExternalModule(source) || /\bdeclare\s+(global|module)\b/u.test(code);
}

/** The linter that puts a file's own text back: the project service is one for the whole process
 * (typescript-eslint's), so a linter with no rule and no configuration file reaches the same one. */
const RESTORER = new ESLint({
  cwd: WEB,
  overrideConfigFile: true,
  overrideConfig: [{ files: ["**/*.ts", "**/*.tsx", "**/*.mts"], languageOptions: { parser: tseslint.parser, parserOptions: { projectService: true, tsconfigRootDir: WEB } } }],
});

/** The paths whose text in the project service is not their own, and another file can see. */
const dirty = new Set<string>();

/** Put a path's own text back in the project service: a lint by `RESTORER`, which runs no rule
 * (the parse alone gives the service the text), so it costs a parse, not a lint. */
async function restore(file: string): Promise<void> {
  const [restored] = await RESTORER.lintText(ownText(file), { filePath: file });
  const fatal = (restored?.messages ?? []).filter((message) => message.fatal === true).map((message) => message.message);
  if (fatal.length > 0) {
    throw new Error(`${path.relative(WEB, file)}'s own text does not parse: ${fatal.join("; ")}`);
  }
}

export class Linter extends ESLint {
  /** Lint a text as the file at `filePath`; before it, put back every other path's own text. */
  override async lintText(code: string, options?: Parameters<ESLint["lintText"]>[1]): Promise<ESLint.LintResult[]> {
    const at = options?.filePath === undefined ? undefined : path.resolve(WEB, options.filePath);
    for (const file of [...dirty]) {
      if (file !== at) {
        dirty.delete(file);
        await restore(file);
      }
    }
    const results = await super.lintText(code, options);
    if (at !== undefined) {
      if (code !== ownText(at) && seenByOthers(at, code)) {
        dirty.add(at);
      } else {
        dirty.delete(at);
      }
    }
    return results;
  }

  /** The paths whose text is not their own, now. */
  static dirty(): string[] {
    return [...dirty].map((file) => path.relative(WEB, file));
  }
}
