// @ts-check
/**
 * What the DOM library declares of each of the page's types (D423), read through the type checker,
 * and the classification of every member of them: a **channel** (`PAGE_TYPES`' list: refused), a
 * **reacher** (`REACHERS`: a member that reaches another page object, refused), an **object** (a
 * member whose type is or gives a page object and that is allowed as the object of a member access:
 * `window.document`, `document.location`, ...) or **allowed** (the rest). The checked-in fixture
 * `tests/fixtures/page-members.json` holds the last two lists; `tests/gates/page-types.test.ts` fails
 * when the library declares a member the fixture does not know (a TypeScript upgrade), when a member
 * that gives a page object is only "allowed", and when the fixture names a member that is gone.
 * `node scripts/page-members.mjs --write` brings the fixture up to date: it adds every new member
 * that gives no page object to "allowed" and refuses (exit 1, listing them) every new member that
 * does, which a person classifies in `scripts/page-objects.mjs` or in the fixture's "object" lists.
 */
import { readFileSync, writeFileSync } from "node:fs";
import path from "node:path";

import ts from "typescript";

import { EVERY, PAGE_TYPES, REACHERS, pageTypeOf } from "./page-objects.mjs";

const WEB = path.join(import.meta.dirname, "..");

export const FIXTURE = path.join(WEB, "tests/fixtures/page-members.json");

const ROOT = "/virtual/page-types.ts";

/** The app's compiler options (`tsconfig.app.json`, as `tsc -p` reads them). */
function appOptions() {
  const parsed = ts.getParsedCommandLineOfConfigFile(path.join(WEB, "tsconfig.app.json"), {}, {
    ...ts.sys,
    onUnRecoverableConfigFileDiagnostic: (diagnostic) => {
      throw new Error(ts.flattenDiagnosticMessageText(diagnostic.messageText, "\n"));
    },
  });
  if (parsed === undefined) {
    throw new Error("tsconfig.app.json is unreadable");
  }
  return { ...parsed.options, noEmit: true, types: [] };
}

/** A program of the app's options with one virtual file that declares a value of each page type.
 * @returns {{ program: import("typescript").Program, checker: import("typescript").TypeChecker, file: import("typescript").SourceFile, names: string[] }} */
export function domProgram() {
  const names = [...PAGE_TYPES.keys()].filter((name) => name !== "globalThis");
  const text = `${names.map((name, index) => `declare const v${String(index)}: ${name};`).join("\n")}\nexport {};\n`;
  const options = appOptions();
  const host = ts.createCompilerHost(options);
  const getSourceFile = host.getSourceFile.bind(host);
  host.getSourceFile = (file, languageVersion, ...rest) => (file === ROOT ? ts.createSourceFile(file, text, languageVersion) : getSourceFile(file, languageVersion, ...rest));
  const fileExists = host.fileExists.bind(host);
  host.fileExists = (file) => file === ROOT || fileExists(file);
  const program = ts.createProgram([ROOT], options, host);
  const file = program.getSourceFile(ROOT);
  if (file === undefined) {
    throw new Error("the virtual file is missing");
  }
  return { program, checker: program.getTypeChecker(), file, names };
}

/** Every member each page type declares (inherited ones included), with what it gives: the name of
 * the page type its value (or its call's result) is or holds, or `null`.
 * @returns {Record<string, Record<string, string | null>>} */
export function liveMembers() {
  const { program, checker, file, names } = domProgram();
  /** @type {Record<string, Record<string, string | null>>} */
  const found = {};
  const declarations = file.statements.filter(ts.isVariableStatement).map((statement) => statement.declarationList.declarations[0]);
  names.forEach((name, index) => {
    const declaration = declarations[index];
    if (declaration === undefined) {
      throw new Error(`no declaration for ${name}`);
    }
    /** @type {Record<string, string | null>} */
    const members = {};
    for (const property of checker.getPropertiesOfType(checker.getTypeAtLocation(declaration.name))) {
      const type = checker.getTypeOfSymbolAtLocation(property, declaration);
      const gives = [type, ...type.getCallSignatures().map((signature) => signature.getReturnType()), ...type.getConstructSignatures().map((signature) => signature.getReturnType())];
      members[property.getName()] = gives.map((one) => pageTypeOf(checker, program, one)).find((found_) => found_ !== null) ?? null;
    }
    found[name] = members;
  });
  return found;
}

/** @typedef {Record<string, { object: string[], allowed: string[] }>} Fixture */

/** The fixture as checked in.
 * @returns {Fixture} */
export function readFixture() {
  const read = /** @type {unknown} */ (JSON.parse(readFileSync(FIXTURE, "utf8")));
  return /** @type {Fixture} */ (read);
}

/** How a member of a page type is classified: `channel` (refused), `reacher` (refused where it gives
 * a page object), `object` (gives a page object, allowed as the object of a member access),
 * `allowed`, or `null` (the fixture does not know it).
 * @param {string} type @param {string} name @param {Fixture} fixture
 * @returns {"channel" | "reacher" | "object" | "allowed" | null} */
export function classify(type, name, fixture) {
  const channels = PAGE_TYPES.get(type);
  if (channels === EVERY || (Array.isArray(channels) && channels.includes(name))) {
    return "channel";
  }
  if (REACHERS.has(name)) {
    return "reacher";
  }
  const known = fixture[type];
  return known?.object.includes(name) === true ? "object" : known?.allowed.includes(name) === true ? "allowed" : null;
}

if (process.argv[1] === import.meta.filename && process.argv.includes("--write")) {
  const live = liveMembers();
  /** @type {Fixture} */
  const old = (() => {
    try {
      return readFixture();
    } catch {
      return {};
    }
  })();
  /** @type {Fixture} */
  const next = {};
  /** @type {string[]} */
  const unclassified = [];
  for (const [type, members] of Object.entries(live)) {
    /** @type {{ object: string[], allowed: string[] }} */
    const entry = { object: [], allowed: [] };
    for (const [name, gives] of Object.entries(members)) {
      const how = classify(type, name, old);
      if (how === "object") {
        entry.object.push(name);
      } else if (how === "allowed" || (how === null && gives === null)) {
        entry.allowed.push(name);
      } else if (how === null) {
        unclassified.push(`${type}.${name} gives ${String(gives)}`);
      }
    }
    next[type] = { object: entry.object.sort(), allowed: entry.allowed.sort() };
  }
  if (unclassified.length > 0) {
    process.stderr.write(`Classify these members, which give a page object, as a channel (PAGE_TYPES), a reacher (REACHERS) or an object (the fixture's "object" list):\n${unclassified.join("\n")}\n`);
    process.exit(1);
  }
  writeFileSync(FIXTURE, `${JSON.stringify(next, null, 1)}\n`);
}
