// @ts-check
/**
 * The lint (D418, D419): typescript-eslint's `strictTypeChecked` over every file of the package,
 * `strict-boolean-expressions` with no nullable or non-boolean condition allowed (a boxed `0` is
 * an object, so truthy: `hidden={n}` would render `hidden=""`), no inline configuration
 * (`noInlineConfig`: an `eslint-disable` comment silences nothing and is itself reported), and for
 * the app the policy's rules in layers. **The app is every file of the package but the tooling**
 * (`scripts/tooling.mjs`: the configurations, `scripts/`, `plugins/`, `tests/` and `e2e/`, which run
 * in Node and never ship), so a new directory has the app's rules by default; `src/api/` is a layer
 * of its own. A file outside every tsconfig is a parse error (loud), and one added to
 * `tsconfig.app.json` is the app's. The build's gate holds besides that no module outside `src/`
 * reaches either entry (`plugins/gate.ts`).
 *
 * **No HTML or script sink (D418).** Trusted Types is the barrier at run time (D411); the sink
 * rules are the lint's defence in depth, and they ban names, not spellings: every name in
 * `SINK_NAMES` (one table) is refused as an identifier (a property, a key, a binding, a call), a
 * string or template key or argument, a computed member, a JSX attribute or element name,
 * whatever the position or the case. Beside the table: `document.write` and `writeln`, a computed
 * member of `globalThis`, `window`, `self`, `top`, `parent` or `frames`, an `on...` or `srcdoc`
 * attribute set by `setAttribute` (and `setAttribute` or `createElement` with a name not written
 * out), and a `javascript:` string. What the lint sees, positively: a sink only where its name is
 * written out, as an identifier, a property, a string or template, or a JSX name (and
 * `document.write` and `writeln` on `document` or `x.document`), and the globals `Function`,
 * `Reflect` and `DOMParser` by reference. A sink reached any other way (an alias such as
 * `ownerDocument` or a destructuring, `.constructor`, a computed or built name, a string a URL
 * parser reads as `javascript:`) is Trusted Types' and the policy's to stop at run time.
 *
 * **One place of I/O, one decoder (D419).** `IO_NAMES` (`fetch`, `XMLHttpRequest`, `Response`,
 * `Request`, `EventSource`, `WebSocket`) are refused in every position in `src/` but
 * `src/api/client.ts`, and `.json` as a member or a destructured key everywhere in the app (the
 * client reads bodies through its capped stream reader alone, and seals the rest at run time).
 * `IO_NAMES` hold what the run-time seal cannot reach: the Cache API (`caches`, `CacheStorage`), `sendBeacon`,
 * `serviceWorker`, `XMLHttpRequest`, another realm's `fetch` by name; a JSON module import
 * (`import(url, { with: { type: "json" } })`) is a dynamic import of a name not written out or of
 * `api/` past the index, which the import rules refuse.
 *
 * **No number a component can compute with (D419).** Outside `src/api/` (and, from M5.1c-2,
 * `format.ts` and the layout modules), `NUMBER_NAMES` are refused in every position: `BigInt`,
 * `Number`, `String`, `parseInt`, `parseFloat`, `isNaN`, `isFinite`, `Math`, `JSON` and
 * `valueAsNumber`, as identifiers (a callee, a value such as `.map(Number)`, a property such as
 * `globalThis.parseInt` or `document.defaultView?.Number`, a key, a binding), computed members,
 * destructured keys and aliases (`const j = JSON`); so are unary `+`, every bitwise operator,
 * `.constructor`, `Object.assign`, `Object.defineProperty` and the like, a computed member named by
 * a template, a dynamic `import()` of a name not written out, and every type assertion
 * (`consistent-type-assertions: never`, which `src/api/decode.ts` and `src/api/box.ts` alone
 * escape). Outside `src/api/`, every import of the client is of `src/api/index.ts`; inside it,
 * `box` and `textOf` are `decode.ts`'s alone and `decode` and `revive` `client.ts`'s alone. The
 * box makes every conversion throw at run time besides; the lint is defence in depth. The `api/` import patterns match without regard to case (the rule's default for a `regex`, `caseSensitive: false`, which the corpus holds: `./API/Box`), as a case-insensitive file system resolves them.
 *
 * **What the lint sees, and what it does not (D419).** It reads names written out: an identifier
 * (a callee, a value, a property, a key, a binding), a string key or computed member, a destructured
 * key, an alias of a name written out. It does not see a member of an alias of the global object
 * named by a computed expression (`const w = window; w[k]`, `k` built at run time or a literal in a
 * variable), an overload signature, `declare function`, `declare const` or `declare global` (types
 * taken on trust), or a type a generic argument states. The box throws on a number's conversion at
 * run time, and that is all it protects (numbers): nothing stops, at run time, I/O reached by a
 * built name. That is a documented residual outside the threat model, which is a component's
 * accident and a lazy hand, not deliberate obfuscation inside the operator's own bundle (the
 * plan's section 0). No module under `src/` is bundled unlinted (packages and the documents the
 * build starts from are, and are the gate's and the lockfile's): the extensions under `src/` are
 * one table (`scripts/tooling.mjs`) that the build's gate and the import-graph test read too, a
 * path with a dot-led segment is refused, an ambient `declare module` and `require` are refused in
 * `src/` (gate (d) refuses a `require`d module as any other), and `@ts-expect-error`, `@ts-ignore`
 * and `@ts-nocheck` are banned.
 *
 * **No builtin patched (D419).** Patching a builtin is deliberate code, out of the box's scope,
 * and refused anyway: `no-extend-native`, an assignment to or a `delete` of a member of a builtin
 * or of `X.prototype`, and `Object.defineProperty`, `defineProperties` and `setPrototypeOf`
 * (which `client.ts` alone calls, to seal the body readers). Outside `src/api/` the prototype
 * chain is not reached at all (`prototype`, `getPrototypeOf`, `setPrototypeOf`, `__proto__`: an
 * alias such as `const p = TextDecoder.prototype; p.decode = g` patches without an assignment to a
 * member of a prototype, which only the names refuse), and neither is a type predicate (`v is
 * number`, a type the compiler takes on trust: a lie `strictTypeChecked` does not see). A generic
 * type argument (`import.meta.glob<T>(...)`, `JSON.parse<T>`) stays accepted: the box throws at run
 * time on whatever it is called as. `import.meta.glob`, which resolves a module the source never
 * names, is refused in all of `src/` (the gate checks the graph it makes).
 */
