// @ts-check
/**
 * The display table (D427): its one reader, and SPEC.md's Appendix C, generated from it.
 *
 * `src/display/rows.ts` is the table's one source, and it is read here, transpiled by TypeScript and
 * evaluated in a context of its own (`node:vm`, which has no `require` and no `import`, so a table
 * that imports anything fails to load): its exports are checked to be the table's shapes and
 * copied out of that context before anything uses them. The display gate
 * (`tests/gates/display.ts`) and the build's gate (`plugins/gate.ts`, through `vite.config.ts`)
 * read it through `readTable`, and so does the generator.
 *
 * `npm run generate` writes the appendix between its two markers in SPEC.md; `npm run
 * generate:check` (CI's `web` job) fails, writing nothing, while SPEC.md's text differs from the
 * table's rendering. **Ids are pinned in a file of their own** (`scripts/display-ids.json`: every id
 * the table ever held, rows and retired alike), and both modes fail while the file and the table
 * disagree: a row that is in the file and is neither a row nor in `RETIRED` (with a reason that is
 * not blank), and a row the file does not list. Deleting a row and regenerating the appendix in
 * one change therefore still fails, until the file is edited too, which is a line of its own in
 * review: what this checks is that the removal is a visible edit, not that nobody makes it.
 */
import { createHash } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

import ts from "typescript";

const WEB = path.dirname(path.dirname(fileURLToPath(import.meta.url)));

/** The table's file, relative to a package's root (the web package's, or a fixture's). */
export const ROWS_FILE = "src/display/rows.ts";

/** The ids the table ever held, relative to a package's root (the web package's, or a fixture's). */
export const IDS_FILE = "scripts/display-ids.json";

/** The document whose Appendix C is the table's rendering. */
export const SPEC = path.join(WEB, "..", "SPEC.md");

export const BEGIN =
  "<!-- D427's table: generated from web/src/display/rows.ts by web/scripts/display-table.mjs (npm run generate). Edit the table there, never here. -->";
export const END = "<!-- End of D427's generated table. -->";

/** A table that is not the table's shape (or that does not load). */
export class TableError extends Error {}

/**
 * @typedef {{ id: string, basis: string, must: string, screens: string[], spec: string[], owner: string, module: string, test: string }} Row
 * @typedef {{ module: string, reason: string }} Exemption
 * @typedef {{ id: string, reason: string }} Retired
 * @typedef {{ what: string, spec: string[] }} NotRow
 * @typedef {{ owners: string[], screens: string[], bases: string[], rows: Row[], exempt: Exemption[], helpers: Exemption[], retired: Retired[], notRows: NotRow[] }} Table
 */

const ROW_KEYS = ["id", "basis", "must", "screens", "spec", "owner", "module", "test"];

/** A row's id. */
const ID = /^R\d+[a-z]?$/;

/**
 * @param {unknown} value
 * @returns {value is Record<string, unknown>}
 */
