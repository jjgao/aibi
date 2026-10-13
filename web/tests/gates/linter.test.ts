/**
 * The gates' linter leaves the project service clean (`linter.ts`, m8 of round 2 of #115's
 * review): a lint of other text at a real file of `src/api/` changes what every later lint of the
 * test file sees, unless the file's own text is put back. Held here by a probe whose lint depends
 * on `./api`'s types (an example mint of D425's: clean when `./api` is typed, every `no-unsafe-*`
 * rule when it is not), linted after a lint that empties `src/api/index.ts`: clean through the
 * gates' linter, poisoned through ESLint itself (the control, after which the file is put back).
 * That the lint tests do not depend on their order is run in CI under two shuffled orders
 * (`npm run test:shuffle`).
 */
import path from "node:path";

import { ESLint } from "eslint";
import { describe, expect, it } from "vitest";

import { EMPTY, Linter, ownText, seenByOthers } from "./linter";
import { WEB } from "./program";

const OPTIONS: ESLint.Options = {
  cwd: WEB,
  overrideConfig: { languageOptions: { parserOptions: { projectService: { allowDefaultProject: ["src/*.tsx"], defaultProject: "tsconfig.app.json" } } } },
};

const INDEX = path.join(WEB, "src/api/index.ts");
const PROBE = path.join(WEB, "src/probe.tsx");
const DEPENDS = "import { exchange, routes } from './api';\ntype Id = Parameters<typeof routes.reject>[0]['proposal'];\nconst ids: Id[] = [];\nexport const f = (): unknown[] => ids.map((proposal) => exchange(routes.reject({ dataset: 'd', proposal }), {}));\n";

const rules = async (linter: ESLint, code: string, file: string): Promise<string[]> => ((await linter.lintText(code, { filePath: file }))[0]?.messages ?? []).map((message) => message.ruleId ?? "fatal");

describe("the gates' linter puts each file's own text back", () => {
  it("knows a file's own text, and gives a probe's path an empty module", () => {
    expect(ownText("src/api/index.ts")).toContain("export");
    expect(ownText("src/probe.tsx")).toBe(EMPTY);
  });

  it("leaves the service clean: after a lint that empties src/api/index.ts, a lint that needs its types is clean", async () => {
    const linter = new Linter(OPTIONS);
    expect(await rules(linter, DEPENDS, PROBE)).toEqual([]);
    await rules(linter, EMPTY, INDEX);
    expect(Linter.dirty()).toEqual(["src/api/index.ts"]);
    expect(await rules(linter, DEPENDS, PROBE)).toEqual([]);
    expect(Linter.dirty()).toEqual([]);
  });

  it("the control: through ESLint itself, the same lint poisons the next one, until the file's own text is linted again", async () => {
    const plain = new ESLint(OPTIONS);
    try {
      await rules(plain, EMPTY, INDEX);
      expect((await rules(plain, DEPENDS, PROBE)).filter((rule) => rule.startsWith("@typescript-eslint/no-unsafe-"))).not.toEqual([]);
    } finally {
      await rules(plain, ownText(INDEX), INDEX);
      await rules(plain, EMPTY, PROBE);
    }
    expect(await rules(plain, DEPENDS, PROBE)).toEqual([]);
  });
});

describe("what another file can see", () => {
  it.each([
    ["a file of the package, given other text", "src/api/index.ts", "export {};\n", true],
    ["a probe that is a module", "src/probe.tsx", "export const f = 1;\n", false],
    ["a probe that is a script (its declarations are global)", "src/probe.tsx", "const fetch = 1;\n", true],
    ["a probe that declares into the global scope", "src/probe.tsx", "export {};\ndeclare global {\n  const x: number;\n}\n", true],
    ["a probe that augments a module", "src/probe.tsx", "export {};\ndeclare module './api' {\n  const x: number;\n}\n", true],
  ])("%s: %s", (_case, file, code, seen) => {
    expect(seenByOthers(file, code)).toBe(seen);
  });
});