import js from "@eslint/js";
import { defineConfig } from "eslint/config";
import tseslint from "typescript-eslint";

import { SCRIPT_EXTENSIONS, TOOLING_PATTERNS } from "./scripts/tooling.mjs";

const SINK = "No HTML or script sink: the policy refuses one (D411).";
const IO = "I/O is src/api/client.ts's alone, and a body is read by its decoder alone (D419).";
const NUMBER = "No conversion of a value to a number or digits outside src/api/: a server number is shown, never computed with (D419).";
const IMPORT = "Outside src/api/, the client is imported from src/api/index.ts alone (D419).";
const PATCH = "No builtin is patched (D419).";
const PROTOTYPE = "The prototype chain is src/api/'s alone: no builtin is reached through it to be patched (D419).";
const PREDICATE = "No type predicate outside src/api/: it states a type the compiler takes on trust (D419).";
/** The files the lint reads as code: every source file of the package. */
const CODE = `**/*.{${SCRIPT_EXTENSIONS.map((extension) => extension.slice(1)).join(",")}}`;

const REQUIRE = "No require in src/: it imports a module no import statement names (the gate's graph refuses what it loads, D419).";
const AMBIENT = "No ambient module declaration in src/: it makes the type checker accept a module the lint never read (D419).";
const GLOB = "No import.meta.glob: it imports modules the source does not name (D419).";

