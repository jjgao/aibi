// @ts-check
/**
 * The client's types from the OpenAPI document (D416, D419): `schemas/openapi.json` is the input,
 * `src/api/generated/openapi.ts` (openapi-typescript 7.13.0) and `src/api/generated/routes.ts`
 * the output, both checked in. `npm run generate` writes them; `npm run generate -- --check`
 * (CI's `web` job) fails, writing nothing, while either differs from what the document gives.
 *
 * **The transform.** The document marks what a code generator cannot say by itself:
 * - `x-aibi-server-number: true` on every `number` or `integer` of a response's JSON body
 *   (D416): each renders as `ServerNumber` (`src/api/box.ts`), never as `number`. The census of
 *   the marks is read from the document, every mark is checked to be reached by the transform
 *   (by identity of the schema object, as openapi-typescript calls it twice for a property), and
 *   one it does not reach, or one on anything but a plain `number` or `integer`, fails the
 *   generation. A response number that rendered as `number` would be a JS number a component
 *   could do arithmetic on.
 * - `x-aibi-json: {direction, null}` on the six recursive JSON-value components, at a
 *   component's top level alone: each renders as one of the three hand-written types of
 *   `src/api/json.ts` (`request`, `null: false` -> `JsonIn`; `request`, `null: true` ->
 *   `JsonInNull`; `response`, `null: true` -> `JsonOut`); any other combination, a mark that is
 *   not exactly those two members, or a mark anywhere but a component's top level, fails. Without
 *   the transform each of the six is TS2502 (it refers to itself through its own annotation).
 *
 * Request numbers are left as openapi-typescript renders them, `number`: what a person types, or
 * (the request half, M5.1c-1c and M5.3) what the client builds; no `ServerNumber` is assignable
 * to one. `defaultNonNullable: false` (a member with a default stays optional, as on the wire)
 * and `immutable: true` (every member and array read-only, as `decode` freezes what it makes).
 *
 * **The routes.** `routes.ts` gives each operation a function named by its operation id that
 * builds its `Route` (`src/api/route.ts`): the method, and the URL from the path template, each
 * literal segment percent-encoded here and each parameter by `segment` at run time (which refuses
 * an empty, `.` or `..` value), the query by `search`. A template whose literal segment is empty,
 * `.` or `..`, or that mixes a parameter with text in one segment, an operation id that is not a
 * plain name or a reserved word, a method other than GET and POST, and a path parameter the
 * template does not name (or the reverse) fail the generation.
 */
import { readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import openapiTS, { astToString } from "openapi-typescript";
import ts from "typescript";

const WEB = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
export const DOCUMENT = path.join(WEB, "..", "schemas", "openapi.json");
export const OUTPUTS = {
  openapi: path.join(WEB, "src", "api", "generated", "openapi.ts"),
  routes: path.join(WEB, "src", "api", "generated", "routes.ts"),
};

export const SERVER_NUMBER = "x-aibi-server-number";
export const JSON_VALUE = "x-aibi-json";

/** The hand-written type of each `x-aibi-json` mark, by `direction` and `null`. */
export const JSON_TYPES = new Map([
  ["request false", "JsonIn"],
  ["request true", "JsonInNull"],
  ["response true", "JsonOut"],
]);

export class GenerationError extends Error {}

/** @typedef {Record<string, unknown>} JsonObject */

/** @param {unknown} value @returns {value is JsonObject} */
function isObject(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * Every mark of the document: the schema objects that carry `x-aibi-server-number`, with their
 * JSON pointers, and the components that carry `x-aibi-json`. A mark anywhere a transform would
 * not see it as the document means it (`x-aibi-json` below a component's top level) is refused.
 * @param {unknown} document
 * @returns {{ numbers: Map<JsonObject, string>, json: Map<string, string> }}
 */
export function census(document) {
  /** @type {Map<JsonObject, string>} */
  const numbers = new Map();
  /** @type {Map<string, string>} */
  const json = new Map();
  /** @param {unknown} node @param {string} at */
  const walk = (node, at) => {
    if (Array.isArray(node)) {
      node.forEach((item, index) => {
        walk(item, `${at}/${String(index)}`);
      });
      return;
    }
    if (!isObject(node)) {
      return;
    }
    if (Object.hasOwn(node, SERVER_NUMBER)) {
      if (node[SERVER_NUMBER] !== true || (node["type"] !== "integer" && node["type"] !== "number")) {
        throw new GenerationError(`${at}: ${SERVER_NUMBER} is only true, on a plain number or integer`);
      }
      numbers.set(node, at);
    }
    if (Object.hasOwn(node, JSON_VALUE)) {
      const found = /^#\/components\/schemas\/([A-Za-z0-9._-]+)$/u.exec(at);
      if (found?.[1] === undefined) {
        throw new GenerationError(`${at}: ${JSON_VALUE} is read at a component's top level alone`);
      }
      json.set(`#/components/schemas/${found[1]}`, jsonType(node[JSON_VALUE], at));
    }
    for (const [key, value] of Object.entries(node)) {
      walk(value, `${at}/${key.replaceAll("~", "~0").replaceAll("/", "~1")}`);
    }
  };
  walk(document, "#");
  return { numbers, json };
}

/** @param {unknown} mark @param {string} at @returns {string} */
function jsonType(mark, at) {
  if (!isObject(mark) || Object.keys(mark).sort().join(",") !== "direction,null" || typeof mark["null"] !== "boolean") {
    throw new GenerationError(`${at}: ${JSON_VALUE} is exactly {direction, null}`);
  }
  const found = JSON_TYPES.get(`${String(mark["direction"])} ${String(mark["null"])}`);
  if (found === undefined) {
    throw new GenerationError(`${at}: no hand-written type for this ${JSON_VALUE}`);
  }
  return found;
}

const HEADER = `/**
 * Generated by \`scripts/generate-types.mjs\` from \`schemas/openapi.json\`: do not edit. Run
 * \`npm run generate\`; CI fails while this file differs from what the document gives (D419).
 */
`;

const TYPES_HEADER = `${HEADER}import type { ServerNumber } from "../box";
import type { JsonIn, JsonInNull, JsonOut } from "../json";

`;

/**
 * The types of the document, rendered by openapi-typescript with the transform (`marks: false`
 * renders it without, for the gate that shows why the transform exists).
 * @param {JsonObject} document
 * @param {{ marks?: boolean }} [options]
 * @returns {Promise<string>}
 */
export async function renderTypes(document, options = {}) {
  const marks = options.marks ?? true;
  const found = census(document);
  /** @type {Set<JsonObject>} */
  const reached = new Set();
  /** @type {Set<string>} */
  const reachedJson = new Set();
  /** @type {import("openapi-typescript").OpenAPITSOptions["transform"]} */
  const transform = (schema, context) => {
    if (!isObject(schema)) {
      return undefined;
    }
    if (Object.hasOwn(schema, JSON_VALUE)) {
      const type = context.path === undefined ? undefined : found.json.get(context.path);
      if (type === undefined) {
        throw new GenerationError(`${String(context.path)}: ${JSON_VALUE} reached where the census has none`);
      }
      reachedJson.add(context.path ?? "");
      return ts.factory.createTypeReferenceNode(type);
    }
    if (Object.hasOwn(schema, SERVER_NUMBER)) {
      if (!found.numbers.has(schema)) {
        throw new GenerationError(`${String(context.path)}: a ${SERVER_NUMBER} the census has not`);
      }
      reached.add(schema);
      return ts.factory.createTypeReferenceNode("ServerNumber");
    }
    return undefined;
  };
  const nodes = await openapiTS(document, {
    ...(marks ? { transform } : {}),
    defaultNonNullable: false,
    immutable: true,
    silent: true,
  });
  if (marks) {
    const missed = [...found.numbers].filter(([node]) => !reached.has(node)).map(([, at]) => at);
    if (missed.length > 0) {
      throw new GenerationError(`the transform reached no ${SERVER_NUMBER} at ${missed.join(", ")}`);
    }
    const missedJson = [...found.json.keys()].filter((at) => !reachedJson.has(at));
    if (missedJson.length > 0) {
      throw new GenerationError(`the transform reached no ${JSON_VALUE} at ${missedJson.join(", ")}`);
    }
  }
  return `${marks ? TYPES_HEADER : HEADER}${astToString(nodes)}`;
}

const RESERVED = new Set(
  (
    "break case catch class const continue debugger default delete do else enum export extends false finally for " +
    "function if import in instanceof new null return super switch this throw true try typeof var void while with " +
    "yield let static implements interface package private protected public await arguments eval undefined"
  ).split(" "),
);

/** An operation id a function can be named by. @param {unknown} id @param {string} at @returns {string} */
function functionName(id, at) {
  if (typeof id !== "string" || !/^[a-z][a-z0-9_]*$/u.test(id) || RESERVED.has(id)) {
    throw new GenerationError(`${at}: the operation id is no plain name`);
  }
  return id;
}

/**
 * The parts of a path template: each literal segment percent-encoded, each parameter by name.
 * @param {string} template
 * @returns {({ literal: string } | { parameter: string })[]}
 */
export function templateParts(template) {
  if (!template.startsWith("/")) {
    throw new GenerationError(`${template}: a path starts with /`);
  }
  return template
    .slice(1)
    .split("/")
    .map((part) => {
      const parameter = /^\{([A-Za-z_][A-Za-z0-9_]*)\}$/u.exec(part);
      if (parameter?.[1] !== undefined) {
        return { parameter: parameter[1] };
      }
      if (part === "" || part === "." || part === ".." || part.includes("{") || part.includes("}")) {
        throw new GenerationError(`${template}: a segment is empty, . or .., or mixes a parameter with text`);
      }
      return { literal: encodeURIComponent(part) };
    });
}

/**
 * `routes.ts`: a function for each operation, named by its operation id.
 * @param {JsonObject} document
 * @returns {string}
 */
export function renderRoutes(document) {
  const paths = document["paths"];
  if (!isObject(paths)) {
    throw new GenerationError("the document has no paths");
  }
  const out = [
    HEADER,
    'import { route, search, segment, type Route } from "../route";\n',
    'import type { operations } from "./openapi";\n',
  ];
  /** @type {Set<string>} */
  const names = new Set();
  for (const [template, item] of Object.entries(paths)) {
    if (!isObject(item)) {
      throw new GenerationError(`${template}: no path item`);
    }
    for (const [method, operation] of Object.entries(item)) {
      if (method !== "get" && method !== "post") {
        throw new GenerationError(`${template}: the method ${method} is not GET or POST`);
      }
      if (!isObject(operation)) {
        throw new GenerationError(`${template} ${method}: no operation`);
      }
      const name = functionName(operation["operationId"], `${template} ${method}`);
      if (names.has(name)) {
        throw new GenerationError(`${name}: an operation id twice`);
      }
      names.add(name);
      out.push(renderRoute(template, method.toUpperCase(), name, operation));
    }
  }
  return out.join("");
}

/**
 * @param {string} template @param {string} method @param {string} name @param {JsonObject} operation
 * @returns {string}
 */
function renderRoute(template, method, name, operation) {
  const parameters = Array.isArray(operation["parameters"]) ? operation["parameters"].filter(isObject) : [];
  const inPath = parameters.filter((given) => given["in"] === "path").map((given) => String(given["name"]));
  const inQuery = parameters.filter((given) => given["in"] === "query");
  const parts = templateParts(template);
  const named = parts.flatMap((part) => ("parameter" in part ? [part.parameter] : []));
  if ([...named].sort().join(",") !== [...inPath].sort().join(",") || new Set(named).size !== named.length) {
    throw new GenerationError(`${template}: the template's parameters are not the operation's path parameters`);
  }
  const url = parts
    .map((part) => ("parameter" in part ? `/\${segment(path.${part.parameter})}` : `/${part.literal}`))
    .join("");
  const signature = [];
  if (named.length > 0) {
    signature.push(`path: operations["${name}"]["parameters"]["path"]`);
  }
  let query = "";
  if (inQuery.length > 0) {
    const required = inQuery.some((given) => given["required"] === true);
    const type = `NonNullable<operations["${name}"]["parameters"]["query"]>`;
    signature.push(required ? `query: ${type}` : `query: ${type} = {}`);
    const entries = inQuery.map((given) => {
      const key = String(given["name"]);
      if (!/^[A-Za-z_][A-Za-z0-9_]*$/u.test(key)) {
        throw new GenerationError(`${template}: the query parameter ${key} is no plain name`);
      }
      return `["${key}", query.${key}]`;
    });
    query = `\${search([${entries.join(", ")}])}`;
  }
  return `
/** \`${method} ${template}\` */
export function ${name}(${signature.join(", ")}): Route<"${name}"> {
    return route("${name}", "${method}", \`${url}${query}\`);
}
`;
}

/**
 * Generate both files from `document`.
 * @param {JsonObject} document
 * @returns {Promise<{ openapi: string, routes: string }>}
 */
export async function generate(document) {
  return { openapi: await renderTypes(document), routes: renderRoutes(document) };
}

/**
 * @param {string[]} argv
 * @param {(line: string) => void} say
 * @param {{ document: string, outputs: { openapi: string, routes: string } }} [files] where the
 *   document is read and the outputs written (the committed ones but in a test)
 * @returns {Promise<number>}
 */
export async function main(argv, say, files = { document: DOCUMENT, outputs: OUTPUTS }) {
  const check = argv.length === 1 && argv[0] === "--check";
  if (argv.length > 1 || (argv.length === 1 && !check)) {
    say("usage: generate-types.mjs [--check]");
    return 2;
  }
  /** @type {unknown} */
  const document = JSON.parse(readFileSync(files.document, "utf8"));
  if (!isObject(document)) {
    say("generate-types: the document is no object");
    return 2;
  }
  let made;
  try {
    made = await generate(document);
  } catch (error) {
    if (error instanceof GenerationError) {
      say(`generate-types: ${error.message}`);
      return 1;
    }
    throw error;
  }
  const stale = /** @type {const} */ (["openapi", "routes"]).filter((key) => {
    let current = null;
    try {
      current = readFileSync(files.outputs[key], "utf8");
    } catch {
      // missing: stale
    }
    return current !== made[key];
  });
  if (check) {
    for (const key of stale) {
      say(`generate-types: ${path.relative(WEB, files.outputs[key])} is stale: run npm run generate`);
    }
    return stale.length === 0 ? 0 : 1;
  }
  for (const key of stale) {
    writeFileSync(files.outputs[key], made[key]);
    say(`generate-types: wrote ${path.relative(WEB, files.outputs[key])}`);
  }
  return 0;
}

if (process.argv[1] !== undefined && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  process.exitCode = await main(process.argv.slice(2), (line) => {
    process.stdout.write(`${line}\n`);
  });
}
