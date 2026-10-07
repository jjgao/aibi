/** `scripts/policy-files.mjs`: the checked-in policy files read strictly, and the commands that
 * read them (`check-bundle`, `check-licenses`) refusing a file that is no policy with status 2. */
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";

import { afterAll, describe, expect, it } from "vitest";

import { main as checkBundle } from "../../scripts/check-bundle.mjs";
import { main as checkLicenses } from "../../scripts/check-licenses.mjs";
import {
  checkBudget,
  checkLicensePolicy,
  parseStrict,
  PolicyError,
  readPolicy,
} from "../../scripts/policy-files.mjs";

const WEB = path.join(import.meta.dirname, "../..");
const scratch = mkdtempSync(path.join(os.tmpdir(), "aibi-policy-files-"));

afterAll(() => {
  rmSync(scratch, { recursive: true, force: true });
});

let made = 0;
function written(text: string): string {
  made += 1;
  const file = path.join(scratch, `f${String(made)}.json`);
  writeFileSync(file, text);
  return file;
}

const BUDGET = '{"prod":{"bytes":100,"files":2},"e2e":{"bytes":200,"files":3}}';
const LICENSES = '{"runtime":["MIT"],"dev":["MIT","ISC"]}';

describe("parseStrict", () => {
  it.each([
    ['{"a":1,"b":[true,null,"x\\n",-1.5e2,{}],"c":{"d":[]}}', { a: 1, b: [true, null, "x\n", -150, {}], c: { d: [] } }],
    [' \n{ "a" : [ ] }\t', { a: [] }],
    ['"s"', "s"],
    ["0", 0],
    ['{"__proto__":1}', { ["__proto__"]: 1 }],
  ])("reads %s", (text, expected) => {
    expect(parseStrict(text)).toEqual(expected);
  });

  it.each([
    '{"a":1,"a":2}',
    '{"prod":{"bytes":1,"files":1},"prod":{"bytes":1000000000,"files":1}}',
    '{"a":{"b":1,"b":2}}',
    '[{"a":1,"a":1}]',
    '{"a":1,}',
    "{a:1}",
    '{"a" 1}',
    "[1,]",
    "[1 2]",
    '{"a":1} x',
    "",
    "01",
    "NaN",
    '"\n"',
    "'a'",
  ])("refuses %j", (text) => {
    expect(() => parseStrict(text)).toThrow();
  });
});

describe("the budget's shape", () => {
  const good = { prod: { bytes: 100, files: 2 }, e2e: { bytes: 200, files: 3 } };

  it("admits the budget's own shape", () => {
    expect(checkBudget(good)).toEqual(good);
    expect(readPolicy(written(BUDGET), checkBudget)).toEqual(good);
  });

  it.each([
    ["a missing mode", { prod: good.prod }],
    ["another mode", { ...good, dev: good.prod }],
    ["a missing bytes", { ...good, prod: { files: 6 } }],
    ["another key", { ...good, prod: { ...good.prod, extra: 1 } }],
    ["bytes a string", { ...good, prod: { bytes: "x", files: 6 } }],
    ["bytes a numeric string", { ...good, prod: { bytes: "100", files: 6 } }],
    ["bytes infinite", { ...good, prod: { bytes: Infinity, files: 6 } }],
    ["bytes zero", { ...good, prod: { bytes: 0, files: 6 } }],
    ["bytes negative", { ...good, prod: { bytes: -1, files: 6 } }],
    ["bytes fractional", { ...good, prod: { bytes: 1.5, files: 6 } }],
    ["files null", { ...good, e2e: { bytes: 1, files: null } }],
    ["an allowance an array", { ...good, e2e: [1, 2] }],
    ["the budget an array", [good]],
    ["the budget null", null],
  ])("refuses %s", (_why, found) => {
    expect(() => checkBudget(found)).toThrow(PolicyError);
  });

  it("refuses a repeated key, 1e400 and a file that is not JSON, naming the file", () => {
    const repeated = written('{"prod":{"bytes":1,"files":1},"prod":{"bytes":1000000000,"files":1},"e2e":{"bytes":1,"files":1}}');
    expect(() => readPolicy(repeated, checkBudget)).toThrow(`${repeated}: the key "prod" is given twice`);
    const huge = written('{"prod":{"bytes":1e400,"files":1},"e2e":{"bytes":1,"files":1}}');
    expect(() => readPolicy(huge, checkBudget)).toThrow("prod.bytes: not a finite positive integer");
    expect(() => readPolicy(written("{"), checkBudget)).toThrow(PolicyError);
    expect(() => readPolicy(path.join(scratch, "absent.json"), checkBudget)).toThrow(PolicyError);
  });

  it("is refused by check-bundle with status 2, whichever shape is wrong", () => {
    mkdirSync(path.join(scratch, "dir"), { recursive: true });
    for (const text of [
      '{"prod":{"files":6},"e2e":{"bytes":1,"files":1}}',
      '{"prod":{"bytes":"x","files":6},"e2e":{"bytes":1,"files":1}}',
      '{"prod":{"bytes":1e400,"files":6},"e2e":{"bytes":1,"files":1}}',
      '{"prod":{"bytes":1,"files":6},"prod":{"bytes":1000000000,"files":6},"e2e":{"bytes":1,"files":1}}',
      "not json",
    ]) {
      const said: string[] = [];
      expect(checkBundle(["--mode", "prod", path.join(scratch, "dir"), "--budget", written(text), "--no-loader"], (line) => said.push(line)), text).toBe(2);
      expect(said, text).toHaveLength(1);
      expect(said[0], text).toMatch(/^check-bundle: .*\.json: /u);
    }
  });
});

describe("the licence policy's shape", () => {
  const good = { runtime: ["MIT"], dev: ["MIT", "ISC"] };

  it("admits the policy's own shape", () => {
    expect(checkLicensePolicy(good)).toEqual(good);
    expect(readPolicy(written(LICENSES), checkLicensePolicy)).toEqual(good);
  });

  it.each([
    ["a missing list", { runtime: ["MIT"] }],
    ["another key", { ...good, extra: ["MIT"] }],
    ["a string where an array belongs", { ...good, runtime: "MIT" }],
    ["a string for the dev list", { ...good, dev: "MIT" }],
    ["an empty list", { ...good, runtime: [] }],
    ["a non-string entry", { ...good, runtime: ["MIT", 1] }],
    ["an entry that is not an identifier", { ...good, dev: ["MIT OR GPL-3.0"] }],
    ["an empty entry", { ...good, dev: [""] }],
    ["an object entry", { ...good, dev: [{ id: "MIT" }] }],
    ["the policy an array", [good]],
  ])("refuses %s", (_why, found) => {
    expect(() => checkLicensePolicy(found)).toThrow(PolicyError);
  });

  it("is refused by check-licenses with status 2, and a repeated key too", () => {
    const lock = path.join(WEB, "package-lock.json");
    for (const text of [
      '{"runtime":"MIT","dev":["MIT"]}',
      '{"runtime":["MIT"],"runtime":["MIT","GPL-3.0"],"dev":["MIT"]}',
      '{"runtime":["MIT"]}',
      "not json",
    ]) {
      const said: string[] = [];
      expect(checkLicenses([lock, "--policy", written(text)], (line) => said.push(line)), text).toBe(2);
      expect(said[0], text).toMatch(/^check-licenses: .*\.json: /u);
    }
  });
});