/** Every name the app's source may not write, in any position (the lint's one table). Matched
 * whole and without regard to case, as an `Identifier`, a `JSXIdentifier` (attribute and element
 * names), a string `Literal` and a `TemplateElement`, but for the names of `SINK_GLOBALS`, which
 * are not matched as strings (`'function'` is what `typeof` compares to; a global is reached by
 * name or by a computed member of the global object, which has a rule of its own). */
const SINK_NAMES = [
  "innerHTML",
  "outerHTML",
  "insertAdjacentHTML",
  "srcdoc",
  "createContextualFragment",
  "setHTMLUnsafe",
  "parseHTMLUnsafe",
  "DOMParser",
  "dangerouslySetInnerHTML",
  "setAttributeNS",
  "createAttribute",
  "createAttributeNS",
  "setAttributeNode",
  "setAttributeNodeNS",
  "script",
  "Reflect",
  "Function",
];

/** The names of the table that are globals, banned again as references to the global. */
const SINK_GLOBALS = ["Function", "Reflect", "DOMParser"];

/** The names of I/O, `src/api/client.ts`'s alone (matched exactly). */
const IO_NAMES = ["fetch", "XMLHttpRequest", "Response", "Request", "EventSource", "WebSocket", "caches", "CacheStorage", "serviceWorker", "sendBeacon"];

/** The places a page can keep or pass a value beyond its own memory (D423): storage, cookies,
 * another context's messages. Refused in every position in all of `src/`, so that a secret (the
 * curator token, a handle, the CSRF token, or a copy of one, `btoa(token)`) has nowhere to go
 * but the client's closure (matched exactly). */
const STORAGE_NAMES = ["localStorage", "sessionStorage", "indexedDB", "IDBFactory", "cookieStore", "BroadcastChannel", "postMessage", "SharedWorker", "MessageChannel"];

/** The names of the prototype chain, `src/api/`'s alone (matched exactly). */
const PROTOTYPE_NAMES = ["prototype", "getPrototypeOf", "setPrototypeOf", "__proto__"];

/** The names that turn a value into a number or digits, `src/api/`'s alone (matched exactly). */
const NUMBER_NAMES = ["BigInt", "Number", "String", "parseInt", "parseFloat", "isNaN", "isFinite", "Math", "JSON", "valueAsNumber"];

/** The builtins no code assigns a member of. */
const BUILTINS = [
  "JSON",
  "Number",
  "String",
  "RegExp",
  "Object",
  "Reflect",
  "Math",
  "Array",
  "Function",
  "Symbol",
  "BigInt",
  "Promise",
  "Map",
  "Set",
  "WeakMap",
  "WeakSet",
  "Proxy",
  "Intl",
  "Date",
  "Error",
  "TypeError",
  "Response",
  "Request",
  "Headers",
  "TextDecoder",
  "URL",
  "URLSearchParams",
  "globalThis",
  "window",
  "self",
];

const NAME = `/^(${SINK_NAMES.join("|")})$/i`;
const NAME_AS_TEXT = `/^(${SINK_NAMES.filter((name) => name !== "Function" && name !== "Reflect").join("|")})$/i`;

/** A regular expression of selectors matching any of `names` exactly.
 * @param {readonly string[]} names */
const exactly = (names) => `/^(${names.join("|")})$/`;

/** A call of a method or function named `method`, written `x.method(`, `method(` or `x["method"](`.
 * @param {string} method */
const called = (method) =>
  `CallExpression:matches([callee.name='${method}'], [callee.property.name='${method}'], [callee.property.value='${method}'])`;

/** Not a name written out: neither a string nor a template without a substitution.
 * @param {number} position */
const notWritten = (position) =>
  `:not([arguments.${String(position)}.type='Literal']):not([arguments.${String(position)}.type='TemplateLiteral'][arguments.${String(position)}.expressions.length=0])`;

/** @typedef {{ selector: string, message: string }} Restriction */

