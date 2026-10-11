// @ts-check
/**
 * The checked-in policy files the gates read, `budget.json` and `licenses.json`, read strictly
 * (D418): a policy no gate validates is a policy a one-byte edit can empty. The text is parsed
 * here, not by `JSON.parse`, which keeps the last of two equal keys; an object with a key twice
 * is refused (`JSON.parse` still decides each string and number). The shape is exact (no other key, no missing one): a budget is `prod` and `e2e`,
 * each `bytes` and `files`, finite positive integers; a licence policy is `runtime` and `dev`,
 * each a non-empty array of SPDX identifiers. A refusal is a `PolicyError`, which the commands
 * report with the exit status 2.
 */
import { readFileSync } from "node:fs";

/** An SPDX licence identifier, as the licence check reads one. */
export const IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9.-]*$/u;

export class PolicyError extends Error {}

const SPACE = /[ \t\n\r]*/uy;
const STRING = /"(?:[^"\\]|\\.)*"/suy;
const SCALAR = /-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?|true|false|null/uy;

/**
 * One string or scalar token's value.
 * @param {string} token
 * @returns {unknown}
 */
function decode(token) {
  /** @type {unknown} */
  const found = JSON.parse(token);
  return found;
}

/**
 * A JSON text's value, with a repeated key in an object refused.
 * @param {string} text
 * @returns {unknown}
 */
export function parseStrict(text) {
  let at = 0;
  /** @param {RegExp} pattern @returns {string | null} */
  function take(pattern) {
    pattern.lastIndex = at;
    const found = pattern.exec(text);
    if (found === null) {
      return null;
    }
    at += found[0].length;
    return found[0];
  }
  /** @returns {unknown} */
  function value() {
    take(SPACE);
    const next = text[at];
    if (next === "{") {
      at += 1;
      /** @type {Map<string, unknown>} */
      const members = new Map();
      take(SPACE);
      if (text[at] === "}") {
        at += 1;
        return {};
      }
      for (;;) {
        take(SPACE);
        const raw = take(STRING);
        if (raw === null) {
          throw new PolicyError(`not JSON: a key was expected at ${String(at)}`);
        }
        const key = String(decode(raw));
        if (members.has(key)) {
          throw new PolicyError(`the key ${raw} is given twice`);
        }
        take(SPACE);
        if (text[at] !== ":") {
          throw new PolicyError(`not JSON: a colon was expected at ${String(at)}`);
        }
        at += 1;
        members.set(key, value());
        take(SPACE);
        const sep = text[at];
        at += 1;
        if (sep === "}") {
          return Object.fromEntries(members);
        }
        if (sep !== ",") {
          throw new PolicyError(`not JSON: a comma or brace was expected at ${String(at - 1)}`);
        }
      }
    }
    if (next === "[") {
      at += 1;
      /** @type {unknown[]} */
      const items = [];
      take(SPACE);
      if (text[at] === "]") {
        at += 1;
        return items;
      }
      for (;;) {
        items.push(value());
        take(SPACE);
        const sep = text[at];
        at += 1;
        if (sep === "]") {
          return items;
        }
        if (sep !== ",") {
          throw new PolicyError(`not JSON: a comma or bracket was expected at ${String(at - 1)}`);
        }
      }
    }
    const raw = take(STRING) ?? take(SCALAR);
    if (raw === null) {
      throw new PolicyError(`not JSON: a value was expected at ${String(at)}`);
    }
    return decode(raw);
  }
  const found = value();
  take(SPACE);
  if (at !== text.length) {
    throw new PolicyError(`not JSON: text after the value, at ${String(at)}`);
  }
  return found;
}

/**
 * @param {unknown} found
 * @returns {found is Record<string, unknown>}
 */
function isObject(found) {
  return typeof found === "object" && found !== null && !Array.isArray(found);
}

/**
 * Whether an object has exactly these keys.
 * @param {unknown} found
 * @param {readonly string[]} keys
 * @returns {found is Record<string, unknown>}
 */
function hasKeys(found, keys) {
  return isObject(found) && Object.keys(found).sort().join("\0") === [...keys].sort().join("\0");
}

/**
 * @typedef {{ bytes: number, files: number }} Allowance
 * @typedef {{ prod: Allowance, e2e: Allowance }} Budget
 * @typedef {{ runtime: string[], dev: string[] }} LicensePolicy
 */

/**
 * A budget: `prod` and `e2e`, each exactly `bytes` and `files`, finite positive integers.
 * @param {unknown} found
 * @returns {Budget}
 */
export function checkBudget(found) {
  if (!hasKeys(found, ["prod", "e2e"])) {
    throw new PolicyError('a budget has the keys "prod" and "e2e", and no other');
  }
  for (const mode of ["prod", "e2e"]) {
    const allowance = found[mode];
    if (!hasKeys(allowance, ["bytes", "files"])) {
      throw new PolicyError(`${mode}: an allowance has the keys "bytes" and "files", and no other`);
    }
    for (const key of ["bytes", "files"]) {
      const number = allowance[key];
      if (typeof number !== "number" || !Number.isSafeInteger(number) || number <= 0) {
        throw new PolicyError(`${mode}.${key}: not a finite positive integer`);
      }
    }
  }
  return /** @type {Budget} */ (found);
}

/**
 * A licence policy: `runtime` and `dev`, each a non-empty array of SPDX identifiers.
 * @param {unknown} found
 * @returns {LicensePolicy}
 */
export function checkLicensePolicy(found) {
  if (!hasKeys(found, ["runtime", "dev"])) {
    throw new PolicyError('a licence policy has the keys "runtime" and "dev", and no other');
  }
  for (const closure of ["runtime", "dev"]) {
    const list = found[closure];
    if (!Array.isArray(list) || list.length === 0) {
      throw new PolicyError(`${closure}: not a non-empty array of licence identifiers`);
    }
    for (const id of list) {
      if (typeof id !== "string" || !IDENTIFIER.test(id)) {
        throw new PolicyError(`${closure}: ${JSON.stringify(id)} is not a licence identifier`);
      }
    }
  }
  return /** @type {LicensePolicy} */ (found);
}

/**
 * Read a policy file: its text through `parseStrict`, its value through `check`; every refusal
 * names the file.
 * @template T
 * @param {string} file
 * @param {(found: unknown) => T} check
 * @returns {T}
 */
export function readPolicy(file, check) {
  try {
    return check(parseStrict(readFileSync(file, "utf8")));
  } catch (error) {
    // an unreadable file (an `Error` with a `code`), or a string `JSON.parse` refuses
    throw new PolicyError(`${file}: ${error instanceof Error ? error.message : "unreadable"}`);
  }
}
