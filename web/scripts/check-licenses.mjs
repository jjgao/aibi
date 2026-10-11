// @ts-check
/**
 * The licence policy (D418), over the lockfile's own `license` fields: lockfile v3 records one for
 * every package it holds, the per-platform binaries a machine does not install included, so the
 * check reads the lockfile and never `node_modules/`.
 *
 * A package of the runtime closure (one the lockfile does not mark `dev`) must be under a licence
 * of `licenses.json`'s `runtime` list, any other under one of its `dev` list. A `license` is an
 * SPDX expression: `OR` admits a package when one alternative is allowed, `AND` only when every
 * part is, `AND` binding tighter than `OR`, with parentheses; `WITH` exceptions and `+` are not
 * admitted. A package without a `license`, or with one that is not an expression of this grammar,
 * fails, as does a licence on neither list. The policy file is read strictly
 * (`policy-files.mjs`: its exact shape, no repeated key); one that is not a policy is a usage
 * error.
 *
 * Usage: `node scripts/check-licenses.mjs <package-lock.json> [--policy <licenses.json>]`.
 */
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { checkLicensePolicy, IDENTIFIER, PolicyError, readPolicy } from "./policy-files.mjs";

const WEB = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
/**
 * A JSON file's value, unknown until its reader says what it is.
 * @param {string} file
 * @returns {unknown}
 */
function readJson(file) {
  /** @type {unknown} */
  const value = JSON.parse(readFileSync(file, "utf8"));
  return value;
}

/**
 * @typedef {{ kind: "id", id: string } | { kind: "and" | "or", left: Expression, right: Expression }} Expression
 * @typedef {import("./policy-files.mjs").LicensePolicy} Policy
 */

/**
 * An SPDX expression's tree, or `null` if it is not one of the admitted grammar.
 * @param {string} text
 * @returns {Expression | null}
 */
export function parseLicense(text) {
  const tokens = text.match(/\(|\)|[^\s()]+/gu) ?? [];
  let at = 0;
  /** @returns {Expression | null} */
  function primary() {
    const token = tokens[at];
    if (token === "(") {
      at += 1;
      const inner = or();
      if (inner === null || tokens[at] !== ")") {
        return null;
      }
      at += 1;
      return inner;
    }
    if (token === undefined || token === ")" || token === "AND" || token === "OR" || token === "WITH") {
      return null;
    }
    if (!IDENTIFIER.test(token)) {
      return null;
    }
    at += 1;
    return { kind: "id", id: token };
  }
  /** @returns {Expression | null} */
  function and() {
    let left = primary();
    while (left !== null && tokens[at] === "AND") {
      at += 1;
      const right = primary();
      left = right === null ? null : { kind: "and", left, right };
    }
    return left;
  }
  /** @returns {Expression | null} */
  function or() {
    let left = and();
    while (left !== null && tokens[at] === "OR") {
      at += 1;
      const right = and();
      left = right === null ? null : { kind: "or", left, right };
    }
    return left;
  }
  if (tokens.length === 0) {
    return null;
  }
  const tree = or();
  return tree !== null && at === tokens.length ? tree : null;
}

/**
 * Whether an expression is satisfied by the allowed licences.
 * @param {Expression} tree
 * @param {ReadonlySet<string>} allowed
 * @returns {boolean}
 */
export function satisfied(tree, allowed) {
  switch (tree.kind) {
    case "id":
      return allowed.has(tree.id);
    case "and":
      return satisfied(tree.left, allowed) && satisfied(tree.right, allowed);
    case "or":
      return satisfied(tree.left, allowed) || satisfied(tree.right, allowed);
  }
}

/**
 * Every problem of a lockfile under a policy.
 * @param {unknown} lockfile
 * @param {Policy} policy
 * @returns {string[]}
 */
export function licenseProblems(lockfile, policy) {
  if (typeof lockfile !== "object" || lockfile === null || !("lockfileVersion" in lockfile) || lockfile.lockfileVersion !== 3 || !("packages" in lockfile)) {
    return ["the lockfile is not a lockfile of version 3"];
  }
  const packages = /** @type {Record<string, Record<string, unknown>>} */ (lockfile.packages);
  const runtime = new Set(policy.runtime);
  const dev = new Set(policy.dev);
  const problems = [];
  let counted = 0;
  for (const [key, found] of Object.entries(packages)) {
    if (key === "") {
      continue;
    }
    counted += 1;
    const closure = found["dev"] === true ? "dev" : "runtime";
    const license = found["license"];
    if (typeof license !== "string") {
      problems.push(`${key}: no licence recorded`);
      continue;
    }
    const tree = parseLicense(license);
    if (tree === null) {
      problems.push(`${key}: ${JSON.stringify(license)} is not an SPDX expression this check reads`);
      continue;
    }
    if (!satisfied(tree, closure === "dev" ? dev : runtime)) {
      problems.push(`${key}: ${JSON.stringify(license)} is not allowed in the ${closure} closure`);
    }
  }
  if (counted === 0) {
    problems.push("the lockfile holds no package");
  }
  return problems;
}

/**
 * The command line; the exit status: 0 when every package passes, 1 when one does not, 2 for a
 * usage error or a policy file that is not one.
 * @param {readonly string[]} argv
 * @param {(line: string) => void} say
 * @returns {number}
 */
export function main(argv, say) {
  const lockfile = argv[0];
  let policyFile = path.join(WEB, "licenses.json");
  if (argv[1] === "--policy" && argv[2] !== undefined && argv.length === 3) {
    policyFile = argv[2];
  } else if (argv.length !== 1) {
    say("usage: check-licenses.mjs <package-lock.json> [--policy <licenses.json>]");
    return 2;
  }
  if (lockfile === undefined) {
    return 2;
  }
  /** @type {Policy} */
  let policy;
  try {
    policy = readPolicy(policyFile, checkLicensePolicy);
  } catch (error) {
    if (error instanceof PolicyError) {
      say(`check-licenses: ${error.message}`);
      return 2;
    }
    throw error;
  }
  const problems = licenseProblems(readJson(lockfile), policy);
  for (const problem of problems) {
    say(`check-licenses: ${problem}`);
  }
  if (problems.length === 0) {
    say(`check-licenses: every package of ${lockfile} passes`);
  }
  return problems.length === 0 ? 0 : 1;
}

if (process.argv[1] !== undefined && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  process.exitCode = main(process.argv.slice(2), (line) => {
    process.stdout.write(`${line}\n`);
  });
}
