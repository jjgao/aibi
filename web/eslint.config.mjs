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
 * (`consistent-type-assertions: never`, which `src/api/decode.ts`, `src/api/box.ts` and
 * `src/api/ids.ts` alone escape). Outside `src/api/`, every import of the client is of
 * `src/api/index.ts`; inside it, each internal is its owner's alone (`API_INTERNALS`: `box`
 * `decode.ts`'s, `decode` and `revive` `client.ts`'s, and the request half's, D425: `textOf`
 * `decode.ts`'s and `provenance.ts`'s, so that one function, `idAt`, reads a box's text for a
 * request, and `numbers.ts`'s, which reads it for the page alone, D430 (its glyphs, `glyphsOf`,
 * `format.tsx`'s alone, and the sanctioned predicates; both modules held to the number rules above);
 * `requestId`, `originOf` and `stamp` `provenance.ts`'s, `idAt` `curator.ts`'s, `recorder`, `urlOf`
 * and `datasetOf` `client.ts`'s; and the document seam's, D432: `choiceFor`, `choiceText`,
 * `ofTable`, `walkedAs`, the datatypes and the three functions that send a document
 * `document.ts`'s). The
 * box makes every conversion throw at run time besides; the lint is defence in depth. The `api/` import patterns match without regard to case (the rule's default for a `regex`, `caseSensitive: false`, which the corpus holds: `./API/Box`), as a case-insensitive file system resolves them.
 *
 * **The layout's form and the router (D428).** `src/layout/form.ts` (the context that sets the
 * visible form) is imported by `src/layout/` and `src/api/text.tsx` alone (`FORM_PATTERNS`,
 * `FORM_SYNTAX`: an import in any spelling, a dynamic import, an import type), so that each layout
 * component's form is its own and no screen sets one around it; `showText`, the one door from a
 * server text to the DOM, is `src/layout/`'s alone (`SHOW_PATTERNS`, `SHOW_SYNTAX`: the index's pinned
 * export, which only the layout imports, and `./text` is the index's alone), so that no screen shows
 * a text without the isolate and the form a layout component gives it. The router package's names are an
 * allow-list outside `src/router/address.tsx` (`ROUTER_ALLOWED`, per name: `allowImportNames`; any
 * other specifier of the package, a dynamic import and an import type refused), and the page's
 * address is read in the router module alone (`ADDRESS_SYNTAX`: `location`'s `pathname`, `search`,
 * `hash` and `href`, a `.location` of the window or the document, `document.URL`, `referrer`,
 * `baseURI`, `documentURI`);
 * `tests/gates/router.test.ts` and `tests/gates/layout-form.test.ts` hold each with its cells (the
 * latter also scans the symbols the program resolves, which a re-export in a layout module cannot get past).
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
 *
 * **No channel out of the page's memory (D423).** Two layers. By name: the storages and the Cache
 * API (`STORAGE_NAMES`) in every position, the channels (`CHANNEL_SYNTAX`: `navigator.storage`,
 * `navigator.clipboard`, `history.pushState` and `replaceState`, a write to `location` or a member
 * of it, `location.replace`, `assign`, `hash` and `href`, a write to `document.title`,
 * `window.open` and the window's `name`), and the page's objects (`PAGE_OBJECTS`) by direct member
 * access alone (`ALIAS_SYNTAX`). By type (`aibi/page-objects`, `scripts/page-objects.mjs`, the checker
 * the lint already loads; the window's channels that are globals, `open` or `postMessage`, are refused
 * by the symbol they resolve to): whatever a value is called, one of the types of `PAGE_TYPES` (or one that
 * holds one) may stand only as the object of a member access that is no channel of its type, no
 * member of one is written, and a member that reaches one (`REACHERS`: `defaultView`,
 * `ownerDocument`, `view`, ...) is refused, so that `const l = window.location`, `el.ownerDocument`,
 * `event.view` and a returned value are seen. `history.replaceState` stands in `src/operator/url.ts`,
 * which is nothing but one function and its one call, the path a literal (`URL_SYNTAX`,
 * `URL_FILE_SYNTAX`, the rule's option), and in the router module, `src/router/address.tsx` (D428:
 * `redirectAddress`'s one call, to `/` or to `/#/datasets/` and a value the checker types
 * `DatasetId`: `ROUTER_SYNTAX`, the rule's option), alone; `src/operator/` has no module-level `let` or `var`
 * (`OPERATOR_SYNTAX`). What it does not see is D419's residual: a value the checker types `any`, a
 * `declare` or an assertion that lies, another realm, a patched builtin, and the channels that are no
 * page object's member (an anchor's `href` and `click()`, a form's `submit()`, `<meta refresh>`,
 * `img.src`, ...: D423 lists them). `tests/gates/operator.test.ts` holds each channel in each
 * position and a generated table of every type, reacher, position and channel;
 * `tests/gates/page-types.test.ts` holds the rule's lists against what the DOM library declares.
 *
 * **The display lint (D429; D427's R90–R93): the line at the sink, by type** (`scripts/lint/display.mjs`,
 * the inline plugin `display`). Whatever a text's source (response data, the address, an operator's
 * answer), no value whose type holds a non-literal string, number or bigint stands at a JSX position or
 * as what a component returns (`display/raw-text`, every file of the app but the door,
 * `src/api/text.tsx`; a render slot reached other than at a JSX position is R90's flow class, OPEN
 * until 2b-1L2's R90f); the elements, the
 * attributes and the bare specifiers are allow-lists (`display/elements`, `display/specifiers`: no
 * `react/jsx-runtime`, `createElement`, `react-dom/server`; `react-dom/client` in the entries alone);
 * outside `src/api/`, no write to the DOM and no function of the DOM library but a reviewed list
 * (`display/dom`), and no operator answer's `body` or `codes` (`display/answers`); the address's members
 * by type, beside the names above (`display/address`). `tests/unit/lint-display.test.ts` lints each
 * row's corpus through this configuration; `tests/gates/display-lint.test.ts` holds the tables and the
 * wiring for every file of `src/`.
 */
import js from "@eslint/js";
import { defineConfig } from "eslint/config";
import tseslint from "typescript-eslint";

import { display, ENTRIES, ENTRY_SPECIFIERS, ROUTER_SPECIFIERS, SPECIFIERS } from "./scripts/lint/display.mjs";
import { pageObjects } from "./scripts/page-objects.mjs";
import { SCRIPT_EXTENSIONS, SRC_EXTENSIONS, TOOLING_PATTERNS } from "./scripts/tooling.mjs";

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
const AMBIENT =
  "No ambient module, namespace or global declaration in src/: it makes the type checker accept a module the lint never read, or merges a declaration into the page's own types (D419, D423).";
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

/** The places a page can keep or pass a value beyond its own memory (D423): storage, the Cache
 * API, cookies, another context's messages. Refused in every position in all of `src/`, so that a
 * secret (the curator token, a handle, the CSRF token, or a copy of one, `btoa(token)`) has nowhere
 * to go but the client's closure (matched exactly). */
const STORAGE_NAMES = [
  "localStorage",
  "sessionStorage",
  "indexedDB",
  "IDBFactory",
  "cookieStore",
  "BroadcastChannel",
  "postMessage",
  "SharedWorker",
  "MessageChannel",
  "Worker",
  "caches",
  "CacheStorage",
];

/** The global object, and the names that lead to it (`defaultView` is a member that gives the
 * window of a document). */
const GLOBAL_OBJECTS = ["globalThis", "window", "self", "top", "parent", "frames", "opener"];

/** What the page's channels hang on: the global object and the objects of it that carry a secret
 * out of the page's memory (the address, the history, the navigator, the document). Used by direct
 * member access alone: `window.history.length`, never `const h = history`. */
const PAGE_OBJECTS = [...GLOBAL_OBJECTS, "location", "history", "navigator", "document"];

/** The names of the prototype chain, `src/api/`'s alone (matched exactly). */
const PROTOTYPE_NAMES = ["prototype", "getPrototypeOf", "setPrototypeOf", "__proto__"];

/** The names that turn a value into a number or digits, `src/api/`'s alone (matched exactly). */
const NUMBER_NAMES = ["BigInt", "Number", "String", "parseInt", "parseFloat", "isNaN", "isFinite", "Math", "JSON", "valueAsNumber"];

/** The names of a rounding or a formatting of a number, refused in `src/api/numbers.ts` and
 * `src/api/format.tsx` alone (D430): the two modules that read and show a number's text, where
 * `Intl` and these methods would round it. The lint holds these names, D419's `NUMBER_NAMES` and
 * the number syntax, and no spelling else: a conversion by another name (`Float32Array.of(n)[0]`,
 * a type predicate narrowing a number) is held by the tests' number corpus alone. */
const ROUNDING_NAMES = ["Intl", "toFixed", "toPrecision", "toExponential", "toLocaleString"];

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
/** The page's own constructors, which no code tests a value against. */
const PAGE_CONSTRUCTORS = ["Window", "Location", "Navigator", "Document", "HTMLDocument", "History", "Storage", "StorageManager", "Clipboard", "CacheStorage", "Cache", "ShadowRoot"];

const ALIAS =
  "No alias of window, globalThis, self, top, parent, frames, opener, location, history, navigator or document, no destructuring from one and none passed as an argument: each is used by direct member access alone (D423).";
const CHANNEL = "No channel out of the page's memory in src/ (the address, the history, the title, the window's name, the clipboard, a new window, the origin private file system): a secret lives in the operator client's closure alone (D423).";

/** A member expression whose object is one of `names`, written out (`navigator.x`) or reached as a
 * member of that name (`window.navigator.x`, `self.navigator.x`); the global object's names are
 * also reached by `defaultView`.
 * @param {readonly string[]} names */
const onObject = (names) =>
  `MemberExpression:matches([object.name=${exactly(names)}], [object.property.name=${exactly(names)}]${names.includes("window") ? ", [object.property.name='defaultView']" : ""})`;

/** The channel rules (D423), by name and by position. The page's objects are used by direct member
 * access alone (`ALIAS_SYNTAX`: no alias, no destructuring, no argument, no computed member), so
 * that these rules see every use of a channel; a channel is then refused as a member of its
 * object, wherever it stands (a call, an assignment, an optional chain, a read). @type {Restriction[]} */
const CHANNEL_SYNTAX = [
  // The origin private file system (`navigator.storage.getDirectory`), as persistent as `indexedDB`.
  { selector: `${onObject(["navigator"])}[property.name='storage']`, message: CHANNEL },
  // The clipboard outlives the page and is read by whatever the person pastes into; a lock's name is
  // read by every context of the origin (`navigator.locks.query()`, the class of `BroadcastChannel`);
  // a protocol handler keeps a URL in the browser's settings; the media session shows text to the system.
  { selector: `${onObject(["navigator"])}[property.name=/^(clipboard|locks|registerProtocolHandler|mediaSession)$/]`, message: CHANNEL },
  // `document.open(url, name, features)` opens a window, as `window.open` does.
  { selector: `${onObject(["document"])}[property.name='open']`, message: CHANNEL },
  // `history.pushState` and `history.replaceState` take a state that outlives the page and a URL;
  // `src/operator/url.ts` alone calls `replaceState`, with a fixed state and path (`URL_SYNTAX`).
  { selector: "Identifier[name=/^(pushState|replaceState)$/]", message: CHANNEL },
  { selector: "Literal[value=/^(pushState|replaceState)$/]", message: CHANNEL },
  // The address: written (`location = x`, `window.location = x`, `location.x = y`), navigated
  // (`replace`, `assign`) or carrying a secret in its `hash` or `href`, read or written.
  { selector: "AssignmentExpression:matches([left.name='location'], [left.property.name='location'])", message: CHANNEL },
  { selector: "AssignmentExpression:matches([left.object.name='location'], [left.object.property.name='location'])", message: CHANNEL },
  { selector: `${onObject(["location"])}[property.name=/^(replace|assign|hash|href)$/]`, message: CHANNEL },
  // The title is kept in session history.
  {
    selector: "AssignmentExpression[left.property.name='title']:matches([left.object.name='document'], [left.object.property.name='document'])",
    message: CHANNEL,
  },
  // A new window's address, and the window's name, which outlives a navigation.
  { selector: `${onObject(GLOBAL_OBJECTS)}[property.name='open']`, message: CHANNEL },
  { selector: "CallExpression[callee.name='open']", message: CHANNEL },
  { selector: `${onObject(GLOBAL_OBJECTS)}:matches([property.name='name'], [property.value='name'])`, message: CHANNEL },
];

/** The page's objects by direct member access alone (D423): no reference to one that is not the
 * object of a member (an alias, an argument, a returned value, a destructuring's source, a
 * binding of the name), and no computed member of one or of a member of that name. @type {Restriction[]} */
const ALIAS_SYNTAX = [
  {
    selector: `Identifier[name=${exactly(PAGE_OBJECTS)}]:not(MemberExpression > Identifier.object):not(MemberExpression[computed=false] > Identifier.property):not(:matches(Property, PropertyDefinition, TSPropertySignature, MethodDefinition, TSMethodSignature, AccessorProperty)[computed=false] > Identifier.key)`,
    message: ALIAS,
  },
  { selector: `MemberExpression[computed=true]:matches([object.name=${exactly(PAGE_OBJECTS)}], [object.property.name=${exactly(PAGE_OBJECTS)}])`, message: ALIAS },
  // A test against the page's own constructors (`e.currentTarget instanceof Window`).
  { selector: `BinaryExpression[operator='instanceof'][right.name=${exactly(PAGE_CONSTRUCTORS)}]`, message: ALIAS },
];

/** The storage rules (D423): the names in every position; `cookie` as a member or a destructured
 * key; the channels by name; the page's objects by direct member access alone.
 * @type {Restriction[]} */
const STORAGE_SYNTAX = [
  ...everywhere(STORAGE_NAMES, STORE),
  // `cookie` as a member or a destructured key (`document.cookie`), not as a type's key: the
  // generated types name every operation's `cookie` parameters, which are `never`.
  { selector: "MemberExpression:matches([property.name='cookie'], [property.value='cookie'])", message: STORE },
  { selector: "ObjectPattern > Property:matches([key.name='cookie'], [key.value='cookie'])", message: STORE },
  ...CHANNEL_SYNTAX,
  ...ALIAS_SYNTAX,
];

/** The storage rules of `src/operator/url.ts`, the one file that calls `history.replaceState`
 * (D412: the operator entry takes nothing from its URL and replaces it by its fixed path): its one
 * call states `null`, `""` and `"/curate"`, written out (a literal, no binding), and no other use of
 * the name stands; the type-aware rule (`aibi/page-objects`) holds the rest (the global `history`,
 * the one exported function, once). @type {Restriction[]} */
const URL_SYNTAX = [
  ...STORAGE_SYNTAX.filter(({ selector }) => !selector.includes("pushState|replaceState")),
  { selector: "Identifier[name='pushState']", message: CHANNEL },
  { selector: "Literal[value=/^(pushState|replaceState)$/]", message: CHANNEL },
  {
    selector:
      "Identifier[name='replaceState']:not(CallExpression[arguments.length=3][arguments.0.raw='null'][arguments.1.type='Literal'][arguments.1.value=''][arguments.2.type='Literal'][arguments.2.value='/curate'] > MemberExpression.callee[object.name='history'] > Identifier.property)",
    message: CHANNEL,
  },
];

/** The storage rules of `src/router/address.tsx`, the router module (D428): the one file of the
 * catalogue that writes the address, its one call of `history.replaceState` stating `null` and `""`
 * written out; the type-aware rule (`aibi/page-objects`, its option) holds the address it writes
 * (`/`, or `/#/datasets/` and a `DatasetId`), the global `history` and the one exported function,
 * `redirectAddress`, once. @type {Restriction[]} */
const ROUTER_SYNTAX = [
  ...STORAGE_SYNTAX.filter(({ selector }) => !selector.includes("pushState|replaceState")),
  { selector: "Identifier[name='pushState']", message: CHANNEL },
  { selector: "Literal[value=/^(pushState|replaceState)$/]", message: CHANNEL },
  {
    selector:
      "Identifier[name='replaceState']:not(CallExpression[arguments.length=3][arguments.0.raw='null'][arguments.1.type='Literal'][arguments.1.value=''] > MemberExpression.callee[object.name='history'] > Identifier.property)",
    message: CHANNEL,
  },
];

const URL_FILE = "src/operator/url.ts is one exported function, forgetAddress, with no parameter, whose body is its one call of history.replaceState (D412, D423).";

/** `src/operator/url.ts` is nothing but `export function forgetAddress(): void { history.replaceState(null, "", "/curate"); }`:
 * no import, no binding, no second statement, no parameter. @type {Restriction[]} */
const URL_FILE_SYNTAX = [
  { selector: "Program[body.length!=1]", message: URL_FILE },
  { selector: "Program > :not(ExportNamedDeclaration[declaration.type='FunctionDeclaration'][declaration.id.name='forgetAddress'])", message: URL_FILE },
  { selector: "FunctionDeclaration[params.length>0]", message: URL_FILE },
  { selector: "FunctionDeclaration[async=true], FunctionDeclaration[generator=true]", message: URL_FILE },
  { selector: "FunctionDeclaration > BlockStatement[body.length!=1]", message: URL_FILE },
  { selector: "FunctionDeclaration > BlockStatement > :not(ExpressionStatement)", message: URL_FILE },
  { selector: "FunctionDeclaration > BlockStatement > ExpressionStatement > :not(CallExpression)", message: URL_FILE },
];

const MUTABLE =
  "No mutable module binding (let, var) in src/operator/: a value kept in module scope outlives the call that handed it over and no test of the React tree sees it (D423).";

/** The operator entry's modules hold no mutable module binding. @type {Restriction[]} */
const OPERATOR_SYNTAX = [
  { selector: "Program > VariableDeclaration[kind!='const']", message: MUTABLE },
  { selector: "Program > ExportNamedDeclaration > VariableDeclaration[kind!='const']", message: MUTABLE },
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

/** An ambient module, namespace or global declaration (`declare module "*.JS" { ... }`, `declare
 * global { ... }`, `namespace Window { ... }`, any name), anywhere in `src/`: it lets the type checker
 * accept an import of a file the lint does not read, and merges declarations into the library's types
 * (the type rule's check of a global is by symbol, and a merge is `some`). @type {Restriction[]} */
const AMBIENT_SYNTAX = [{ selector: "TSModuleDeclaration", message: AMBIENT }];

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
  // A type import of a module of `src/api/` past the index (`typeof import("../api/client")`,
  // `import("../api/json").JsonOut`): a type alone, harmless at run time, refused all the same so
  // that the import ban has no spelling around it (m14 of the typed answers' round 4).
  { selector: "TSImportType[source.value=/(^|\\/)api\\/(?!index(\\.ts)?$)/i]", message: IMPORT },
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
  { module: "box", names: ["box"], owners: ["src/api/decode.ts"], message: "box is decode.ts's alone (D419)." },
  {
    module: "box",
    names: ["textOf"],
    owners: ["src/api/decode.ts", "src/api/provenance.ts", "src/api/numbers.ts"],
    message:
      "textOf, the reading of a box's text, is decode.ts's, provenance.ts's idAt's and numbers.ts's alone (its glyphs for format.tsx and its predicates, D430): no member of the operator client reads a box's digits (D425).",
  },
  {
    module: "numbers",
    names: ["glyphsOf", "countGlyphs", "remainingOf"],
    owners: ["src/api/format.tsx"],
    message: "A number's glyphs and the page's counts are format.tsx's alone: the one module that shows a number, as an element (D430).",
  },
  {
    module: "numbers",
    names: ["isCountOf"],
    owners: ["src/api/document.ts"],
    message: "isCountOf, whether a server number is a count the page made, is document.ts's alone: optionsOf asks it once and gives a boolean (D432).",
  },
  { module: "ids", names: ["requestId"], owners: ["src/api/provenance.ts"], message: "requestId, the one maker of a RequestId, is provenance.ts's idAt's alone (D425)." },
  { module: "origin", names: ["stamp"], owners: ["src/api/provenance.ts"], message: "stamp is provenance.ts's alone: a node is stamped as it is decoded (D425)." },
  { module: "origin", names: ["originOf"], owners: ["src/api/provenance.ts"], message: "originOf is provenance.ts's idAt's alone: a node's provenance is read where its id is (D425)." },
  { module: "provenance", names: ["recorder"], owners: ["src/api/client.ts"], message: "recorder is client.ts's alone: send records a 2xx answer's feeding nodes (D425)." },
  { module: "provenance", names: ["idAt"], owners: ["src/api/curator.ts"], message: "idAt, the one function that reads a box's text, is curator.ts's four flows' alone (D425)." },
  { module: "route", names: ["urlOf", "datasetOf"], owners: ["src/api/client.ts"], message: "a route's URL and dataset are client.ts's alone: no component reads a URL (D425)." },
  { module: "decode", names: ["decode", "revive"], owners: ["src/api/client.ts"], message: "decode is client.ts's alone (D419)." },
  { module: "client", names: ["send"], owners: ["src/api/curator.ts"], message: "send, which sets the operator's credentials, is curator.ts's alone (D423)." },
  { module: "curator", names: ["createCurator"], owners: ["src/api/operator.ts"], message: "createCurator is operator.ts's alone: the page has one operator client (D423)." },
  {
    module: "box",
    names: ["boxText", "boxMap"],
    owners: ["src/api/typed.ts"],
    message: "boxText and boxMap are typed.ts's alone: a server text and a map of boxed keys are made by the walk of an answer (D426).",
  },
  {
    module: "box",
    names: ["readText"],
    owners: ["src/api/text.tsx"],
    message: "readText, the reading of a server text, is text.tsx's takers' alone: they give a vocabulary's member, an id, a choice, a boolean or an order, never the text (D426).",
  },
  {
    module: "client",
    names: ["exchange"],
    owners: ["src/api/typed.ts", "src/api/oracle.ts"],
    message: "exchange, whose answer is raw, is typed.ts's (the seam that walks it) and the harness's oracle's alone (D426).",
  },
  {
    module: "typed",
    names: ["walkComponent"],
    owners: ["src/api/oracle.ts"],
    message: "walkComponent is the tests' door to the walk, and the end-to-end harness's oracle's (its corpus, D428): no other module of src/ imports it (D426).",
  },
  {
    module: "text",
    names: ["choiceFor", "choiceText", "ofTable"],
    owners: ["src/api/document.ts"],
    message:
      "choiceFor, choiceText and ofTable are document.ts's alone: optionsOf mints every choice from a describe_dataset answer the walk returned, and documentOf reads each at its role's position (D432).",
  },
  {
    module: "typed",
    names: ["validateDocument", "countCohort", "runAnalysis", "walkedAs"],
    owners: ["src/api/document.ts"],
    message:
      "The functions that send a document, and walkedAs, are document.ts's alone: a document is written from a Draft of choices and typed values, never handed over (D432).",
  },
  {
    module: "datatypes",
    names: ["DATATYPES", "LEFT_OUT"],
    owners: ["src/api/document.ts"],
    message: "The datatypes the seam offers are document.ts's alone (D432).",
  },
  {
    module: "text",
    names: ["showText"],
    owners: ["src/api/index.ts"],
    message: "showText, the one door from a server text to the DOM, is exported by the index alone, for src/layout/: no other module of src/api/ shows a text (D428).",
  },
  {
    module: "visible",
    names: ["visibleForm", "ENGINE"],
    owners: ["src/api/text.tsx"],
    message: "visibleForm, the visible form of a raw text, is showText's alone: the one door from a server text to the DOM (D428).",
  },
];

/** Every spelling the resolver maps a specifier of a module of `src/` to (m15 of round 4, m6 of the
 * typed answers' round 2): none, a source extension of `SRC_EXTENSIONS`' code, and its JavaScript
 * twin (`moduleResolution: bundler` reads `./box.js` as `./box.ts` and `./x.jsx` as `./x.tsx`). */
const SPELLED = [...SRC_EXTENSIONS].filter(([, kind]) => kind === "code").flatMap(([extension]) => [extension.slice(1), extension.slice(1).replace(/^ts/u, "js")]);

/** The visible form's context (`src/layout/form.ts`), every spelling of its specifier: imported by
 * `src/layout/` and `src/api/text.tsx` alone (D428), so that each layout component's form is its
 * own and no screen sets a position's form around one (`tests/gates/layout-form.test.ts` holds the
 * imports the program resolves, and this table's cells). */
const FORM_MODULE = `(^|/)layout/form(\\.(${SPELLED.join("|")}))?$`;
const FORM = "The visible form's context is imported by src/layout/ and src/api/text.tsx alone: each layout component sets its own form, so no screen sets one around a component (D428).";

/** The import patterns of every module but the form's two importers. */
const FORM_PATTERNS = [{ regex: FORM_MODULE, message: FORM }];

/** The same module as a dynamic import or a type import (`import("../layout/form")`,
 * `typeof import("../layout/form")`), anywhere but the form's two importers. @type {Restriction[]} */
const FORM_SYNTAX = [
  { selector: `ImportExpression[source.value=/${FORM_MODULE.replaceAll("/", "\\/")}/i]`, message: FORM },
  { selector: `TSImportType[source.value=/${FORM_MODULE.replaceAll("/", "\\/")}/i]`, message: FORM },
];

/** `showText`, from the index in any spelling (`api`, `api/index`, with or without an extension):
 * the index's pinned export that `src/layout/` alone imports (D428). A named import, an alias, a
 * namespace import, a type import and a re-export are each refused by the rule's `importNames`; the
 * deep path (`api/text`) is refused by `IMPORT` already. `tests/gates/layout-form.test.ts` holds the
 * symbol it resolves to over every file of `src/`, which a layout module's re-export cannot get past.
 * The index's own file is not outside it: nothing imports itself. */
const SHOW_MODULE = `(^|/)api(/index(\\.(${SPELLED.join("|")}))?)?$`;
const SHOW =
  "showText, the one door from a server text to the DOM, is src/layout/'s alone: a screen shows a text through Text, Ref, Segments or Option, which isolate it, mark it as data and set its form (D428).";
const SHOW_PATTERNS = [{ regex: SHOW_MODULE, importNames: ["showText"], message: SHOW }];

/** `showText` from the index as a module of `src/api/` spells it (`.`, `./`, `./index`, with or without
 * an extension): every module of `src/api/` but the index itself (m4 of round 3 of #117: `SHOW_PATTERNS`
 * names the index by its directory, which a sibling does not write). */
const API_INDEX_PATTERNS = [{ regex: `^\\.(/index(\\.(${SPELLED.join("|")}))?)?/?$`, importNames: ["showText"], message: SHOW }];

/** The index as a dynamic import (`import("../api")`) or an import type that names the door
 * (`typeof import("../api").showText`), anywhere but the layout, where it reaches `showText` past the
 * import names. (An import type of the whole index, `typeof import("../api")`, is a type and no
 * door, and stays allowed: `tests/gates/typed.test.ts`.) @type {Restriction[]} */
const SHOW_SYNTAX = [
  { selector: `ImportExpression[source.value=/${SHOW_MODULE.replaceAll("/", "\\/")}/i]`, message: SHOW },
  { selector: `TSImportType[source.value=/${SHOW_MODULE.replaceAll("/", "\\/")}/i][qualifier.name='showText']`, message: SHOW },
];

/** What the app may import of the router package, by specifier and name (D428): an allow-list, so
 * that a reader the package adds, or one this list never heard of (`useMatches`, `useMatch`,
 * `useResolvedPath`, `useHref`, `UNSAFE_LocationContext`, ...), is refused until someone allows it
 * here, with a reason. `tests/gates/router.test.ts` enumerates the installed package's exports and
 * holds each but these refused, and holds the module's own list (`useParams`, the one reader of the
 * address's hash route, `src/router/address.tsx`'s alone).
 * @type {{ module: string, names: string[] }[]} */
const ROUTER_ALLOWED = [
  { module: "react-router", names: ["createHashRouter", "RouteObject"] },
  { module: "react-router/dom", names: ["RouterProvider"] },
];
const ROUTER = "Outside src/router/address.tsx, the router package's names are an allow-list (createHashRouter, RouteObject, RouterProvider): a reader of the address puts its raw text in the DOM (D428).";

/** The router package's import patterns for a file that may name `allowed` of it (the module's own,
 * or the app's): each specifier allows its names, every other specifier that holds the package's
 * name (any other path, `react-router-dom`, a path into `node_modules`) is refused whole.
 * @param {{ module: string, names: string[] }[]} allowed @returns {{ regex: string, allowImportNames?: string[], message: string }[]} */
const routerPatterns = (allowed) => [
  ...allowed.map(({ module, names }) => ({ regex: `^${module}$`, allowImportNames: names, message: ROUTER })),
  // Not anchored at the start: `../../node_modules/react-router/dist/...` is the package too (m4 of round 2 of #117).
  { regex: `^(?!(${allowed.map(({ module }) => module).join("|")})$).*react-router`, message: ROUTER },
];

/** The router package as a dynamic import or a type import (`import("react-router")`,
 * `typeof import("react-router").useParams`), anywhere but where it is allowed whole.
 * @type {Restriction[]} */
const ROUTER_IMPORT_SYNTAX = [
  { selector: "ImportExpression[source.value=/react-router/i]", message: ROUTER },
  { selector: "TSImportType[source.value=/react-router/i]", message: ROUTER },
];

const ADDRESS = "The address is read in src/router/address.tsx alone (D428): location's pathname, search, hash and href, document.URL, referrer, baseURI, documentURI and document.location put the adversary's raw text where it is shown.";

/** The page's address by name, outside the router module: the members of `location` that carry the
 * address (`pathname`, `search`, `hash`, `href`, and `toString` and `valueOf`, which give it), a
 * `.location` of the window, the frames or the document, and the document's URLs. (`origin`, `host`
 * and the like are the server's own, not the adversary's.) Reached by a name written out: D419's
 * residual, as the channel rules'; the page-objects rule holds the types. @type {Restriction[]} */
const ADDRESS_SYNTAX = [
  { selector: `${onObject(["location"])}:matches([property.name=/^(pathname|search|hash|href|toString|valueOf)$/], [property.value=/^(pathname|search|hash|href|toString|valueOf)$/])`, message: ADDRESS },
  { selector: `${onObject(GLOBAL_OBJECTS)}:matches([property.name='location'], [property.value='location'])`, message: ADDRESS },
  { selector: `${onObject(["document"])}:matches([property.name=/^(URL|documentURI|baseURI|referrer|location)$/], [property.value=/^(URL|documentURI|baseURI|referrer|location)$/])`, message: ADDRESS },
];

/** No dynamic `import()` in `src/api/` (MA3 of round 3): an internal's owner is its static
 * importer, and a dynamic import reaches a module's names past `no-restricted-imports`. No module of
 * `src/api/` has one. @type {Restriction[]} */
const API_IMPORT_SYNTAX = [
  { selector: "ImportExpression", message: "No dynamic import in src/api/: each internal is its owner's alone, by a static import (D425)." },
  ...FORM_SYNTAX,
  ...SHOW_SYNTAX,
  ...ROUTER_IMPORT_SYNTAX,
];

/** `no-restricted-imports`' options for a module of `src/api/`: every internal but those it owns. A
 * module is named by every spelling the resolver maps to it: none, `.ts` and `.js` (`moduleResolution:
 * bundler` reads `./box.js` as `./box.ts`, which a pattern of the first two alone let pass, m15 of
 * round 4).
 * @param {string | null} owner */
const internals = (owner) => ({
  patterns: [
    ...API_INTERNALS.filter(({ owners }) => owner === null || !owners.includes(owner)).map(({ module, names, message }) => ({
      regex: `(^|/)${module}(\\.(${SPELLED.join("|")}))?$`,
      importNames: names,
      message,
    })),
    ...(owner === "src/api/text.tsx" ? [] : FORM_PATTERNS),
    ...(owner === "src/api/index.ts" ? [] : API_INDEX_PATTERNS),
    ...routerPatterns([]),
  ],
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

/** The inline plugin of the lint's own rules (the rule: `scripts/page-objects.mjs`). */
const aibi = { rules: { "page-objects": pageObjects } };

/** The syntax rules of a file of `src/` outside `src/api/`, given its storage rules (and whether
 * it may not import the visible form's context, every file but the layout's, and may not read the
 * address, every file but the router module).
 * @param {Restriction[]} storage @param {{ form?: boolean, address?: boolean, show?: boolean }} [lifted] @returns {Restriction[]} */
const appSyntax = (storage, { form = true, address = true, show = true } = {}) => [
  ...SINK_SYNTAX,
  ...storage,
  ...IO_SYNTAX,
  ...JSON_READER_SYNTAX,
  ...GLOB_SYNTAX,
  ...AMBIENT_SYNTAX,
  ...REQUIRE_SYNTAX,
  ...PATCH_SYNTAX,
  ...CHAIN_SYNTAX,
  ...NUMBER_SYNTAX,
  ...(form ? FORM_SYNTAX : []),
  ...(show ? SHOW_SYNTAX : []),
  ...(address ? ADDRESS_SYNTAX : []),
  ...ROUTER_IMPORT_SYNTAX,
];

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
  { plugins: { aibi, display } },
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
      "no-restricted-syntax": ["error", ...appSyntax(STORAGE_SYNTAX)],
      "aibi/page-objects": "error",
      "@typescript-eslint/consistent-type-assertions": ["error", { assertionStyle: "never" }],
      "@typescript-eslint/no-restricted-imports": ["error", { patterns: [{ regex: "(^|/)api/(?!index(\\.ts)?$)", message: IMPORT }, ...FORM_PATTERNS, ...SHOW_PATTERNS, ...routerPatterns(ROUTER_ALLOWED)] }],
    },
  },
  {
    // The layout (D428): each component sets its own form, so the layout alone imports the context
    // that sets it (and `src/api/text.tsx`, the door that reads it).
    files: ["src/layout/**"],
    rules: {
      "no-restricted-syntax": ["error", ...appSyntax(STORAGE_SYNTAX, { form: false, show: false })],
      "@typescript-eslint/no-restricted-imports": ["error", { patterns: [{ regex: "(^|/)api/(?!index(\\.ts)?$)", message: IMPORT }, ...routerPatterns(ROUTER_ALLOWED)] }],
    },
  },
  {
    // The operator entry: the importer of the page's operator client (`src/api/operator.ts`, D423),
    // which the index does not re-export (its listeners would follow it into the catalogue).
    files: ["src/operator/**"],
    rules: {
      "no-restricted-syntax": ["error", ...appSyntax(STORAGE_SYNTAX), ...OPERATOR_SYNTAX],
      "@typescript-eslint/no-restricted-imports": [
        "error",
        { patterns: [{ regex: "(^|/)api/(?!(index|operator)(\\.ts)?$)", message: IMPORT }, ...FORM_PATTERNS, ...SHOW_PATTERNS, ...routerPatterns(ROUTER_ALLOWED)] },
      ],
    },
  },
  {
    // The one file that calls `history.replaceState` (D412: the entry's address is its fixed path).
    files: ["src/operator/url.ts"],
    rules: {
      "no-restricted-syntax": ["error", ...appSyntax(URL_SYNTAX), ...OPERATOR_SYNTAX, ...URL_FILE_SYNTAX],
      "aibi/page-objects": ["error", { replaceState: "forgetAddress" }],
    },
  },
  {
    // The router module (D428): the one file of the catalogue that reads the address and writes it
    // (`redirectAddress`: `/datasets/<id>` replaced by `/#/datasets/<id>` or `/`).
    files: ["src/router/address.tsx"],
    rules: {
      "no-restricted-syntax": ["error", ...appSyntax(ROUTER_SYNTAX, { address: false })],
      "@typescript-eslint/no-restricted-imports": [
        "error",
        { patterns: [{ regex: "(^|/)api/(?!index(\\.ts)?$)", message: IMPORT }, ...FORM_PATTERNS, ...SHOW_PATTERNS, ...routerPatterns([{ module: "react-router", names: ["useParams"] }])] },
      ],
      "aibi/page-objects": ["error", { replaceState: "redirectAddress", addresses: ["/"], prefix: "/#/datasets/" }],
    },
  },
  {
    // The mints of the layout's brands (D428): `Typed` (what the person typed) and `Key` (an
    // index), one type assertion each; `tests/gates/mints.test.ts` refuses a mint anywhere else.
    files: ["src/layout/Field.tsx", "src/layout/key.ts"],
    rules: {
      "@typescript-eslint/consistent-type-assertions": "off",
    },
  },
  {
    // The end-to-end harness: the one importer of the oracle's counter (`src/api/oracle.ts`), and
    // a driver of the page's operator client.
    files: ["src/harness/**"],
    rules: {
      "@typescript-eslint/no-restricted-imports": [
        "error",
        { patterns: [{ regex: "(^|/)api/(?!(index|oracle|operator)(\\.ts)?$)", message: IMPORT }, ...FORM_PATTERNS, ...SHOW_PATTERNS, ...routerPatterns(ROUTER_ALLOWED)] },
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
      "no-restricted-syntax": ["error", ...SINK_SYNTAX, ...STORAGE_SYNTAX, ...IO_SYNTAX, ...JSON_READER_SYNTAX, ...GLOB_SYNTAX, ...AMBIENT_SYNTAX, ...REQUIRE_SYNTAX, ...PATCH_SYNTAX, ...API_IMPORT_SYNTAX, ...ADDRESS_SYNTAX],
      "aibi/page-objects": "error",
      "@typescript-eslint/consistent-type-assertions": ["error", { assertionStyle: "never" }],
      "@typescript-eslint/no-restricted-imports": ["error", internals(null)],
    },
  },
  {
    // The numbers' modules (D430): `numbers.ts` reads a box's text and `format.tsx` shows it, each
    // held to D419's number names as the app is, and to `ROUNDING_NAMES`: these names are a lint
    // error there; another spelling of a conversion or a rounding is held by the tests' corpus.
    files: ["src/api/numbers.ts", "src/api/format.tsx"],
    rules: {
      "no-bitwise": "error",
      "no-restricted-globals": globals([SINK_GLOBALS, SINK], [IO_NAMES, IO], [NUMBER_NAMES, NUMBER], [STORAGE_NAMES, STORE]),
      "no-restricted-syntax": [
        "error",
        ...SINK_SYNTAX,
        ...STORAGE_SYNTAX,
        ...IO_SYNTAX,
        ...JSON_READER_SYNTAX,
        ...GLOB_SYNTAX,
        ...AMBIENT_SYNTAX,
        ...REQUIRE_SYNTAX,
        ...PATCH_SYNTAX,
        ...API_IMPORT_SYNTAX,
        ...ADDRESS_SYNTAX,
        ...NUMBER_SYNTAX,
        ...everywhere(ROUNDING_NAMES, NUMBER),
      ],
    },
  },
  {
    // The decoder, the box and the request ids' grammar: the one type assertion each.
    files: ["src/api/decode.ts", "src/api/box.ts", "src/api/ids.ts"],
    rules: {
      "@typescript-eslint/consistent-type-assertions": "off",
    },
  },
  {
    // The provenance brand's return override (D425): a base class whose constructor returns its
    // argument, so that the subclass's private field lands on the decoded node.
    files: ["src/api/origin.ts"],
    rules: {
      "@typescript-eslint/no-extraneous-class": ["error", { allowConstructorOnly: true }],
    },
  },
  // Each internal's owner imports it (`API_INTERNALS`).
  ...[...new Set(API_INTERNALS.flatMap(({ owners }) => owners))].map((owner) => ({
    files: [owner],
    rules: { "@typescript-eslint/no-restricted-imports": /** @type {["error", object]} */ (["error", internals(owner)]) },
  })),
  {
    // The one place of I/O, which seals the body readers.
    files: ["src/api/client.ts"],
    rules: {
      "no-restricted-globals": globals([SINK_GLOBALS, SINK], [STORAGE_NAMES, STORE]),
      "no-restricted-properties": properties(true),
      "no-restricted-syntax": ["error", ...SINK_SYNTAX, ...STORAGE_SYNTAX, ...JSON_READER_SYNTAX, ...GLOB_SYNTAX, ...AMBIENT_SYNTAX, ...REQUIRE_SYNTAX, ...PATCH_SYNTAX, ...API_IMPORT_SYNTAX, ...ADDRESS_SYNTAX],
      "aibi/page-objects": "error",
    },
  },
  {
    // The display lint (D429, `scripts/lint/display.mjs`; D427's R90–R93): every file of the app.
    // What reaches a render position (R90), the elements and attributes, the address's members
    // (R92) and the bare specifiers, per name.
    files: [CODE],
    ignores: [...TOOLING_PATTERNS],
    rules: {
      "display/raw-text": "error",
      "display/elements": "error",
      "display/address": "error",
      "display/specifiers": ["error", { allowed: SPECIFIERS }],
    },
  },
  {
    // The DOM (R91) and the operator's answers (R93): the app outside the seam (D419: `src/api/`
    // holds the one place of I/O and reads the answers it boxes).
    files: [CODE],
    ignores: [...TOOLING_PATTERNS, "src/api/**"],
    rules: {
      "display/dom": "error",
      "display/answers": "error",
    },
  },
  {
    // The one door from a server text to the DOM (D428): it renders the visible form's own strings.
    files: ["src/api/text.tsx"],
    rules: { "display/raw-text": "off" },
  },
  {
    // The entries alone make a root (`react-dom/client`: a detached root reads the visible form, D428).
    files: ENTRIES,
    rules: { "display/specifiers": ["error", { allowed: ENTRY_SPECIFIERS }] },
  },
  {
    // The router module: `useParams` alone of the router package, and `location.pathname` (D428).
    files: ["src/router/address.tsx"],
    rules: {
      "display/specifiers": ["error", { allowed: ROUTER_SPECIFIERS }],
      "display/address": ["error", { allow: ["Location.pathname"] }],
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