/** The sink rules (D418), every file of `src/`. @type {Restriction[]} */
const SINK_SYNTAX = [
  ...["Identifier", "JSXIdentifier"].map((type) => ({ selector: `${type}[name=${NAME}]`, message: SINK })),
  { selector: `Literal[value=${NAME_AS_TEXT}]`, message: SINK },
  { selector: `TemplateElement[value.cooked=${NAME_AS_TEXT}]`, message: SINK },
  {
    selector:
      "MemberExpression:matches([property.name=/^write(ln)?$/], [property.value=/^write(ln)?$/]):matches([object.name='document'], [object.property.name='document'])",
    message: SINK,
  },
  {
    selector: "MemberExpression[computed=true][object.name=/^(globalThis|window|self|top|parent|frames)$/]",
    message: "No member of the global object named by a computed expression (it can name a sink).",
  },
  { selector: `${called("setAttribute")}[arguments.0.type='Literal'][arguments.0.value=/^(on|srcdoc$)/i]`, message: SINK },
  {
    selector: `${called("setAttribute")}:not([arguments.0.type='Literal'])`,
    message: "No attribute named at run time (it can name an on... handler or srcdoc).",
  },
  {
    selector: "MemberExpression[property.name='setAttribute']:not(CallExpression > MemberExpression.callee)",
    message: "setAttribute is only called, so that the name it sets is the one written out.",
  },
  { selector: `${called("createElement")}${notWritten(0)}`, message: "No element named at run time (it can name a script)." },
  { selector: `${called("createElementNS")}${notWritten(1)}`, message: "No element named at run time (it can name a script)." },
  { selector: "Literal[value=/^\\s*javascript:/i]", message: SINK },
  { selector: "TemplateElement[value.cooked=/^\\s*javascript:/i]", message: SINK },
];

/** A table of names refused in every position: as an identifier (a reference, a callee, a value,
 * a property, a key, a binding, a type), as a computed member or a destructured key written as a
 * string, and as the initialiser of an alias.
 * @param {readonly string[]} names @param {string} message @returns {Restriction[]} */
const everywhere = (names, message) => [
  { selector: `Identifier[name=${exactly(names)}]`, message },
  { selector: `MemberExpression[computed=true][property.value=${exactly(names)}]`, message },
  { selector: `ObjectPattern > Property[key.value=${exactly(names)}]`, message },
];

const STORE = "No storage, cookie or message to another context in src/: a secret lives in the operator client's closure alone (D423).";

/** The storage rules (D423): the names in every position; `history.pushState` in any form, and
 * `history.replaceState` with a state that is not `null` written out, since an entry's state
 * outlives the page (the URL each takes is the operator entry's fixed path, `url.ts`); `cookie` as
 * a member or a destructured key; and the global object's `name`, which outlives a navigation.
 * @type {Restriction[]} */
const STORAGE_SYNTAX = [
  ...everywhere(STORAGE_NAMES, STORE),
  // `cookie` as a member or a destructured key (`document.cookie`), not as a type's key: the
  // generated types name every operation's `cookie` parameters, which are `never`.
  { selector: "MemberExpression:matches([property.name='cookie'], [property.value='cookie'])", message: STORE },
  { selector: "ObjectPattern > Property:matches([key.name='cookie'], [key.value='cookie'])", message: STORE },
  { selector: "Identifier[name='pushState']", message: STORE },
  { selector: "Literal[value='pushState']", message: STORE },
  { selector: `${called("replaceState")}:not([arguments.0.type='Literal'][arguments.0.raw='null'])`, message: STORE },
  { selector: "MemberExpression[property.name='replaceState']:not(CallExpression > MemberExpression.callee)", message: STORE },
  { selector: "MemberExpression[computed=true][property.value='replaceState']", message: STORE },
  { selector: "MemberExpression[object.name=/^(window|self|globalThis|top|parent|frames|opener)$/]:matches([property.name='name'], [property.value='name'])", message: STORE },
];

/** The I/O rules: the names, and every `.json` (D419). @type {Restriction[]} */
const IO_SYNTAX = everywhere(IO_NAMES, IO);

/** `.json`, as a member or a destructured key, anywhere in `src/`. @type {Restriction[]} */
const JSON_READER_SYNTAX = [
  { selector: "MemberExpression:matches([property.name='json'], [property.value='json'])", message: IO },
  { selector: "ObjectPattern > Property:matches([key.name='json'], [key.value='json'])", message: IO },
];

