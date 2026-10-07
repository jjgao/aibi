/** `scripts/check-licenses.mjs`: the SPDX reading, the policy, and the committed lockfile. */
import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { licenseProblems, main, parseLicense, satisfied } from "../../scripts/check-licenses.mjs";
import { checkLicensePolicy, readPolicy } from "../../scripts/policy-files.mjs";

const WEB = path.join(import.meta.dirname, "../..");
const POLICY = { runtime: ["MIT", "ISC"], dev: ["MIT", "ISC", "Apache-2.0", "CC0-1.0"] };

function lockfile(packages: Record<string, Record<string, unknown>>): unknown {
  return { lockfileVersion: 3, packages: { "": { name: "x" }, ...packages } };
}

function allowed(text: string, licences: string[]): boolean | null {
  const tree = parseLicense(text);
  return tree === null ? null : satisfied(tree, new Set(licences));
}

describe("the SPDX reading", () => {
  it.each([
    ["MIT", ["MIT"], true],
    ["MIT", ["ISC"], false],
    ["(MIT OR CC0-1.0)", ["CC0-1.0"], true],
    ["MIT OR CC0-1.0", ["MIT"], true],
    ["(MIT AND CC0-1.0)", ["MIT"], false],
    ["(MIT AND CC0-1.0)", ["MIT", "CC0-1.0"], true],
    ["MIT OR GPL-3.0 AND Apache-2.0", ["MIT"], true],
    ["(MIT OR GPL-3.0) AND Apache-2.0", ["MIT"], false],
    ["GPL-3.0 AND Apache-2.0 OR MIT", ["MIT"], true],
    ["GPL-3.0 AND (Apache-2.0 OR MIT)", ["MIT"], false],
    ["((MIT))", ["MIT"], true],
  ])("%s under %j is %s", (text, licences, expected) => {
    expect(allowed(text, licences)).toBe(expected);
  });

  it.each(["", "MIT OR", "OR MIT", "(MIT", "MIT)", "MIT AND AND ISC", "GPL-2.0 WITH Classpath-exception-2.0", "SEE LICENSE IN LICENSE.txt", "MIT ISC", "GPL-2.0+", "()"])(
    "does not read %j",
    (text) => {
      expect(parseLicense(text)).toBeNull();
    },
  );
});

describe("the policy over a lockfile", () => {
  it("admits the runtime closure under its list, the rest under the dev list", () => {
    expect(
      licenseProblems(
        lockfile({
          "node_modules/react": { license: "MIT" },
          "node_modules/typescript": { license: "Apache-2.0", dev: true },
          "node_modules/type-fest": { license: "(MIT OR CC0-1.0)", dev: true },
        }),
        POLICY,
      ),
    ).toEqual([]);
  });

  it("refuses a dev licence in the runtime closure, and an optional runtime package's too", () => {
    expect(licenseProblems(lockfile({ "node_modules/x": { license: "Apache-2.0" } }), POLICY)).toEqual([
      'node_modules/x: "Apache-2.0" is not allowed in the runtime closure',
    ]);
    expect(licenseProblems(lockfile({ "node_modules/x": { license: "Apache-2.0", devOptional: true } }), POLICY)).toHaveLength(1);
    expect(licenseProblems(lockfile({ "node_modules/x": { license: "Apache-2.0", optional: true } }), POLICY)).toEqual([
      'node_modules/x: "Apache-2.0" is not allowed in the runtime closure',
    ]);
  });

  it("reads a platform binary the machine never installed, from the lockfile", () => {
    const found = licenseProblems(
      lockfile({ "node_modules/bin-win32-arm64": { license: "GPL-3.0", dev: true, optional: true, os: ["win32"], cpu: ["arm64"] } }),
      POLICY,
    );
    expect(found).toEqual(['node_modules/bin-win32-arm64: "GPL-3.0" is not allowed in the dev closure']);
  });

  it("refuses a missing, unreadable or unknown licence, and AND read as OR", () => {
    expect(
      licenseProblems(
        lockfile({
          "node_modules/a": { dev: true },
          "node_modules/b": { license: { type: "MIT" }, dev: true },
          "node_modules/c": { license: "MIT OR", dev: true },
          "node_modules/d": { license: "WTFPL", dev: true },
          "node_modules/e": { license: "MIT AND GPL-3.0", dev: true },
        }),
        POLICY,
      ),
    ).toEqual([
      "node_modules/a: no licence recorded",
      "node_modules/b: no licence recorded",
      'node_modules/c: "MIT OR" is not an SPDX expression this check reads',
      'node_modules/d: "WTFPL" is not allowed in the dev closure',
      'node_modules/e: "MIT AND GPL-3.0" is not allowed in the dev closure',
    ]);
  });

  it("refuses a lockfile of another version, or one without packages", () => {
    expect(licenseProblems({ lockfileVersion: 2, packages: {} }, POLICY)).toEqual(["the lockfile is not a lockfile of version 3"]);
    expect(licenseProblems(lockfile({}), POLICY)).toEqual(["the lockfile holds no package"]);
  });
});

describe("the committed policy and lockfile", () => {
  const policy = readPolicy(path.join(WEB, "licenses.json"), checkLicensePolicy);

  it("allows MIT and ISC alone at run time, and in the rest D418's list exactly", () => {
    expect(policy.runtime).toEqual(["MIT", "ISC"]);
    expect(policy.dev).toEqual([
      "MIT",
      "ISC",
      "MIT-0",
      "Apache-2.0",
      "BSD-2-Clause",
      "BSD-3-Clause",
      "BlueOak-1.0.0",
      "CC0-1.0",
      "MPL-2.0",
    ]);
  });

  it("passes the committed lockfile, whose runtime closure is React's and the router's", () => {
    const said: string[] = [];
    expect(main([path.join(WEB, "package-lock.json")], (line) => said.push(line))).toBe(0);
    const lock = JSON.parse(readFileSync(path.join(WEB, "package-lock.json"), "utf8")) as {
      packages: Record<string, { dev?: boolean }>;
    };
    const runtime = Object.entries(lock.packages)
      .filter(([key, found]) => key !== "" && found.dev !== true)
      .map(([key]) => key.replace(/^node_modules\//u, ""))
      .sort();
    expect(runtime).toEqual(["@remix-run/route-pattern", "cookie-es", "react", "react-dom", "react-router", "scheduler"]);
  });

  it("exits 2 on a usage error", () => {
    expect(main([], () => undefined)).toBe(2);
    expect(main(["a", "b"], () => undefined)).toBe(2);
  });
});