function isRecord(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * A string that renders in one Markdown table cell: no `|`, no line break, no `<!--`.
 * @param {unknown} value
 * @param {string} where
 * @param {boolean} [empty] whether the empty string is allowed
 * @returns {string}
 */
function cell(value, where, empty = false) {
  if (typeof value !== "string" || (!empty && value.trim() === "")) {
    throw new TableError(`${where} is not a${empty ? "" : " non-blank"} string`);
  }
  if (/[|\r\n]|<!--/.test(value)) {
    throw new TableError(`${where} holds a character a table cell cannot (a bar, a line break or a comment)`);
  }
  return value;
}

/**
 * @param {unknown} value
 * @param {string} where
 * @returns {string[]}
 */
function cells(value, where) {
  if (!Array.isArray(value)) {
    throw new TableError(`${where} is not an array`);
  }
  return value.map((member, index) => cell(member, `${where}[${String(index)}]`));
}

/**
 * Check the table's exports and copy them out of the context they were made in.
 * @param {unknown} exported
 * @returns {Table}
 */
export function checkTable(exported) {
  if (!isRecord(exported)) {
    throw new TableError("the table's module exports nothing");
  }
  const owners = cells(exported["OWNERS"], "OWNERS");
  const screens = cells(exported["SCREENS"], "SCREENS");
  const bases = cells(exported["BASES"], "BASES");
  const rowsValue = exported["ROWS"];
  if (!Array.isArray(rowsValue)) {
    throw new TableError("ROWS is not an array");
  }
  /** @type {Row[]} */
  const rows = rowsValue.map((value, index) => {
    const where = `ROWS[${String(index)}]`;
    if (!isRecord(value)) {
      throw new TableError(`${where} is not an object`);
    }
    const keys = Object.keys(value).sort();
    if (keys.join() !== [...ROW_KEYS].sort().join()) {
      throw new TableError(`${where} has the members ${keys.join(", ")}, not ${ROW_KEYS.join(", ")}`);
    }
    const basis = cell(value["basis"], `${where}.basis`);
    if (!bases.includes(basis)) {
      throw new TableError(`${where}.basis is not one of BASES`);
    }
    const rowScreens = cells(value["screens"], `${where}.screens`);
    for (const screen of rowScreens) {
      if (!screens.includes(screen)) {
        throw new TableError(`${where}.screens names ${JSON.stringify(screen)}, which is not one of SCREENS`);
      }
    }
    const spec = cells(value["spec"], `${where}.spec`);
    if (spec.length === 0) {
      throw new TableError(`${where}.spec cites nothing`);
    }
    return {
      id: cell(value["id"], `${where}.id`),
      basis,
      must: cell(value["must"], `${where}.must`),
      screens: rowScreens,
      spec,
      owner: cell(value["owner"], `${where}.owner`),
      module: cell(value["module"], `${where}.module`, true),
      test: cell(value["test"], `${where}.test`, true),
    };
  });
  /**
   * @param {string} name
   * @returns {Exemption[]}
   */
  const modules = (name) => {
    const value = exported[name];
    if (!Array.isArray(value)) {
      throw new TableError(`${name} is not an array`);
    }
    return value.map((member, index) => {
      const where = `${name}[${String(index)}]`;
      if (!isRecord(member) || Object.keys(member).sort().join() !== "module,reason") {
        throw new TableError(`${where} is not {module, reason}`);
      }
      return { module: cell(member["module"], `${where}.module`), reason: cell(member["reason"], `${where}.reason`) };
    });
  };
  const exempt = modules("EXEMPT");
  const helpers = modules("HELPERS");
  const retiredValue = exported["RETIRED"];
  if (!Array.isArray(retiredValue)) {
    throw new TableError("RETIRED is not an array");
  }
  const retired = retiredValue.map((value, index) => {
    const where = `RETIRED[${String(index)}]`;
    if (!isRecord(value) || Object.keys(value).sort().join() !== "id,reason") {
      throw new TableError(`${where} is not {id, reason}`);
    }
    const id = cell(value["id"], `${where}.id`);
    if (!ID.test(id)) {
      throw new TableError(`${where}.id is not an id of the form R<number>, with an optional letter`);
    }
    return { id, reason: cell(value["reason"], `${where}.reason`) };
  });
  const notRowsValue = exported["NOT_ROWS"];
  if (!Array.isArray(notRowsValue)) {
    throw new TableError("NOT_ROWS is not an array");
  }
  const notRows = notRowsValue.map((value, index) => {
    const where = `NOT_ROWS[${String(index)}]`;
    if (!isRecord(value) || Object.keys(value).sort().join() !== "spec,what") {
      throw new TableError(`${where} is not {what, spec}`);
    }
    return { what: cell(value["what"], `${where}.what`), spec: cells(value["spec"], `${where}.spec`) };
  });
  return { owners, screens, bases, rows, exempt, helpers, retired, notRows };
}

/**
 * Read the table of the package at `root` (the web package's by default).
 * @param {string} [root]
 * @returns {Table}
 */
export function readTable(root = WEB) {
  const file = path.join(root, ROWS_FILE);
  let source;
  try {
    source = readFileSync(file, "utf8");
  } catch {
    throw new TableError(`${ROWS_FILE} cannot be read`);
  }
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
    fileName: file,
  });
  /** @type {{ exports: Record<string, unknown> }} */
  const context = { exports: {} };
  try {
    vm.runInNewContext(outputText, context, { filename: file, timeout: 1000 });
  } catch (error) {
    // The context has its own `Error`, so `instanceof` would not see a SyntaxError of the table.
    const said = typeof error === "object" && error !== null && "message" in error && typeof error.message === "string" ? error.message : "it threw";
    throw new TableError(`${ROWS_FILE} does not load: ${said}`);
  }
  return checkTable(context.exports);
}