/** `import.meta.glob`, eager or lazy, anywhere in `src/` (the bundler resolves the modules it
 * names, which no import statement states). @type {Restriction[]} */
const GLOB_SYNTAX = [
  { selector: "MemberExpression[object.type='MetaProperty']:matches([property.name=/^glob/], [property.value=/^glob/])", message: GLOB },
];

/** An ambient module (`declare module "*.JS" { ... }`, any name), anywhere in `src/`: it lets the
 * type checker accept an import of a file the lint does not read. @type {Restriction[]} */
const AMBIENT_SYNTAX = [{ selector: "TSModuleDeclaration[id.type='Literal']", message: AMBIENT }];

/** `require`, in any position (a call, a value, `declare const require`), anywhere in `src/`.
 * @type {Restriction[]} */
const REQUIRE_SYNTAX = [{ selector: "Identifier[name='require']", message: REQUIRE }];

/** The patching rules: an assignment to, an update or a `delete` of a member of a builtin or of
 * a prototype. @type {Restriction[]} */
const PATCH_SYNTAX = [
  ...["AssignmentExpression > MemberExpression.left", "UpdateExpression > MemberExpression.argument", "UnaryExpression[operator='delete'] > MemberExpression.argument"].flatMap(
    (place) => [
      { selector: `${place}[object.name=${exactly(BUILTINS)}]`, message: PATCH },
      { selector: `${place}[object.property.name=${exactly(BUILTINS)}]`, message: PATCH },
      { selector: `${place}[object.property.name='prototype']`, message: PATCH },
      { selector: `${place}[property.name='prototype']`, message: PATCH },
      { selector: `${place}:matches([property.name='__proto__'], [property.value='__proto__'])`, message: PATCH },
    ],
  ),
  // `Object`'s own reach: `Object.assign` and the patching calls are no-restricted-properties',
  // which sees `Object.x` and `const { x } = Object`; these are its aliases.
  { selector: "MemberExpression:matches([property.name='Object'], [property.value='Object'])", message: PATCH },
  { selector: "ObjectPattern > Property:matches([key.name='Object'], [key.value='Object'])", message: PATCH },
  { selector: "VariableDeclarator[init.name='Object']", message: PATCH },
  {
    selector: "ObjectPattern > Property:matches([key.name=/^(assign|defineProperty|defineProperties|setPrototypeOf)$/], [key.value=/^(assign|defineProperty|defineProperties|setPrototypeOf)$/])",
    message: PATCH,
  },
];

/** The rules of the prototype chain and of type predicates, outside `src/api/` (D419).
 * @type {Restriction[]} */
const CHAIN_SYNTAX = [
  ...everywhere(PROTOTYPE_NAMES, PROTOTYPE),
  { selector: "TSTypePredicate", message: PREDICATE },
  // A JSON module (`import(url, { with: { type: "json" } })`) is a body read that no seal reaches.
  { selector: "ImportExpression[options]", message: IMPORT },
  { selector: ":matches(ImportDeclaration, ExportNamedDeclaration, ExportAllDeclaration)[attributes.length>0]", message: IMPORT },
];

/** The number rules, outside `src/api/` (D419). @type {Restriction[]} */
const NUMBER_SYNTAX = [
  ...everywhere(NUMBER_NAMES, NUMBER),
  { selector: `VariableDeclarator[init.name=/^(JSON|Number|String|Math|Reflect)$/]`, message: NUMBER },
  { selector: "UnaryExpression[operator='+']", message: NUMBER },
  { selector: "MemberExpression:matches([property.name='constructor'], [property.value='constructor'])", message: NUMBER },
  { selector: "MemberExpression[computed=true][property.type='TemplateLiteral']", message: NUMBER },
  { selector: `ImportExpression[source.value=/(^|\\/)api\\/(?!index(\\.ts)?$)/i]`, message: IMPORT },
  { selector: "ImportExpression:not([source.type='Literal'])", message: IMPORT },
];

