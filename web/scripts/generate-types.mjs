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
 * - The request ids (D425): the request positions `src/api/classes.json` gives a `from` (the
 *   four that take back an id a response gave: `TakeOver.session`, `Accept.proposal`,
 *   `path:proposal`, `query:release`) render as `RequestId` (`src/api/ids.ts`), a number only the
 *   grammar makes, so that no plain number and no box compiles there. Each position must name at
 *   least one plain `integer` schema of a request that is not a response number (a component's
 *   member, or every path or query parameter of that name), and each must be reached, by the
 *   identity of its schema object, or the generation fails.
 *
 * Every other request number is left as openapi-typescript renders it, `number`: what a person
 * types, or (M5.3) what the client builds; no `ServerNumber` is assignable to one. `defaultNonNullable: false` (a member with a default stays optional, as on the wire)
 * and `immutable: true` (every member and array read-only, as `decode` freezes what it makes).
 *
 * **The routes.** `routes.ts` gives each operation a function named by its operation id that
 * builds its `Route` (`src/api/route.ts`): the method, and the URL from the path template, each
 * literal segment percent-encoded here and each parameter by `segment` at run time (which refuses
 * an empty, `.` or `..` value), the query by `search`, and the value of a `{dataset}` parameter,
 * which the route holds privately with its URL (D425: the dataset an answer's provenance is
 * recorded under). A template whose literal segment is empty,
 * `.` or `..`, or that mixes a parameter with text in one segment, an operation id that is not a
 * plain name or a reserved word, a method other than GET and POST, and a path parameter the
 * template does not name (or the reverse) fail the generation.
 */
import { readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import openapiTS, { astToString } from "openapi-typescript";
import ts from "typescript";

import { renderShapes, ShapeError } from "./generate-shapes.mjs";

const WEB = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
export const DOCUMENT = path.join(WEB, "..", "schemas", "openapi.json");
export const CLASSES = path.join(WEB, "src", "api", "classes.json");
export const OUTPUTS = {
  openapi: path.join(WEB, "src", "api", "generated", "openapi.ts"),
  routes: path.join(WEB, "src", "api", "generated", "routes.ts"),
  shapes: path.join(WEB, "src", "api", "generated", "shapes.ts"),
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

/**
 * The positions `classes.json` gives a feed (`adapter_fed.from`, not `null`): the request ids.
 * @param {unknown} classes
 * @returns {string[]}
 */
export function idPositions(classes) {
  const fed = isObject(classes) ? classes["adapter_fed"] : undefined;
  const from = isObject(fed) ? fed["from"] : undefined;
  if (!isObject(from)) {
    throw new GenerationError("classes.json: adapter_fed has no from");
  }
  return Object.entries(from)
    .filter(([, feed]) => feed !== null)
    .map(([position]) => position)
    .sort();
}

/**
 * The schema objects of the request ids, each with its position: `Component.member` is that
 * member of that component; `path:name` and `query:name` every parameter so placed and named. A
 * position that names no plain `integer`, or one a response number's mark is on, fails (a
 * parameter of the name that is no number at all, such as a string, is not the position).
 * @param {JsonObject} document
 * @param {readonly string[]} positions
 * @returns {Map<JsonObject, string>}
 */
export function idSchemas(document, positions) {
  /** @type {Map<JsonObject, string>} */
  const found = new Map();
  const components = isObject(document["components"]) ? document["components"] : {};
  const schemas = isObject(components["schemas"]) ? components["schemas"] : {};
  const paths = isObject(document["paths"]) ? document["paths"] : {};
  for (const position of positions) {
    /** @type {unknown[]} */
    const named = [];
    const placed = /^(path|query):([A-Za-z_][A-Za-z0-9_]*)$/u.exec(position);
    const member = /^([A-Za-z][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)$/u.exec(position);
    if (placed?.[1] !== undefined && placed[2] !== undefined) {
      for (const item of Object.values(paths)) {
        for (const operation of isObject(item) ? Object.values(item) : []) {
          const parameters = isObject(operation) && Array.isArray(operation["parameters"]) ? operation["parameters"] : [];
          for (const given of parameters.filter(isObject)) {
            const schema = given["schema"];
            // A parameter of that name that is no number (the descriptor route's string `release`)
            // is no request number, as the classes' walk reads it.
            if (given["in"] === placed[1] && given["name"] === placed[2] && isObject(schema) && (schema["type"] === "integer" || schema["type"] === "number")) {
              named.push(given["schema"]);
            }
          }
        }
      }
    } else if (member?.[1] !== undefined && member[2] !== undefined) {
      const component = schemas[member[1]];
      const properties = isObject(component) ? component["properties"] : undefined;
      named.push(isObject(properties) ? properties[member[2]] : undefined);
    }
    if (named.length === 0 || !named.every((schema) => isObject(schema) && schema["type"] === "integer" && !Object.hasOwn(schema, SERVER_NUMBER))) {
      throw new GenerationError(`${position}: a request id is a plain integer of a request`);
    }
    for (const schema of named) {
      if (isObject(schema)) {
        found.set(schema, position);
      }
    }
  }
  return found;
}

/** The committed `classes.json`. @returns {unknown} */
export function committedClasses() {
  /** @type {unknown} */
  const parsed = JSON.parse(readFileSync(CLASSES, "utf8"));
  return parsed;
}

const HEADER = `/**
 * Generated by \`scripts/generate-types.mjs\` from \`schemas/openapi.json\`: do not edit. Run
 * \`npm run generate\`; CI fails while this file differs from what the document gives (D419).
 */
`;

const TYPES_HEADER = `${HEADER}import type { ServerNumber } from "../box";
import type { RequestId } from "../ids";
import type { JsonIn, JsonInNull, JsonOut } from "../json";

`;

/**
 * The types of the document, rendered by openapi-typescript with the transform (`marks: false`
 * renders it without, for the gate that shows why the transform exists). `ids` are the request
 * ids' positions (none by default; `generate` gives the committed `classes.json`'s).
 * @param {JsonObject} document
 * @param {{ marks?: boolean, ids?: readonly string[] }} [options]
 * @returns {Promise<string>}
 */
export async function renderTypes(document, options = {}) {
  const marks = options.marks ?? true;
  const found = census(document);
  /** @type {Map<JsonObject, string>} */
  const ids = marks ? idSchemas(document, options.ids ?? []) : new Map();
  /** @type {Set<JsonObject>} */
  const reachedIds = new Set();
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
    if (ids.has(schema)) {
      reachedIds.add(schema);
      return ts.factory.createTypeReferenceNode("RequestId");
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
    const missedIds = [...ids].filter(([node]) => !reachedIds.has(node)).map(([, at]) => at);
    if (missedIds.length > 0) {
      throw new GenerationError(`the transform reached no request id at ${missedIds.join(", ")}`);
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
  const dataset = named.includes("dataset") ? ", path.dataset" : "";
  return `
/** \`${method} ${template}\` */
export function ${name}(${signature.join(", ")}): Route<"${name}"> {
    return route("${name}", "${method}", \`${url}${query}\`${dataset});
}
`;
}

/**
 * Generate the three files from `document` (`text`: the document's own text, whose digest the
 * typed answers' table records; the committed document's by default).
 * @param {JsonObject} document
 * @param {string} [text]
 * @returns {Promise<{ openapi: string, routes: string, shapes: string }>}
 */
export async function generate(document, text = readFileSync(DOCUMENT, "utf8")) {
  let shapes;
  try {
    shapes = renderShapes(document, text);
  } catch (error) {
    throw error instanceof ShapeError ? new GenerationError(`the typed answers' table: ${error.message}`) : error;
  }
  return { openapi: await renderTypes(document, { ids: idPositions(committedClasses()) }), routes: renderRoutes(document), shapes };
}

/**
 * @param {string[]} argv
 * @param {(line: string) => void} say
 * @param {{ document: string, outputs: { openapi: string, routes: string, shapes?: string } }} [files] where the
 *   document is read and the outputs written (the committed ones but in a test; an output not
 *   given is neither checked nor written)
 * @returns {Promise<number>}
 */
export async function main(argv, say, files = { document: DOCUMENT, outputs: OUTPUTS }) {
  const check = argv.length === 1 && argv[0] === "--check";
  if (argv.length > 1 || (argv.length === 1 && !check)) {
    say("usage: generate-types.mjs [--check]");
    return 2;
  }
  const text = readFileSync(files.document, "utf8");
  /** @type {unknown} */
  const document = JSON.parse(text);
  if (!isObject(document)) {
    say("generate-types: the document is no object");
    return 2;
  }
  let made;
  try {
    made = await generate(document, text);
  } catch (error) {
    if (error instanceof GenerationError) {
      say(`generate-types: ${error.message}`);
      return 1;
    }
    throw error;
  }
  const outputs = files.outputs;
  const stale = /** @type {const} */ (["openapi", "routes", "shapes"]).filter((key) => {
    const target = outputs[key];
    if (target === undefined) {
      return false;
    }
    let current = null;
    try {
      current = readFileSync(target, "utf8");
    } catch {
      // missing: stale
    }
    return current !== made[key];
  });
  if (check) {
    for (const key of stale) {
      say(`generate-types: ${path.relative(WEB, outputs[key] ?? "")} is stale: run npm run generate`);
    }
    return stale.length === 0 ? 0 : 1;
  }
  for (const key of stale) {
    writeFileSync(outputs[key] ?? "", made[key]);
    say(`generate-types: wrote ${path.relative(WEB, outputs[key] ?? "")}`);
  }
  return 0;
}

if (process.argv[1] !== undefined && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  process.exitCode = await main(process.argv.slice(2), (line) => {
    process.stdout.write(`${line}\n`);
  });
}