/**
 * @param {string} text
 * @returns {string}
 */
function code(text) {
  return text === "" ? "" : `\`${text}\``;
}

/**
 * The ids' checksum: the first 16 hex digits of the SHA-256 of the ids, in order, one per line.
 * @param {readonly { id: string }[]} rows
 * @returns {string}
 */
export function idsChecksum(rows) {
  return createHash("sha256").update(rows.map((row) => `${row.id}\n`).join("")).digest("hex").slice(0, 16);
}

/** The appendix's table header (its columns are the rows' members). */
const HEADER = "| Row | Basis | What the page must show | Screens | Spec | Owner | Module | Test |";
const SEPARATOR = "|---|---|---|---|---|---|---|---|";
const RETIRED_HEAD = "**Retired rows** (each left the table with a stated reason, and its id is never used again):";

/**
 * @param {Exemption[]} modules
 * @returns {string[]}
 */
function moduleLines(modules) {
  return ["| Module | Reason |", "|---|---|", ...modules.map((exemption) => `| ${code(exemption.module)} | ${exemption.reason} |`)];
}

/**
 * The appendix's text between its markers.
 * @param {Table} table
 * @returns {string}
 */
export function renderAppendix(table) {
  const order = table.owners.filter((owner) => owner !== "server");
  const counts = table.owners.map((owner) => `${owner} ${String(table.rows.filter((row) => row.owner === owner).length)}`);
  const lines = [
    BEGIN,
    "",
    `The table has ${String(table.rows.length)} rows (by owner: ${counts.join(", ")}), with the ids' checksum ${code(idsChecksum(table.rows))} (the first 16 hex digits of the SHA-256 of the ids in order, one per line; \`web/scripts/display-ids.json\` lists every id the table ever held). Owners land in this order: ${order.join(", ")}; a built row of an owner fails the gate while an owner before it has a row not built. \`server\` rows are the server's and name no module. A row's basis is \`spec MUST\` (a MUST or MUST NOT of the spec states the duty of what is shown), \`spec statement\` (the cited section or decision states it, in no MUST of its own) or \`derived\` (the spec states it for another reader or implies it, or D427 decides it, the derivation in the row).`,
    "",
    HEADER,
    SEPARATOR,
    ...table.rows.map(
      (row) => `| ${row.id} | ${row.basis} | ${row.must} | ${row.screens.join(", ")} | ${row.spec.join(", ")} | ${row.owner} | ${code(row.module)} | ${code(row.test)} |`,
    ),
    "",
    RETIRED_HEAD,
    "",
    ...(table.retired.length === 0 ? ["None."] : table.retired.map((entry) => `- ${entry.id}: ${entry.reason}`)),
    "",
    "**Exempt modules** (today's screens, built before the table, which show fixed words; **reviewed, not checked**: the build's gate lets each into a bundle by name alone, and nothing reads what it does):",
    "",
    ...moduleLines(table.exempt),
    "",
    "**Helpers** (modules that are no row's and no screen; **reviewed, not checked**, like the exemptions; every module of a bundle under `src/` outside `src/api/` is a row's module, an exemption or a helper, or the build fails):",
    "",
    ...moduleLines(table.helpers),
    "",
    "**Not rows** (the server's alone, listed so that none is forgotten):",
    "",
    ...table.notRows.map((notRow) => `- ${notRow.what.replace(/\.$/, "")} (${notRow.spec.join(", ")}).`),
    "",
    END,
  ];
  return lines.join("\n");
}

/**
 * The ids `scripts/display-ids.json` of the package at `root` lists.
 * @param {string} [root]
 * @returns {string[]}
 */