/** The internals of `src/api/`: each module's names that its owner alone imports, within
 * `src/api/` (outside it, every import is of the index, or of the modules the operator entry and
 * the harness name). */
const API_INTERNALS = [
  { module: "box", names: ["box", "textOf"], owners: ["src/api/decode.ts"], message: "box and textOf are decode.ts's alone (D419)." },
  { module: "decode", names: ["decode", "revive"], owners: ["src/api/client.ts"], message: "decode is client.ts's alone (D419)." },
  { module: "client", names: ["send"], owners: ["src/api/curator.ts"], message: "send, which sets the operator's credentials, is curator.ts's alone (D423)." },
  { module: "curator", names: ["createCurator"], owners: ["src/api/operator.ts"], message: "createCurator is operator.ts's alone: the page has one operator client (D423)." },
];

/** `no-restricted-imports`' options for a module of `src/api/`: every internal but those it owns.
 * @param {string | null} owner */
const internals = (owner) => ({
  patterns: API_INTERNALS.filter(({ owners }) => owner === null || !owners.includes(owner)).map(({ module, names, message }) => ({
    regex: `(^|/)${module}(\\.ts)?$`,
    importNames: names,
    message,
  })),
});

/** The properties no file of `src/` names, but those `client.ts` needs to seal the readers.
 * @param {boolean} sealing @returns {["error", ...object[]]} */
const properties = (sealing) => [
  "error",
  { object: "Object", property: "assign", message: NUMBER },
  ...(sealing ? [] : [{ object: "Object", property: "defineProperty", message: PATCH }]),
  { object: "Object", property: "defineProperties", message: PATCH },
  { object: "Object", property: "setPrototypeOf", message: PATCH },
  { property: "__proto__", message: PATCH },
  { property: "__defineGetter__", message: PATCH },
  { property: "__defineSetter__", message: PATCH },
];

/** `no-restricted-globals` from tables of names.
 * @param {...[readonly string[], string]} tables @returns {["error", ...object[]]} */
const globals = (...tables) => ["error", ...tables.flatMap(([names, message]) => names.map((name) => ({ name, message })))];

/** The rules every file of `src/` has, whichever its layer.
 * @type {Record<string, import("eslint").Linter.RuleEntry>} */
const SOURCE_RULES = {
  "no-console": "error",
  "no-eval": "error",
  "no-implied-eval": "error",
  "no-new-func": "error",
  "no-extend-native": "error",
  "no-proto": "error",
  "@typescript-eslint/ban-ts-comment": ["error", { "ts-expect-error": true, "ts-ignore": true, "ts-nocheck": true, "ts-check": false }],
};

