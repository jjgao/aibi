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
 *
 * **No channel out of the page's memory (D423).** Two layers. By name: the storages and the Cache
 * API (`STORAGE_NAMES`) in every position, the channels (`CHANNEL_SYNTAX`: `navigator.storage`,
 * `navigator.clipboard`, `history.pushState` and `replaceState`, a write to `location` or a member
 * of it, `location.replace`, `assign`, `hash` and `href`, a write to `document.title`,
 * `window.open` and the window's `name`), and the page's objects (`PAGE_OBJECTS`) by direct member
 * access alone (`ALIAS_SYNTAX`). By type (`aibi/page-objects`, `pageObjects` below, the checker the
 * lint already loads): whatever a value is called, one of the types of `PAGE_TYPES` (or one that
 * holds one) may stand only as the object of a member access that is no channel of its type, no
 * member of one is written, and a member that reaches one (`REACHERS`: `defaultView`,
 * `ownerDocument`, `view`, ...) is refused, so that `const l = window.location`, `el.ownerDocument`,
 * `event.view` and a returned value are seen. `history.replaceState` stands in `src/operator/url.ts`
 * alone, which is nothing but one function and its one call, the path a literal (`URL_SYNTAX`,
 * `URL_FILE_SYNTAX`, the rule's option); `src/operator/` has no module-level `let` or `var`
 * (`OPERATOR_SYNTAX`). What it does not see is D419's residual: a value the checker types `any`, a
 * `declare` or an assertion that lies, another realm, a patched builtin, and the channels that are no
 * page object's member (an anchor's `href` and `click()`, a form's `submit()`, `<meta refresh>`,
 * `img.src`, ...: D423 lists them). `tests/gates/operator.test.ts` holds each channel in each
 * position and a generated table of every type, reacher, position and channel.
 */
import js from "@eslint/js";
import { defineConfig } from "eslint/config";
import tseslint from "typescript-eslint";
import ts from "typescript";

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
  // The clipboard outlives the page and is read by whatever the person pastes into.
  { selector: `${onObject(["navigator"])}[property.name='clipboard']`, message: CHANNEL },
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

// ---------------------------------------------------------------------------------------------
// The page's objects by type (D423). The rules above match names; this one matches what a value IS,
// with the type checker the lint already loads (`projectService`, `strictTypeChecked`), so that an
// alias reached through a member (`window.navigator`, `document.defaultView`, `el.ownerDocument`,
// `event.view`, a return value, a parameter) is seen as the page's object it is.

/** What a type of the page's is refused to give: `*` for every member, else those named. */
const EVERY = "*";

/** The page's object types of the DOM library (`lib.dom.d.ts`; `globalThis` for the global object's
 * own type) and the members of each that are channels out of the page's memory (D423). A value of
 * one of these types, or of a type that contains one (a union, an intersection, a generic's
 * constraint, a subtype), may stand only as the object of a member access that is no channel. */
const PAGE_TYPES = new Map(/** @type {[string, string | string[]][]} */ ([
  ["Window", ["name", "open", "caches", "localStorage", "sessionStorage", "indexedDB", "postMessage"]],
  ["globalThis", ["name", "open", "caches", "localStorage", "sessionStorage", "indexedDB", "postMessage"]],
  ["Location", ["hash", "href", "replace", "assign"]],
  ["Navigator", ["storage", "clipboard"]],
  ["Document", ["cookie"]],
  ["ShadowRoot", []],
  ["History", ["pushState", "replaceState"]],
  ["Storage", EVERY],
  ["StorageManager", EVERY],
  ["Clipboard", EVERY],
  ["CacheStorage", EVERY],
  ["Cache", EVERY],
]));

/** The members that give a page object from another value (`el.ownerDocument`, `event.view`,
 * `window.window`): refused wherever they give one, whatever the value they are read from. */
const REACHERS = new Set(["defaultView", "ownerDocument", "view", "window", "self", "top", "parent", "frames", "opener"]);

const PAGE_OBJECT = "A page object (a window, an address, a navigator, a document, a history, a storage) is used by member access alone: not bound, destructured, spread, passed, returned or tested (D423).";
const PAGE_CHANNEL = "A channel out of the page's memory, on a value of the page's own type, whatever it is called (D423).";
const PAGE_WRITE = "No write to a member of a page object (D423).";
const PAGE_REACHER = "A member that reaches a page object (defaultView, ownerDocument, view, window, self, top, parent, frames, opener) is refused (D423).";
const PAGE_COMPUTED = "No computed member of a page object (D423).";
const PAGE_ONCE = "history.replaceState stands once, in the one function that is allowed it (D423).";

/** The names of the nodes that are no value: a key or a property name, a specifier, a label.
 * @param {unknown} value @returns {value is Record<string, unknown>} */
const isRecord = (value) => typeof value === "object" && value !== null;

/** @param {unknown} node @returns {string} */
const kindOf = (node) => (isRecord(node) && typeof node["type"] === "string" ? node["type"] : "");

/** A node's field.
 * @param {unknown} node @param {string} key @returns {unknown} */
const fieldOf = (node, key) => (isRecord(node) ? node[key] : undefined);

/** The nodes whose key or name is a name the syntax gives, not a value: a key, a declared type's name. */
const NAMING_NODES = [
  "Property",
  "PropertyDefinition",
  "MethodDefinition",
  "TSPropertySignature",
  "TSMethodSignature",
  "AccessorProperty",
  "TSEnumMember",
  "TSTypeAliasDeclaration",
  "TSInterfaceDeclaration",
  "TSEnumDeclaration",
  "TSModuleDeclaration",
  "TSTypeParameter",
];

/** Whether an Identifier stands as a value or a binding (it has a type to read), not as a name a
 * syntax gives: a property of a member, a key, a specifier, a label.
 * @param {unknown} node */
function namesAValue(node) {
  const parent = fieldOf(node, "parent");
  const kind = kindOf(parent);
  const computed = fieldOf(parent, "computed") === true;
  if (kind === "MemberExpression" && fieldOf(parent, "property") === node && !computed) {
    return false;
  }
  if (kind === "Property" && fieldOf(parent, "shorthand") === true) {
    return true;
  }
  const named = fieldOf(parent, "key") === node || fieldOf(parent, "id") === node || fieldOf(parent, "name") === node;
  if (named && !computed && NAMING_NODES.includes(kind)) {
    return false;
  }
  return !["ImportSpecifier", "ImportDefaultSpecifier", "ImportNamespaceSpecifier", "ExportSpecifier", "LabeledStatement", "BreakStatement", "ContinueStatement", "MetaProperty"].includes(kind);
}

/** The page's type a type is or holds, by name, or `null`: a union's or an intersection's
 * constituents, a generic's constraint, an instantiation's target, a subtype's bases.
 * @param {import("typescript").TypeChecker} checker @param {import("typescript").Program} program
 * @param {import("typescript").Type | undefined} type @param {Set<unknown>} seen @param {number} depth
 * @returns {string | null} */
function pageTypeOf(checker, program, type, seen = new Set(), depth = 0) {
  if (type === undefined || depth > 8 || seen.has(type)) {
    return null;
  }
  seen.add(type);
  if (type.isUnionOrIntersection()) {
    for (const part of type.types) {
      const found = pageTypeOf(checker, program, part, seen, depth + 1);
      if (found !== null) {
        return found;
      }
    }
    return null;
  }
  if ((type.flags & ts.TypeFlags.TypeParameter) !== 0) {
    const constraint = checker.getBaseConstraintOfType(type);
    return constraint === undefined || constraint === type ? null : pageTypeOf(checker, program, constraint, seen, depth + 1);
  }
  for (const symbol of [type.getSymbol(), type.aliasSymbol]) {
    const name = symbol?.getName();
    if (symbol !== undefined && name !== undefined && PAGE_TYPES.has(name)) {
      const own = name === "globalThis" || (symbol.declarations ?? []).some((declaration) => program.isSourceFileDefaultLibrary(declaration.getSourceFile()));
      if (own) {
        return name;
      }
    }
  }
  if (type.isClassOrInterface()) {
    for (const base of checker.getBaseTypes(type)) {
      const found = pageTypeOf(checker, program, base, seen, depth + 1);
      if (found !== null) {
        return found;
      }
    }
  }
  if ((type.flags & ts.TypeFlags.Object) !== 0 && ((/** @type {import("typescript").ObjectType} */ (type)).objectFlags & ts.ObjectFlags.Reference) !== 0) {
    const target = (/** @type {import("typescript").TypeReference} */ (type)).target;
    return target === type ? null : pageTypeOf(checker, program, target, seen, depth + 1);
  }
  return null;
}

/** The rule `aibi/page-objects` (D423). Every value whose type is a page object's may stand only as
 * the object of a member access, and that member is no channel of the type; no member of one is
 * written; a member that reaches a page object is refused; a computed member of one is refused. The
 * option `{ replaceState: "<function>" }` lets exactly one `history.replaceState(null, "", "/curate")`
 * stand, in the exported function of that name, and nowhere else.
 * @type {import("eslint").Rule.RuleModule} */
const pageObjects = {
  meta: {
    type: "problem",
    schema: [{ type: "object", properties: { replaceState: { type: "string" } }, additionalProperties: false }],
    messages: { object: PAGE_OBJECT, channel: PAGE_CHANNEL, write: PAGE_WRITE, reacher: PAGE_REACHER, computed: PAGE_COMPUTED, once: PAGE_ONCE },
  },
  create(context) {
    const given = /** @type {unknown} */ (context.sourceCode.parserServices);
    const services = /** @type {{ program?: import("typescript").Program | null, esTreeNodeToTSNodeMap?: WeakMap<object, import("typescript").Node> }} */ (given);
    const { program, esTreeNodeToTSNodeMap: nodes } = services;
    if (program === null || program === undefined || nodes === undefined) {
      throw new Error("aibi/page-objects needs type information (parserOptions.projectService)");
    }
    const checker = program.getTypeChecker();
    const option = /** @type {unknown} */ (context.options[0]);
    const options = /** @type {{ replaceState?: string } | undefined} */ (option);
    let replaceStates = 0;

    /** @param {unknown} node @returns {string | null} */
    const pageType = (node) => {
      const found = isRecord(node) ? nodes.get(node) : undefined;
      return found === undefined ? null : pageTypeOf(checker, program, checker.getTypeAtLocation(found));
    };

    /** Whether a node stands as the object of a member access, through a non-null assertion or an
     * optional chain's wrapper.
     * @param {import("eslint").Rule.Node} node */
    const isMemberObject = (node) => {
      /** @type {unknown} */
      let at = node;
      let up = fieldOf(at, "parent");
      while (["TSNonNullExpression", "ChainExpression"].includes(kindOf(up))) {
        at = up;
        up = fieldOf(at, "parent");
      }
      return kindOf(up) === "MemberExpression" && fieldOf(up, "object") === at;
    };

    /** @param {import("eslint").Rule.Node} node */
    const isWritten = (node) => {
      const parent = fieldOf(node, "parent");
      const kind = kindOf(parent);
      return (
        (kind === "AssignmentExpression" && fieldOf(parent, "left") === node) ||
        (kind === "UpdateExpression" && fieldOf(parent, "argument") === node) ||
        (kind === "UnaryExpression" && fieldOf(parent, "operator") === "delete")
      );
    };

    /** Whether `history.replaceState(null, "", "/curate")` stands here: a call of exactly that, on the
     * global `history`, in the exported function the option names, once.
     * @param {import("eslint").Rule.Node} member */
    const allowedReplaceState = (member) => {
      const name = options?.replaceState;
      const call = fieldOf(member, "parent");
      const args = fieldOf(call, "arguments");
      const object = fieldOf(member, "object");
      if (name === undefined || kindOf(call) !== "CallExpression" || fieldOf(call, "callee") !== member || !Array.isArray(args) || args.length !== 3) {
        return false;
      }
      const state = fieldOf(args, "0");
      const title = fieldOf(args, "1");
      const address = fieldOf(args, "2");
      if (fieldOf(state, "raw") !== "null" || fieldOf(title, "value") !== "" || fieldOf(address, "value") !== "/curate" || kindOf(address) !== "Literal" || kindOf(title) !== "Literal") {
        return false;
      }
      if (kindOf(object) !== "Identifier" || fieldOf(object, "name") !== "history") {
        return false;
      }
      /** @type {import("eslint").Scope.Scope | null} */
      let scope = context.sourceCode.getScope(member);
      for (; scope !== null; scope = scope.upper) {
        if ((scope.set.get("history")?.defs.length ?? 0) > 0) {
          return false;
        }
      }
      /** @type {unknown} */
      let at = fieldOf(member, "parent");
      while (isRecord(at) && !["FunctionDeclaration", "FunctionExpression", "ArrowFunctionExpression"].includes(kindOf(at))) {
        at = fieldOf(at, "parent");
      }
      const wrapper = fieldOf(at, "parent");
      return kindOf(at) === "FunctionDeclaration" && kindOf(wrapper) === "ExportNamedDeclaration" && fieldOf(fieldOf(at, "id"), "name") === name;
    };

    /** @param {import("eslint").Rule.Node} node */
    const checkValue = (node) => {
      if (pageType(node) !== null && !isMemberObject(node)) {
        context.report({ node, messageId: "object" });
      }
    };

    return {
      /** @param {import("eslint").Rule.Node} node */
      Identifier(node) {
        if (!namesAValue(node)) {
          return;
        }
        const tsNode = nodes.get(node);
        for (let up = tsNode?.parent; up !== undefined; up = up.parent) {
          if (ts.isTypeNode(up)) {
            return;
          }
          if (ts.isStatement(up)) {
            break;
          }
        }
        checkValue(node);
      },
      /** A value tested against a page object's own constructor (`x instanceof Window`).
       * @param {import("eslint").Rule.Node} node */
      BinaryExpression(node) {
        const right = fieldOf(node, "right");
        const found = isRecord(right) ? nodes.get(right) : undefined;
        const prototype = fieldOf(node, "operator") === "instanceof" && found !== undefined ? checker.getTypeAtLocation(found).getProperty("prototype") : undefined;
        if (found !== undefined && prototype !== undefined && pageTypeOf(checker, program, checker.getTypeOfSymbolAtLocation(prototype, found)) !== null) {
          context.report({ node, messageId: "object" });
        }
      },
      /** @param {import("eslint").Rule.Node} node */
      CallExpression: checkValue,
      /** @param {import("eslint").Rule.Node} node */
      NewExpression: checkValue,
      /** @param {import("eslint").Rule.Node} node */
      ThisExpression: checkValue,
      /** @param {import("eslint").Rule.Node} node */
      MemberExpression(node) {
        const owner = pageType(fieldOf(node, "object"));
        const property = fieldOf(node, "property");
        const computed = fieldOf(node, "computed") === true;
        const literal = kindOf(property) === "Literal" ? fieldOf(property, "value") : undefined;
        const name = computed ? (typeof literal === "string" ? literal : null) : kindOf(property) === "Identifier" ? fieldOf(property, "name") : null;
        if (owner !== null) {
          /** @type {string | string[]} */
          const refused = PAGE_TYPES.get(owner) ?? [];
          if (name === null) {
            context.report({ node, messageId: "computed" });
          } else if (typeof name === "string" && (refused === EVERY || refused.includes(name))) {
            if (owner === "History" && name === "replaceState" && allowedReplaceState(node)) {
              replaceStates += 1;
              if (replaceStates > 1) {
                context.report({ node, messageId: "once" });
              }
            } else {
              context.report({ node, messageId: "channel" });
            }
          }
          if (isWritten(node)) {
            context.report({ node, messageId: "write" });
          }
        }
        if (typeof name === "string" && REACHERS.has(name) && pageType(node) !== null) {
          context.report({ node, messageId: "reacher" });
        }
        checkValue(node);
      },
    };
  },
};

/** The inline plugin of the lint's own rules. */
const aibi = { rules: { "page-objects": pageObjects } };

/** The syntax rules of a file of `src/` outside `src/api/`, given its storage rules.
 * @param {Restriction[]} storage @returns {Restriction[]} */
const appSyntax = (storage) => [
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
  { plugins: { aibi } },
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
      "@typescript-eslint/no-restricted-imports": ["error", { patterns: [{ regex: "(^|/)api/(?!index(\\.ts)?$)", message: IMPORT }] }],
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
        { patterns: [{ regex: "(^|/)api/(?!(index|operator)(\\.ts)?$)", message: IMPORT }] },
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
      "aibi/page-objects": "error",
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
      "aibi/page-objects": "error",
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
