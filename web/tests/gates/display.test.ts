/**
 * The display table's gate (D427) on this package, SPEC.md's Appendix C and the ids file held to the
 * table, and the gate's own tests: every fixture of `display-fixtures.ts` fails by exactly the
 * rules it names (a tree that must pass, by none), every owner built alone fails the order but the
 * first, and every mutant of the gate (`MUTANTS`) is killed by the fixtures `KILLS` names for it or
 * is shown equivalent (`EQUIVALENT`): one table, which the pull request's table is generated from.
 *
 * **Cost.** Each fixture is a tiny mock package (a dozen files) in a temporary directory, whose
 * `node_modules` holds a link to this package's vitest alone; the gate's child run is of the
 * fixture's `unit` project over its built rows' test files and the canaries, about a second each,
 * run several at a time. A `sharedRun` fixture edits no test, so it shares the baseline's run
 * (`D427_FULL=1` gives each its own: the check that the sharing is sound). Each fixture runs once;
 * the mutants are judged on that run's results (`GateOptions.results`), but M15, M46 and M86, whose point
 * is the run itself, and the equivalent that is about the run.
 */
import { mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { afterAll, beforeAll, describe, expect, it } from "vitest";

import {
  BEGIN,
  checkTable,
  END,
  IDS_FILE,
  idsChecksum,
  idsProblem,
  main,
  readIds,
  readTable,
  renderAppendix,
  ROWS_FILE,
  SPEC,
  spliceAppendix,
  TableError,
} from "../../scripts/display-table.mjs";
import { gate, MUTANTS, type Mutant, type Rule, STALE_RESULTS, type Verdict } from "./display";
import { ALL_FIXTURES, BASE, EQUIVALENT, KILLS, type Tree } from "./display-fixtures";

const WEB = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const LONG = 600_000;
const FULL = process.env["D427_FULL"] === "1";

const rulesOf = (verdict: Verdict): Rule[] => [...new Set(verdict.failures.map((failure) => failure.rule))].sort();

describe("the display table's gate on this package (D427)", () => {
  it(
    "passes: no built row lacks its module's value or its test, the owners are in order, and the canaries hold",
    async () => {
      const verdict = await gate(WEB);
      expect(verdict.failures).toEqual([]);
      expect(verdict.results?.length).toBeGreaterThanOrEqual(2);
    },
    LONG,
  );

  it("SPEC.md's Appendix C is the table's rendering, states the row count once, and the ids file is the table's ledger", () => {
    const spec = readFileSync(SPEC, "utf8");
    const table = readTable();
    expect(spliceAppendix(spec, renderAppendix(table))).toBe(spec);
    expect(spec.split(`The table has ${String(table.rows.length)} rows`).length).toBe(2);
    expect(spec).toContain(`checksum \`${idsChecksum(table.rows)}\``);
    expect(idsProblem(readIds(), table)).toBeNull();
    // A row retired with a reason stays in the ledger (D427: a row leaves the table only into RETIRED), so the ledger
    // is the rows' ids and the retired ids; a retirement with a reason must pass here, and a deletion with no reason
    // fails `idsProblem` above (the ids file is edited deliberately for that, a line of its own in review).
    expect([...readIds()].sort()).toEqual([...table.rows.map((row) => row.id), ...table.retired.map((entry) => entry.id)].sort());
  });

  it("generate:check fails while the appendix is stale, and generate writes it", () => {
    const scratch = mkdtempSync(path.join(os.tmpdir(), "aibi-display-table-"));
    try {
      const spec = path.join(scratch, "SPEC.md");
      const current = readFileSync(SPEC, "utf8");
      const said: string[] = [];
      const say = (line: string): void => {
        said.push(line);
      };
      // An appendix not written yet (the two markers, nothing between): stale, and generate writes it.
      writeFileSync(spec, spliceAppendix(current, `${BEGIN}\n${END}`));
      expect(main(["--check"], say, { root: WEB, spec })).toBe(1);
      expect(said.join("\n")).toContain("stale");
      expect(main([], say, { root: WEB, spec })).toBe(0);
      expect(readFileSync(spec, "utf8")).toBe(current);
      expect(main(["--check"], say, { root: WEB, spec })).toBe(0);
      // Edited text between the markers: stale, and regenerated.
      writeFileSync(spec, spliceAppendix(current, `${BEGIN}\nstale\n${END}`));
      expect(main(["--check"], say, { root: WEB, spec })).toBe(1);
      expect(main([], say, { root: WEB, spec })).toBe(0);
      expect(readFileSync(spec, "utf8")).toBe(current);
      writeFileSync(spec, "no markers\n");
      expect(main(["--check"], say, { root: WEB, spec })).toBe(1);
    } finally {
      rmSync(scratch, { recursive: true, force: true });
    }
  });

  it("refuses a table that is not the table's shape", () => {
    const table = { OWNERS: ["a"], SCREENS: ["all"], BASES: ["spec MUST"], EXEMPT: [], HELPERS: [], RETIRED: [], NOT_ROWS: [] };
    const good = { id: "R01", basis: "spec MUST", must: "m", screens: ["all"], spec: ["§8"], owner: "a", module: "", test: "" };
    expect(checkTable({ ...table, ROWS: [good] }).rows).toHaveLength(1);
    for (const bad of [
      { ...good, extra: 1 },
      { ...good, basis: "MUST" },
      { ...good, level: "MUST" },
      { ...good, screens: ["everywhere"] },
      { ...good, spec: [] },
      { ...good, must: "a | b" },
      { ...good, must: "two\nlines" },
      { ...good, must: "   " },
      { ...good, id: "" },
    ]) {
      expect(() => checkTable({ ...table, ROWS: [bad] })).toThrow(TableError);
    }
    expect(() => checkTable({ ...table, ROWS: {} })).toThrow(TableError);
    const listed = { module: "src/a.ts", reason: "r" };
    expect(checkTable({ ...table, ROWS: [good], EXEMPT: [listed], HELPERS: [listed], RETIRED: [{ id: "R02", reason: "why" }] }).retired).toHaveLength(1);
    for (const bad of [
      { EXEMPT: [{ module: "src/a.ts" }] },
      { EXEMPT: [{ ...listed, imports: [] }] },
      { EXEMPT: [{ ...listed, reason: " " }] },
      { HELPERS: [{ ...listed, extra: 1 }] },
      { RETIRED: [{ id: "R2x1", reason: "why" }] },
      { RETIRED: [{ id: "R02", reason: "" }] },
      { RETIRED: [{ id: "R02", reason: " " }] },
      { RETIRED: [{ id: "R02" }] },
    ]) {
      expect(() => checkTable({ ...table, ROWS: [good], ...bad })).toThrow(TableError);
    }
  });

  describe("the ids file: a row leaves the table only by a visible edit of a file of its own", () => {
    const scratch = mkdtempSync(path.join(os.tmpdir(), "aibi-display-ids-"));
    afterAll(() => {
      rmSync(scratch, { recursive: true, force: true });
    });
    let made = 0;
    const rowsText = readFileSync(path.join(WEB, ROWS_FILE), "utf8");
    const idsText = readFileSync(path.join(WEB, IDS_FILE), "utf8");
    const specText = readFileSync(SPEC, "utf8");

    /** The table's text without the row `id` (its block of the array). */
    const without = (text: string, id: string): string => {
      const at = text.indexOf(`    id: "${id}",`);
      const from = text.lastIndexOf("  {\n", at);
      const to = text.indexOf("\n  },\n", at) + "\n  },\n".length;
      expect(at).toBeGreaterThan(0);
      return text.slice(0, from) + text.slice(to);
    };

    /** A package with these two files and a SPEC.md; the result of `main` in the given mode. */
    const run = (rows: string, ids: string, spec: string, mode: string[]): { code: number; said: string; spec: string } => {
      made += 1;
      const root = path.join(scratch, `p${String(made)}`);
      mkdirSync(path.join(root, "src", "display"), { recursive: true });
      mkdirSync(path.join(root, "scripts"), { recursive: true });
      writeFileSync(path.join(root, ROWS_FILE), rows);
      writeFileSync(path.join(root, IDS_FILE), ids);
      const file = path.join(root, "SPEC.md");
      writeFileSync(file, spec);
      const said: string[] = [];
      const code = main(mode, (line) => said.push(line), { root, spec: file });
      return { code, said: said.join("\n"), spec: readFileSync(file, "utf8") };
    };

    it("fails a row deleted, in both modes, and still fails with the appendix regenerated in the same change", () => {
      const cut = without(rowsText, "R45");
      expect(run(cut, idsText, specText, ["--check"])).toMatchObject({ code: 1 });
      expect(run(cut, idsText, specText, ["--check"]).said).toContain("R45 is listed in scripts/display-ids.json and is neither a row nor in RETIRED");
      const generated = run(cut, idsText, specText, []);
      expect(generated).toMatchObject({ code: 1, spec: specText });
      // The same change with Appendix C regenerated by hand: the file is what still fails.
      const regenerated = spliceAppendix(specText, renderAppendix(checkTableOf(cut)));
      expect(run(cut, idsText, regenerated, ["--check"]).code).toBe(1);
      // Edit the ids file too: a visible edit of a file of its own, which then passes.
      const edited = JSON.stringify({ ids: readIds().filter((id) => id !== "R45") }, null, 2) + "\n";
      expect(run(cut, edited, regenerated, ["--check"]).code).toBe(0);
    });

    it("passes a row retired with a reason, and fails a blank reason, a retired id that is a row, an id not listed", () => {
      const retired = (reason: string): string => without(rowsText, "R45").replace("export const RETIRED: readonly Retired[] = [];", `export const RETIRED: readonly Retired[] = [{ id: "R45", reason: ${JSON.stringify(reason)} }];`);
      const spec = spliceAppendix(specText, renderAppendix(checkTableOf(retired("folded into R46"))));
      expect(run(retired("folded into R46"), idsText, spec, ["--check"]).code).toBe(0);
      expect(run(retired(" "), idsText, spec, ["--check"]).said).toContain("not a non-blank string");
      expect(run(rowsText.replace("export const RETIRED: readonly Retired[] = [];", 'export const RETIRED: readonly Retired[] = [{ id: "R45", reason: "no" }];'), idsText, specText, ["--check"]).said).toContain("both a row and in RETIRED");
      const unlisted = JSON.stringify({ ids: readIds().filter((id) => id !== "R45") }, null, 2) + "\n";
      expect(run(retired("folded"), unlisted, spec, ["--check"]).said).toContain("does not list it");
    });

    it("fails a row the ids file does not list, and a file that is not the ledger's shape", () => {
      const added = rowsText.replace("export const ROWS: readonly Row[] = [\n", `export const ROWS: readonly Row[] = [\n  { id: "R999", basis: "derived", must: "m", screens: ["all"], spec: ["§8"], owner: "server", module: "", test: "" },\n`);
      expect(run(added, idsText, specText, ["--check"]).said).toContain("R999 is a row that scripts/display-ids.json does not list");
      for (const bad of ["not json", "{}", '{"ids": "R01"}', '{"ids": ["R01", "R01"]}', '{"ids": ["x"]}', '{"ids": [], "more": 1}']) {
        expect(run(rowsText, bad, specText, ["--check"]).code).toBe(1);
      }
    });
  });
});

const checkTableOf = (text: string): ReturnType<typeof readTable> => {
  const root = mkdtempSync(path.join(os.tmpdir(), "aibi-display-read-"));
  try {
    mkdirSync(path.join(root, "src", "display"), { recursive: true });
    writeFileSync(path.join(root, ROWS_FILE), text);
    return readTable(root);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
};

describe("the gate's own fixtures and mutants", () => {
  const scratch = mkdtempSync(path.join(os.tmpdir(), "aibi-d427-fixtures-"));
  const roots = new Map<string, string>();
  const verdicts = new Map<string, Verdict>();
  const expected = new Map<string, readonly Rule[]>([["base", []]]);
  // A module that throws, which NODE_OPTIONS of the parent would load into the child (M46).
  const thrower = path.join(scratch, "throw.cjs");

  const treeOf = (edit: ((tree: Tree) => void) | null): Tree => {
    const tree: Tree = new Map(Object.entries(BASE));
    edit?.(tree);
    return tree;
  };

  const materialise = (name: string, tree: Tree, links: readonly (readonly [string, string])[], vitest: boolean): string => {
    const root = path.join(scratch, name);
    for (const [file, text] of tree) {
      mkdirSync(path.dirname(path.join(root, file)), { recursive: true });
      writeFileSync(path.join(root, file), text);
    }
    for (const [link, target] of links) {
      symlinkSync(path.join(root, target), path.join(root, link), "dir");
    }
    if (vitest) {
      mkdirSync(path.join(root, "node_modules"), { recursive: true });
      symlinkSync(path.join(WEB, "node_modules", "vitest"), path.join(root, "node_modules", "vitest"), "dir");
    }
    return realpathSync(root);
  };

  const verdictOf = (name: string): Verdict => {
    const verdict = verdicts.get(name);
    if (verdict === undefined) {
      throw new Error(`no verdict for ${name}`);
    }
    return verdict;
  };

  const rootOf = (name: string): string => {
    const root = roots.get(name);
    if (root === undefined) {
      throw new Error(`no fixture ${name}`);
    }
    return root;
  };

  const pool = async <T>(items: readonly T[], size: number, each: (item: T) => Promise<void>): Promise<void> => {
    let next = 0;
    await Promise.all(
      Array.from({ length: size }, async () => {
        for (let item = items[next++]; item !== undefined; item = items[next++]) {
          await each(item);
        }
      }),
    );
  };

  beforeAll(async () => {
    writeFileSync(thrower, 'throw new Error("NODE_OPTIONS of the parent reached the child run");\n');
    const size = Math.max(2, Math.min(8, os.availableParallelism() - 1));
    const work: { name: string; sharedRun: boolean }[] = [];
    roots.set("base", materialise("base", treeOf(null), [], true));
    for (const fixture of ALL_FIXTURES) {
      const shared = fixture.sharedRun === true && !FULL;
      work.push({ name: fixture.name, sharedRun: shared });
      expected.set(fixture.name, fixture.expected);
      roots.set(fixture.name, materialise(fixture.name, treeOf(fixture.edit), fixture.links ?? [], !shared));
    }
    const base = await gate(rootOf("base"));
    verdicts.set("base", base);
    await pool(work, size, async ({ name, sharedRun }) => {
      verdicts.set(name, await gate(rootOf(name), sharedRun ? { results: base.results } : {}));
    });
  }, LONG);

  afterAll(() => {
    rmSync(scratch, { recursive: true, force: true });
  });

  it("the baseline passes, its rows built and tested", () => {
    const verdict = verdictOf("base");
    expect(verdict.failures).toEqual([]);
    expect(verdict.results?.filter((result) => /^R\d+:/.test(result.title) && result.status === "passed")).toHaveLength(6);
  });

  it("every fixture is an edit of the baseline, and its name is its own", () => {
    const names = new Set<string>(["base"]);
    for (const fixture of ALL_FIXTURES) {
      const tree = treeOf(fixture.edit);
      expect([...tree].sort().join("\n") !== Object.entries(BASE).sort().join("\n") || (fixture.links?.length ?? 0) > 0).toBe(true);
      expect(names.has(fixture.name)).toBe(false);
      names.add(fixture.name);
    }
  });

  it.each(ALL_FIXTURES.map((fixture) => [fixture.name, fixture.expected.length === 0 ? "nothing" : fixture.expected.join(", "), fixture.why] as const))("%s fails by %s (%s)", (name) => {
    const verdict = verdictOf(name);
    expect({ name, rules: rulesOf(verdict), failures: verdict.failures }).toMatchObject({ name, rules: [...(expected.get(name) ?? [])].sort() });
  });

  it("every mutant of the gate is named, with the fixtures that kill it or the reason it is equivalent", () => {
    expect([...Object.keys(KILLS), ...Object.keys(EQUIVALENT)].sort()).toEqual(Object.keys(MUTANTS).sort());
    for (const id of Object.keys(EQUIVALENT)) {
      expect(id in KILLS).toBe(false);
    }
    for (const killers of Object.values(KILLS)) {
      expect(killers.length).toBeGreaterThan(0);
      for (const killer of killers) {
        expect(expected.has(killer)).toBe(true);
      }
    }
    for (const entry of Object.values(EQUIVALENT)) {
      expect(entry.reason.length).toBeGreaterThan(20);
      for (const fixture of entry.fixtures) {
        expect(expected.has(fixture)).toBe(true);
      }
    }
    // Only the freshness check of the report is equivalent: every other mutant has a killer.
    expect(Object.keys(EQUIVALENT)).toEqual(["M40"]);
  });

  it.each(Object.entries(KILLS))(
    "mutant %s is killed by %j",
    async (mutant, killers) => {
      for (const killer of killers) {
        const root = rootOf(killer);
        let verdict: Verdict;
        if (mutant === "M15") {
          // The stale file: what a run of the baseline wrote, as if left in this fixture's tree.
          const raw = verdictOf("base").raw ?? "";
          writeFileSync(path.join(root, STALE_RESULTS), raw.split(rootOf("base")).join(root));
          verdict = await gate(root, { mutant });
        } else if (mutant === "M46" || mutant === "M86") {
          // A parent whose environment holds what the child must not inherit: for M46 `NODE_OPTIONS=--require <a throwing
          // module>`, for M86 a `VITEST_D427_PROBE` that the killer's test asserts is absent. The original gate's child
          // sees neither; the mutant's does.
          const variable = mutant === "M46" ? "NODE_OPTIONS" : "VITEST_D427_PROBE";
          const saved = process.env[variable];
          process.env[variable] = mutant === "M46" ? `--require ${thrower}` : "the parent's";
          try {
            const original = await gate(root);
            expect({ killer, rules: rulesOf(original) }).toEqual({ killer, rules: [...(expected.get(killer) ?? [])].sort() });
            verdict = await gate(root, { mutant });
          } finally {
            if (saved === undefined) {
              Reflect.deleteProperty(process.env, variable);
            } else {
              process.env[variable] = saved;
            }
          }
        } else {
          verdict = await gate(root, { mutant: mutant as Mutant, results: verdictOf(killer).results });
        }
        expect({ killer, rules: rulesOf(verdict) }).not.toEqual({ killer, rules: [...(expected.get(killer) ?? [])].sort() });
      }
    },
    LONG,
  );

  it.each(Object.entries(EQUIVALENT))(
    "mutant %s changes no verdict of its fixtures: it is equivalent",
    async (mutant, entry) => {
      for (const name of entry.fixtures) {
        const verdict = await gate(rootOf(name), { mutant: mutant as Mutant, ...(entry.sharesRun ? { results: verdictOf(name).results } : {}) });
        expect({ name, rules: rulesOf(verdict) }).toEqual({ name, rules: [...(expected.get(name) ?? [])].sort() });
      }
    },
    LONG,
  );
});