export default defineConfig(
  { ignores: ["node_modules/", "dist/", "dist-e2e/", "playwright-report/", "test-results/"] },
  {
    linterOptions: { noInlineConfig: true, reportUnusedDisableDirectives: "error" },
  },
  js.configs.recommended,
  tseslint.configs.strictTypeChecked,
  {
    languageOptions: {
      parserOptions: { projectService: true, tsconfigRootDir: import.meta.dirname },
    },
    rules: {
      "no-undef": "off",
      "@typescript-eslint/strict-boolean-expressions": [
        "error",
        {
          allowString: false,
          allowNumber: false,
          allowNullableObject: false,
          allowNullableBoolean: false,
          allowNullableString: false,
          allowNullableNumber: false,
          allowNullableEnum: false,
          allowAny: false,
        },
      ],
    },
  },
  {
    // The app outside the client: every layer, on every file of the package but the tooling and
    // `src/api/`.
    files: [CODE],
    ignores: [...TOOLING_PATTERNS, "src/api/**"],
    rules: {
      ...SOURCE_RULES,
      "no-bitwise": "error",
      "no-restricted-globals": globals([SINK_GLOBALS, SINK], [IO_NAMES, IO], [NUMBER_NAMES, NUMBER], [STORAGE_NAMES, STORE]),
      "no-restricted-properties": properties(false),
      "no-restricted-syntax": [
        "error",
        ...SINK_SYNTAX,
        ...STORAGE_SYNTAX,
        ...IO_SYNTAX,
        ...JSON_READER_SYNTAX,
        ...GLOB_SYNTAX,
        ...AMBIENT_SYNTAX, ...REQUIRE_SYNTAX,
        ...PATCH_SYNTAX,
        ...CHAIN_SYNTAX,
        ...NUMBER_SYNTAX,
      ],
      "@typescript-eslint/consistent-type-assertions": ["error", { assertionStyle: "never" }],
      "@typescript-eslint/no-restricted-imports": ["error", { patterns: [{ regex: "(^|/)api/(?!index(\\.ts)?$)", message: IMPORT }] }],
    },
  },
  {
    // The operator entry: the importer of the page's operator client (`src/api/operator.ts`, D423),
    // which the index does not re-export (its listeners would follow it into the catalogue).
    files: ["src/operator/**"],
    rules: {
      "@typescript-eslint/no-restricted-imports": [
        "error",
        { patterns: [{ regex: "(^|/)api/(?!(index|operator)(\\.ts)?$)", message: IMPORT }] },
      ],
    },
  },
  {
    // The end-to-end harness: the one importer of the oracle's counter (`src/api/oracle.ts`), and
    // a driver of the page's operator client.
    files: ["src/harness/**"],
    rules: {
      "@typescript-eslint/no-restricted-imports": [
        "error",
        { patterns: [{ regex: "(^|/)api/(?!(index|oracle|operator)(\\.ts)?$)", message: IMPORT }] },
      ],
    },
  },
  {
    // The client: the sink, I/O and patching rules; its numbers are its own.
    files: ["src/api/**"],
    rules: {
      ...SOURCE_RULES,
      "no-restricted-globals": globals([SINK_GLOBALS, SINK], [IO_NAMES, IO], [STORAGE_NAMES, STORE]),
      "no-restricted-properties": properties(false),
      "no-restricted-syntax": ["error", ...SINK_SYNTAX, ...STORAGE_SYNTAX, ...IO_SYNTAX, ...JSON_READER_SYNTAX, ...GLOB_SYNTAX, ...AMBIENT_SYNTAX, ...REQUIRE_SYNTAX, ...PATCH_SYNTAX],
      "@typescript-eslint/consistent-type-assertions": ["error", { assertionStyle: "never" }],
      "@typescript-eslint/no-restricted-imports": ["error", internals(null)],
    },
  },
  {
    // The decoder and the box: the one type assertion each.
    files: ["src/api/decode.ts", "src/api/box.ts"],
    rules: {
      "@typescript-eslint/consistent-type-assertions": "off",
    },
  },
  // Each internal's owner imports it (`API_INTERNALS`).
  ...API_INTERNALS.flatMap(({ owners }) => owners).map((owner) => ({
    files: [owner],
    rules: { "@typescript-eslint/no-restricted-imports": /** @type {["error", object]} */ (["error", internals(owner)]) },
  })),
  {
    // The one place of I/O, which seals the body readers.
    files: ["src/api/client.ts"],
    rules: {
      "no-restricted-globals": globals([SINK_GLOBALS, SINK], [STORAGE_NAMES, STORE]),
      "no-restricted-properties": properties(true),
      "no-restricted-syntax": ["error", ...SINK_SYNTAX, ...STORAGE_SYNTAX, ...JSON_READER_SYNTAX, ...GLOB_SYNTAX, ...AMBIENT_SYNTAX, ...REQUIRE_SYNTAX, ...PATCH_SYNTAX],
    },
  },
  {
    // openapi-typescript renders an `allOf` conjunct it cannot type as `unknown` (`X & unknown`,
    // `unknown | unknown`), which these two rules report; `tests/gates/generated.test.ts` holds
    // where an `unknown` may stand and that every property resolves to a type.
    files: ["src/api/generated/openapi.ts"],
    rules: {
      "@typescript-eslint/no-duplicate-type-constituents": "off",
      "@typescript-eslint/no-redundant-type-constituents": "off",
    },
  },
);