export function readIds(root = WEB) {
  const file = path.join(root, IDS_FILE);
  /** @type {unknown} */
  let parsed;
  try {
    parsed = JSON.parse(readFileSync(file, "utf8"));
  } catch {
    throw new TableError(`${IDS_FILE} cannot be read as JSON`);
  }
  if (!isRecord(parsed) || Object.keys(parsed).join() !== "ids" || !Array.isArray(parsed["ids"])) {
    throw new TableError(`${IDS_FILE} is not {"ids": [...]}`);
  }
  const ids = parsed["ids"].map((id, index) => {
    if (typeof id !== "string" || !ID.test(id)) {
      throw new TableError(`${IDS_FILE}'s ids[${String(index)}] is not an id of the form R<number>, with an optional letter`);
    }
    return id;
  });
  if (new Set(ids).size !== ids.length) {
    throw new TableError(`${IDS_FILE} lists an id twice`);
  }
  return ids;
}

/**
 * Why the table cannot stand beside the ids the file lists, or null: every listed id is a row or is
 * retired (with the reason `checkTable` made non-blank), every row is listed, no retired id is a
 * row, and every retired id is listed.
 * @param {readonly string[]} ids
 * @param {Table} table
 * @returns {string | null}
 */
export function idsProblem(ids, table) {
  const listed = new Set(ids);
  const rows = new Set(table.rows.map((row) => row.id));
  const retired = new Set(table.retired.map((entry) => entry.id));
  for (const id of retired) {
    if (rows.has(id)) {
      return `${id} is both a row and in RETIRED: a retired id is never used again`;
    }
    if (!listed.has(id)) {
      return `${id} is in RETIRED and ${IDS_FILE} does not list it: list it there, where every id the table ever held stays`;
    }
  }
  for (const id of ids) {
    if (!rows.has(id) && !retired.has(id)) {
      return `${id} is listed in ${IDS_FILE} and is neither a row nor in RETIRED: a row leaves the table only with its reason (or a reviewed edit of ${IDS_FILE} itself)`;
    }
  }
  for (const id of rows) {
    if (!listed.has(id)) {
      return `${id} is a row that ${IDS_FILE} does not list: add it there, a line of its own for review`;
    }
  }
  return null;
}

/**
 * SPEC.md's text with the appendix put between its markers.
 * @param {string} spec
 * @param {string} appendix
 * @returns {string}
 */
export function spliceAppendix(spec, appendix) {
  const begin = spec.indexOf(BEGIN);
  const end = spec.indexOf(END);
  if (begin === -1 || end === -1 || end < begin || spec.indexOf(BEGIN, begin + 1) !== -1 || spec.indexOf(END, end + 1) !== -1) {
    throw new TableError("SPEC.md does not hold D427's two markers once each, in order");
  }
  return spec.slice(0, begin) + appendix + spec.slice(end + END.length);
}

/**
 * @param {string[]} argv
 * @param {(line: string) => void} say
 * @param {{ root: string, spec: string }} [files] the package whose table is read and the document written
 * @returns {number}
 */
export function main(argv, say, files = { root: WEB, spec: SPEC }) {
  const check = argv.length === 1 && argv[0] === "--check";
  if (argv.length > 1 || (argv.length === 1 && !check)) {
    say("usage: display-table.mjs [--check]");
    return 2;
  }
  let made;
  const current = readFileSync(files.spec, "utf8");
  try {
    const table = readTable(files.root);
    const problem = idsProblem(readIds(files.root), table);
    if (problem !== null) {
      say(`display-table: ${problem}`);
      return 1;
    }
    made = spliceAppendix(current, renderAppendix(table));
  } catch (error) {
    if (error instanceof TableError) {
      say(`display-table: ${error.message}`);
      return 1;
    }
    throw error;
  }
  if (made === current) {
    return 0;
  }
  if (check) {
    say("display-table: SPEC.md's Appendix C is stale: run npm run generate");
    return 1;
  }
  writeFileSync(files.spec, made);
  say("display-table: wrote SPEC.md's Appendix C");
  return 0;
}

if (process.argv[1] !== undefined && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  process.exitCode = main(process.argv.slice(2), (line) => {
    process.stdout.write(`${line}\n`);
  });
}
