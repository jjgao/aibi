// @ts-check
/**
 * The display lint (D429, M5.1c-2b-1L): the line that holds D428's rule, "a string from data, the
 * address or an operator's answer reaches the DOM only through the visible form", in every module of
 * the app, by the type checker the lint already loads (`projectService`), whatever the text's source.
 * D427's rows R90–R93 are this module's; `eslint.config.mjs` registers its rules as the inline plugin
 * `display`, and `tests/unit/lint-display.test.ts` (the rows' tests) and
 * `tests/gates/display-lint.test.ts` (the tables) hold each rule through that configuration.
 *
 * - **`display/raw-text` (R90, at JSX positions)**: no value whose type holds a non-literal `string`,
 *   `number` or `bigint` stands at a JSX position or as what a component returns. The positions: a
 *   JSX child or attribute value; the return of a function named as a component (React's
 *   convention, a capital letter: its own name, or its variable's); what a component returns, by its
 *   tag's type, whoever declares it (every union member and call signature, a type parameter by its
 *   constraint: `ComponentType`, `FC` and a route's `Component` return `ReactNode`, so such a tag is
 *   refused; a tag whose type React declares as an exotic component, or that has a `$$typeof` a
 *   package declares (inherited or intersected), is exempt and NOT read, so a raw text its render
 *   function returns is OPEN, below; but a lazy one whose type keeps its `_result` is read by the
 *   component it loads). No JSX tag is of string type, `any` or `unknown`, and no child
 *   is spread. The type is walked (`rawIn`, to a depth of 16, past which it is refused) through
 *   unions, intersections, arrays and tuples, iterables (every iterator and `next` signature),
 *   promises, template literal and string-mapping types, deferred types by their constraint, a
 *   function by what each of its call signatures returns (a component or a render prop given at a
 *   JSX position), and an object type a string or a number is assignable to; `any`, `unknown` and a
 *   type with no constraint are refused, and so is `ReactNode` (it holds `string`). Allowed: literals
 *   and values of a brand of `BRANDS` (matched by the declaration of its symbol). An object's
 *   properties are not walked: a component given one is read where it shows them, at its own JSX
 *   positions. The one exception is a JSX child that is exactly a component's own `children`, the
 *   key `children` of its first parameter's object pattern (not the member `props.children`: `props`
 *   can be written through an alias or by a callee), never written in the component (ESLint's scope
 *   analysis: no default, assignment, update, loop or destructuring target): it holds because every
 *   caller's JSX position is read (an attribute `children`, a JSX child), and only that far.
 *   **OPEN (D429, 2b-1L2)**: a raw text that reaches a render slot other than at a JSX position: a
 *   route's `element`, `errorElement` or `children` (built apart or nested), `root.render(x)`, a
 *   component called as a function or passed as a value (`rows.map(Row)`, an alias, `call`,
 *   `apply`, `bind`, which also reaches its own `children` past the exemption), a state setter
 *   handed to `then`, a library's container, an iterable spread, an index signature, and a tag
 *   typed as one of React's exotic components (`const Body: ExoticComponent = lazy(...)`,
 *   `NamedExoticComponent`, `ProviderExoticComponent`, `ForwardRefExoticComponent`, `Provider`,
 *   `MemoExoticComponent`, an interface that extends or intersects one, a `ComponentClass`): the
 *   rule does not read its render function. Each is a probe asserted open in
 *   `tests/gates/display-lint.ts` (`RAW_OPEN`).
 * - **`display/elements`**: the elements and attributes the app may write, allow-lists measured on
 *   `src/` (`ELEMENTS`, `ATTRIBUTES`, and the prefixes `aria-` and `data-`); an `<option>` in
 *   `src/layout/Option.tsx` alone (D428); no spread; `ref` in the reviewed modules alone
 *   (`REF_MODULES`); `className`, `role`, `href`, `aria-*` and `data-*` of a literal type, `href`
 *   also exactly `Link`'s template (`HREF_TEMPLATE`: `#/datasets/` and one `DatasetId`).
 * - **`display/dom` (R91)**: no write to a member of a value of a type the DOM library declares, or
 *   to a member it declares (through `Pick`, `Partial` or a mapped type), and no reference to a
 *   function, method or constructor it declares (a call or not: `.call`, `.apply`, `.bind`, an
 *   argument, a shorthand) but those of `CALLS`, measured on `src/`, each with its reason. The DOM
 *   library is `lib.dom.d.ts` and its parts (`lib.dom.iterable.d.ts`, ...). The reviewed sites are
 *   `SITES`, each matched by its file and its text, exactly once.
 * - **`display/address` (R92)**: the members the DOM library declares that give the address
 *   (`ADDRESS`, by their declaring interface, a URL's query and credentials included) are read
 *   nowhere but where the option allows (`Location.pathname` in the router module); a reviewed list,
 *   defence in depth (R90 and R91 are the line), beside the names `eslint.config.mjs` refuses
 *   (`ADDRESS_SYNTAX`).
 * - **`display/answers` (R93)**: no member, destructured key or computed literal named `body` or
 *   `codes` (an operator answer's raw parts, D426) outside `src/api/`, but `SITES`.
 * - **`display/specifiers`**: every bare specifier is refused but those of the option, per name
 *   (`SPECIFIERS`, `ENTRY_SPECIFIERS`, `ROUTER_SPECIFIERS`, measured on `src/`); a relative
 *   specifier into `node_modules` is refused.
 *
 * **What it does not see** (D429's residuals; the threat is an accident or a lazy hand, D419, not
 * deliberate obfuscation), each with its probe in `tests/gates/display-lint.ts`: a literal type or a
 * brand minted by unsoundness (array covariance, a method's bivariance); a declaration that lies
 * about what a component returns (`declare const C: () => ReactElement`); a DOM value flowed into a
 * type that does not say so (a parameter or a variable typed structurally, a generic setter). Every
 * block of this module is EXECUTED by the corpora (`tests/gates/display-coverage.test.ts`), but the
 * two its `UNREACHED` names; that gate holds execution, not assertion (the rows assert, and the
 * mutation pass measures them: D429 lists its survivors, seven equivalent and one stricter than the rule, each with its reason).
 */
import path from "node:path";

import ts from "typescript";

// ---------------------------------------------------------------------------------------------
// The tables, each reviewed with its reason.

/** The brands a value may carry to a render position (R90): each matched by the declaration of its
 * `unique symbol`, in its file, so that a brand of the same name declared elsewhere is no brand. */
export const BRANDS = [
  {
    type: "DatasetId",
    brand: "datasetBrand",
    file: "src/api/text.tsx",
    reason: "A dataset's id, of the generated grammar (`idOf`, `idOfAddress`, D428): its characters are bounded, so it is shown raw by design.",
  },
  {
    type: "Typed",
    brand: "typedBrand",
    file: "src/layout/Field.tsx",
    reason: "What the person typed into a field (`Field`'s mint, D428): an `<input>`'s value, their own text.",
  },
  { type: "Key", brand: "keyBrand", file: "src/layout/key.ts", reason: "An index (`keyOf`, D428): an element's `key` and an `<option>`'s value, a whole number the page made." },
];

/** The intrinsic elements the app may write (`display/elements`), measured on `src/` (each is written
 * there today; a pull request that needs another adds it here, with its reason): none loads, embeds,
 * navigates or runs (`script`, `iframe`, `object`, `embed`, `img`, `link`, `meta`, `base`, `style`,
 * `title`), each a channel or a sink of its own (D411, D423). */
export const ELEMENTS = ["a", "bdi", "button", "div", "form", "h1", "h2", "h3", "input", "label", "main", "option", "output", "p", "section", "select", "span"];

/** The one module that writes an `<option>` (D428: a screen's own `<option>` would flatten a `<Text>`
 * and lose the escape's mark). */
export const OPTION_MODULE = "src/layout/Option.tsx";

/** The attributes an intrinsic element may have, beside `aria-*` and `data-*` (`display/elements`), measured
 * on `src/`, and `hidden`, which `tests/gates/corpus.test.ts`'s control writes (a boolean: D418's
 * `strict-boolean-expressions`, `hidden={n}`). */
export const ATTRIBUTES = [
  "autoComplete",
  "className",
  "disabled",
  "hidden",
  "href",
  "key",
  "name",
  "onChange",
  "onClick",
  "onSubmit",
  "ref",
  "rel",
  "role",
  "spellCheck",
  "title",
  "type",
  "value",
];

/** The attributes whose value must be of a literal type, beside `aria-*` and `data-*`: a brand is no
 * literal, but `href`'s template of a `DatasetId` (`Link`'s target; a bare id is no address). */
export const LITERAL_ATTRIBUTES = ["className", "href", "role"];

/** The modules that may write `ref` (D423's uncontrolled token field; the end-to-end harness's fields). */
export const REF_MODULES = ["src/operator/Shell.tsx", "src/harness/Harness.tsx"];

/** The end-to-end harness: in the end-to-end build alone (`plugins/gate.ts` (a) fails any other build
 * that holds a module of it), its `data-*` attributes are read by Playwright, never shown, so they
 * are exempt from R90 and from the literal rule. */
export const HARNESS = "src/harness/";

/** The members of the DOM library a module may reference as a function, method or constructor
 * (`display/dom`), by their declaring interface (`Interface.member`) or, for a global, their name:
 * measured on `src/` (each is used there today). */
export const CALLS = new Map([
  ["AbortController", "Makes the controller of a request's cancellation (the shell's Cancel): no text."],
  ["AbortController.abort", "Cancels a pending request (the shell's Cancel): no text."],
  ["Document.getElementById", "Reads the document: finds the root both entries render into (`src/root.ts`)."],
  ["History.replaceState", "The address's two writers (`src/operator/url.ts`, `src/router/address.tsx`), which the channel rules confine to a fixed or a `DatasetId` address (D423, D428)."],
]);

/** The members of the DOM library that give the page's address (`display/address`), by their
 * declaring interface: what `location`, the document, a node, a link, a form's action (the document's
 * URL when none is written), a `hashchange` event, the navigation's timing entry, a request a route's
 * middleware is given and a `URL` a router hands over hold of it (D428's open readers among them). A
 * reviewed list, defence in depth: R90 and R91 are the line. */
export const ADDRESS = [
  "Document.URL",
  "Document.documentURI",
  "Document.location",
  "Document.referrer",
  "HTMLBaseElement.href",
  "HTMLHyperlinkElementUtils.hash",
  "HTMLHyperlinkElementUtils.href",
  "HTMLHyperlinkElementUtils.password",
  "HTMLHyperlinkElementUtils.pathname",
  "HTMLHyperlinkElementUtils.search",
  "HTMLHyperlinkElementUtils.toString",
  "HTMLHyperlinkElementUtils.username",
  "HTMLButtonElement.formAction",
  "HTMLFormElement.action",
  "HTMLInputElement.formAction",
  "HashChangeEvent.newURL",
  "HashChangeEvent.oldURL",
  "Location.hash",
  "Location.href",
  "Location.pathname",
  "Location.search",
  "Location.toString",
  "Node.baseURI",
  "PerformanceEntry.name",
  "Request.url",
  "URL.hash",
  "URL.href",
  "URL.password",
  "URL.pathname",
  "URL.search",
  "URL.searchParams",
  "URL.toJSON",
  "URL.toString",
  "URL.username",
  "Window.location",
];

/** An operator answer's raw parts (D426: raw until the curation screens' plan reads them through the seam). */
export const ANSWER_NAMES = ["body", "codes"];

/** The reviewed sites: a report of `rule` in `file` whose node's text is `code` stands, once. */
export const SITES = [
  {
    rule: "dom",
    file: "src/catalogue/main.tsx",
    code: "rootElement().textContent",
    reason: "The entry's one write, before React renders anything: on an engine that cannot read a server number exactly, the root shows `REFUSALS.unsupported`, fixed words (D419).",
  },
  {
    rule: "dom",
    file: "src/operator/main.tsx",
    code: "rootElement().textContent",
    reason: "The entry's one write, before React renders anything: on an engine that cannot read a server number exactly, the root shows `REFUSALS.unsupported`, fixed words (D419).",
  },
  {
    rule: "dom",
    file: "src/operator/Shell.tsx",
    code: "field.value",
    reason: "D423: the uncontrolled token field is emptied (`\"\"`) as the form is sent, so that the token stands in the DOM no longer than the call that hands it to the client (a controlled field would write it into the DOM).",
  },
  {
    rule: "dom",
    file: "src/catalogue/load.ts",
    code: "setTimeout",
    reason:
      "The queue's deadline (D431): `dom` reports a call's callee, so the site matches the identifier, once in this file; `tests/unit/load.test.ts`' source cell holds the call's text, `setTimeout(expire, DEADLINE_MS)`, a closure with no argument and no text.",
  },
  {
    rule: "dom",
    file: "src/catalogue/load.ts",
    code: "clearTimeout",
    reason:
      "The queue's deadline stopped (D431) by its one `stop()`, when the call settles, the deadline fires or the store is dropped: the site matches the identifier, once in this file; `tests/unit/load.test.ts`' source cell holds the call's text, `clearTimeout(timer)`.",
  },
  {
    rule: "answers",
    file: "src/harness/Harness.tsx",
    code: "found.body",
    reason: "The end-to-end harness passes an answer's decoded node, whole, to the next operation (D425's request half); it shows nothing of it.",
  },
  {
    rule: "answers",
    file: "src/harness/Harness.tsx",
    code: "outcome.codes",
    reason: "The end-to-end harness writes a refusal's codes into `data-codes` for Playwright, in the end-to-end build alone (`plugins/gate.ts` (a)).",
  },
];

/** @typedef {{ values: string[], types: string[] | "*" }} Allowed */

/** React's names (its components and hooks that render no text of their own; its types). @type {Allowed} */
const REACT = { values: ["Fragment", "StrictMode", "Suspense", "createContext", "lazy", "useContext", "useRef", "useState", "useSyncExternalStore"], types: "*" };
/** @typedef {Record<string, Allowed>} Specifiers */

/** The router package's names (D428's allow-list, `eslint.config.mjs`'s `ROUTER_ALLOWED`). */
const ROUTER = { "react-router": { values: ["createHashRouter"], types: ["RouteObject"] }, "react-router/dom": { values: ["RouterProvider"], types: [] } };

/** The bare specifiers of every module of the app, per name (`display/specifiers`): React's
 * components and hooks that render no text of their own (its types alone, `*`: a type has no value
 * at run time), and the router's allow-list. Anything else is refused: `createElement`,
 * `cloneElement`, `Children`, `react/jsx-runtime` and `react/jsx-dev-runtime` (an element made with
 * no JSX the rule reads), `react-dom` (`createPortal`, `flushSync`), `react-dom/server` and
 * `react-dom/static` (a detached render of the visible form), `react-dom/profiling` (a second
 * `createRoot`), and every package the app does not name. @type {Specifiers} */
export const SPECIFIERS = { react: REACT, ...ROUTER };

/** The entries: `react-dom/client`'s `createRoot` (a root the entries alone make: a detached root
 * reads the visible form's text, D428). */
export const ENTRIES = ["src/catalogue/main.tsx", "src/operator/main.tsx"];

/** @type {Specifiers} */
export const ENTRY_SPECIFIERS = { ...SPECIFIERS, "react-dom/client": { values: ["createRoot"], types: [] } };

/** The router module's own: `useParams` alone of the router package (D428). @type {Specifiers} */
export const ROUTER_SPECIFIERS = { react: REACT, "react-router": { values: ["useParams"], types: [] } };

// ---------------------------------------------------------------------------------------------
// Reading ESTree nodes without trusting their shape.

/** @param {unknown} value @returns {value is Record<string, unknown>} */
const isRecord = (value) => typeof value === "object" && value !== null;

/** @param {unknown} node @returns {string} */
const kindOf = (node) => String(fieldOf(node, "type"));

/** @param {unknown} node @param {string} key @returns {unknown} */
const fieldOf = (node, key) => (isRecord(node) ? node[key] : undefined);

/** An empty list, for a list field a well-formed tree always has. @type {readonly never[]} */
const NONE = Object.freeze([]);

/** An ESTree node's list field (an array in every well-formed tree). @param {unknown} node @param {string} key @returns {readonly unknown[]} */
const listOf = (node, key) => {
  const value = fieldOf(node, key);
  return Array.isArray(value) ? value : NONE;
};

/** @param {unknown} node @returns {string | null} */
const nameOf = (node) => {
  const name = fieldOf(node, "name");
  return typeof name === "string" ? name : null;
};

/** A key's name: an identifier's, or a literal's value as a string. @param {unknown} key */
const keyName = (key) => String(nameOf(key) ?? fieldOf(key, "value"));

/** A string an expression writes out: a string literal, or a template with no substitution.
 * @param {unknown} node @returns {string | null} */
const writtenString = (node) => {
  const value = kindOf(node) === "Literal" ? fieldOf(node, "value") : kindOf(node) === "TemplateLiteral" && listOf(node, "expressions").length === 0 ? fieldOf(fieldOf(listOf(node, "quasis")[0], "value"), "cooked") : null;
  return typeof value === "string" ? value : null;
};

/** As a rule's report node. @param {unknown} node @returns {import("eslint").Rule.Node} */
const asNode = (node) => /** @type {import("eslint").Rule.Node} */ (node);

/** A path relative to the package, with forward slashes. @param {string} cwd @param {string} file */
export const relativeOf = (cwd, file) => path.relative(cwd, file).split(path.sep).join("/");

/** @typedef {{ program: ts.Program, checker: ts.TypeChecker, tsNode: (node: unknown) => ts.Node, typeOf: (node: unknown) => ts.Type }} Services */

/** The type checker of a lint, or a loud failure: these rules read types.
 * @param {import("eslint").Rule.RuleContext} context @param {string} rule @returns {Services} */
function servicesOf(context, rule) {
  const given = /** @type {unknown} */ (context.sourceCode.parserServices);
  const services = /** @type {{ program?: ts.Program | null, esTreeNodeToTSNodeMap?: WeakMap<object, ts.Node> }} */ (given);
  const { program, esTreeNodeToTSNodeMap: nodes } = services;
  if (program === null || program === undefined || nodes === undefined) {
    throw new Error(`display/${rule} needs type information (parserOptions.projectService)`);
  }
  const checker = program.getTypeChecker();
  // Every node a rule asks of is an expression, a pattern or a name of the tree, which the parser maps.
  const tsNode = (/** @type {unknown} */ node) => /** @type {ts.Node} */ (nodes.get(/** @type {object} */ (node)));
  return { program, checker, tsNode, typeOf: (node) => checker.getTypeAtLocation(tsNode(node)) };
}

/** Whether a source file is one of the DOM library's (`lib.dom.d.ts`, `lib.dom.iterable.d.ts`, ...).
 * @param {ts.Program} program @param {ts.SourceFile} file */
export const isDomLibrary = (program, file) => program.isSourceFileDefaultLibrary(file) && /(^|[\\/])lib\.dom(\.[a-z]+)?\.d\.ts$/u.test(file.fileName);

/** Whether a declaration is in a package (`node_modules`). @param {ts.Node} declaration */
const inPackage = (declaration) => /[\\/]node_modules[\\/]/u.test(declaration.getSourceFile().fileName);

/** A symbol's declarations (none for a symbol the checker makes up). @param {ts.Symbol | undefined} symbol @returns {readonly ts.Declaration[]} */
const declarationsOf = (symbol) => symbol?.declarations ?? NONE;

/** The key of a member or a global the DOM library declares: `Interface.member` for a member of an
 * interface, `Global.member` for one of a global's type literal (`URL.canParse`), the name for a
 * global. @param {ts.Declaration} declaration @param {string} name @returns {string} */
export function domKey(declaration, name) {
  const owner = declaration.parent;
  const holder = ts.isInterfaceDeclaration(owner) ? owner.name.text : ts.isTypeLiteralNode(owner) && ts.isVariableDeclaration(owner.parent) ? owner.parent.name.getText() : null;
  return holder === null ? name : `${holder}.${name}`;
}

/** The keys of a symbol's declarations in the DOM library (none: it is no member of it).
 * @param {ts.Program} program @param {ts.Symbol | undefined} symbol @returns {string[]} */
function domKeys(program, symbol) {
  return declarationsOf(symbol)
    .filter((declaration) => isDomLibrary(program, declaration.getSourceFile()))
    .map((declaration) => domKey(declaration, String(symbol?.getName())));
}

/** The first result of `find` over `items` that is not `null`. @template T @param {readonly T[]} items @param {(item: T) => string | null} find @returns {string | null} */
function firstOf(items, find) {
  for (const item of items) {
    const found = find(item);
    if (found !== null) {
      return found;
    }
  }
  return null;
}

/** A union's members, or the type alone. @param {ts.Type} type @returns {readonly ts.Type[]} */
const membersOf = (type) => (type.isUnion() ? type.types : [type]);

// ---------------------------------------------------------------------------------------------
// R90: what a type holds.

/** The brands of `BRANDS` an object type carries, by the declaration of its `unique symbol`.
 * @param {ts.TypeChecker} checker @param {ts.Type} type @returns {string[]} */
export function brandsOf(checker, type) {
  return type.getProperties().flatMap((property) =>
    declarationsOf(property).flatMap((declaration) => {
      const name = /** @type {{ name?: ts.Node }} */ (declaration).name;
      const symbol = name !== undefined && ts.isComputedPropertyName(name) ? checker.getSymbolAtLocation(name.expression) : undefined;
      return declarationsOf(symbol).flatMap((held) => {
        const file = held.getSourceFile().fileName.replaceAll("\\", "/");
        return BRANDS.filter(({ brand, file: own }) => ts.isVariableDeclaration(held) && held.name.getText() === brand && file.endsWith(`/${own}`) && !inPackage(held)).map(({ type: branded }) => branded);
      });
    }),
  );
}

/** The types an iterable yields (its iterator's `next` results that are not `done: true`), or
 * `undefined` if the type has no `[Symbol.iterator]`; a part that cannot be read is `any`.
 * @param {ts.TypeChecker} checker @param {ts.Type} type @returns {ts.Type[] | undefined} */
export function yieldedBy(checker, type) {
  const iterator = type.getProperties().find((property) => String(property.escapedName).startsWith("__@iterator@"));
  if (iterator === undefined) {
    return undefined;
  }
  const signatures = checker.getTypeOfSymbol(iterator).getCallSignatures();
  if (signatures.length === 0) {
    return [checker.getAnyType()];
  }
  return signatures.flatMap((signature) => {
    const next = signature.getReturnType().getProperty("next");
    const steps = next === undefined ? [] : checker.getTypeOfSymbol(next).getCallSignatures();
    if (steps.length === 0) {
      return [checker.getAnyType()];
    }
    return steps.flatMap((step) =>
      membersOf(step.getReturnType()).flatMap((member) => {
        const done = member.getProperty("done");
        const value = member.getProperty("value");
        if (done !== undefined && checker.typeToString(checker.getTypeOfSymbol(done)) === "true") {
          return [];
        }
        return [value === undefined ? checker.getAnyType() : checker.getTypeOfSymbol(value)];
      }),
    );
  });
}

const LITERAL_FLAGS =
  ts.TypeFlags.StringLiteral |
  ts.TypeFlags.NumberLiteral |
  ts.TypeFlags.BigIntLiteral |
  ts.TypeFlags.BooleanLiteral |
  ts.TypeFlags.EnumLiteral |
  ts.TypeFlags.Null |
  ts.TypeFlags.Undefined |
  ts.TypeFlags.Void |
  ts.TypeFlags.Never;

const DEFERRED_FLAGS = ts.TypeFlags.TypeParameter | ts.TypeFlags.IndexedAccess | ts.TypeFlags.Conditional | ts.TypeFlags.Substitution | ts.TypeFlags.Index;

/** A deferred type's constraint, if it has one of its own. @param {ts.TypeChecker} checker @param {ts.Type} type @returns {ts.Type | undefined} */
const constraintOf = (checker, type) => {
  const constraint = checker.getBaseConstraintOfType(type);
  return constraint === type ? undefined : constraint;
};

/** What a type holds that may not reach a render position raw (R90), in words, or `null`: a
 * non-literal `string`, `number` or `bigint` anywhere in it, `any`, `unknown`, or a type it cannot
 * read. A value of one of `brands` is allowed.
 * @param {ts.TypeChecker} checker @param {ts.Type} type @param {readonly string[]} brands
 * @param {Set<ts.Type>} [seen] @param {number} [depth] @returns {string | null} */
export function rawIn(checker, type, brands, seen = new Set(), depth = 0) {
  if (depth > 16) {
    return "a type too deep to read";
  }
  if (seen.has(type)) {
    return null;
  }
  seen.add(type);
  const flags = type.flags;
  const inner = (/** @type {ts.Type} */ part) => rawIn(checker, part, brands, seen, depth + 1);
  if ((flags & ts.TypeFlags.Any) !== 0) {
    return "any";
  }
  if ((flags & ts.TypeFlags.Unknown) !== 0) {
    return "unknown";
  }
  if (type.isUnion()) {
    return firstOf(type.types, inner);
  }
  if (type.isIntersection()) {
    return type.types.some((member) => brandsOf(checker, member).some((brand) => brands.includes(brand))) ? null : firstOf(type.types, inner);
  }
  if ((flags & LITERAL_FLAGS) !== 0) {
    return null;
  }
  if ((flags & ts.TypeFlags.TemplateLiteral) !== 0) {
    const found = firstOf(/** @type {ts.TemplateLiteralType} */ (type).types, inner);
    return found === null ? null : `a template of ${found}`;
  }
  if ((flags & ts.TypeFlags.StringMapping) !== 0) {
    const found = inner(/** @type {ts.StringMappingType} */ (type).type);
    return found === null ? null : `a mapping of ${found}`;
  }
  if ((flags & ts.TypeFlags.String) !== 0) {
    return "string";
  }
  if ((flags & ts.TypeFlags.Number) !== 0) {
    return "number";
  }
  if ((flags & ts.TypeFlags.BigInt) !== 0) {
    return "bigint";
  }
  if ((flags & DEFERRED_FLAGS) !== 0) {
    const constraint = constraintOf(checker, type);
    return constraint === undefined ? "a type with no constraint" : inner(constraint);
  }
  if ((flags & ts.TypeFlags.Object) === 0) {
    return null;
  }
  if (checker.isArrayType(type) || checker.isTupleType(type)) {
    const found = firstOf(checker.getTypeArguments(/** @type {ts.TypeReference} */ (type)), inner);
    return found === null ? null : `an array of ${found}`;
  }
  const awaited = checker.getAwaitedType(type);
  const promised = awaited === undefined || awaited === type ? null : inner(awaited);
  if (promised !== null) {
    return `a promise of ${promised}`;
  }
  const yielded = firstOf(yieldedBy(checker, type) ?? [], inner);
  if (yielded !== null) {
    return `an iterable of ${yielded}`;
  }
  // A function at a JSX position may be rendered (a component prop, a render prop, a context's
  // consumer's child): it is read by what each of its call signatures returns (a handler's `void` passes).
  const called = firstOf(
    type.getCallSignatures().map((signature) => signature.getReturnType()),
    inner,
  );
  if (called !== null) {
    return `a function that returns ${called}`;
  }
  return checker.isTypeAssignableTo(checker.getStringType(), type) || checker.isTypeAssignableTo(checker.getNumberType(), type) ? "an object type a string or a number is assignable to" : null;
}

/** The one template a literal attribute takes: `href`'s `#/datasets/${DatasetId}`, `Link`'s (its
 * literal text exactly the prefix, its one hole a `DatasetId`), never a bare id or another shape. */
export const HREF_TEMPLATE = { attribute: "href", prefix: "#/datasets/", brand: "DatasetId" };

/** What makes a type no literal (the literal attributes, `display/elements`), in words, or `null`:
 * a literal, a union of them, or (for `template`'s attribute alone) exactly that template.
 * @param {ts.TypeChecker} checker @param {ts.Type} type @param {typeof HREF_TEMPLATE | null} template @returns {string | null} */
export function notLiteral(checker, type, template) {
  if (type.isUnion()) {
    return firstOf(type.types, (member) => notLiteral(checker, member, template));
  }
  if ((type.flags & LITERAL_FLAGS) !== 0) {
    return null;
  }
  if (template !== null && (type.flags & ts.TypeFlags.TemplateLiteral) !== 0) {
    const { texts, types } = /** @type {ts.TemplateLiteralType} */ (type);
    const [hole] = types;
    if (texts.length === 2 && texts[0] === template.prefix && texts[1] === "" && hole !== undefined && hole.isIntersection() && hole.types.some((member) => brandsOf(checker, member).includes(template.brand))) {
      return null;
    }
  }
  return checker.typeToString(type);
}

const RAW_TEXT =
  "No text reaches the DOM raw (D429, R90): {{what}} {{where}} ({{type}}). Show a server text through the layout (Text, Ref, Segments, Option), words as literals or an `as const` table, a number through its door; a `DatasetId`, a `Typed` and a `Key` are the only brands allowed.";

/** The ESTree kinds of a function. */
const FUNCTIONS = ["FunctionDeclaration", "FunctionExpression", "ArrowFunctionExpression"];

/** The name a function is given as a component: its own, or its variable's. @param {unknown} fn @returns {string | null} */
function functionName(fn) {
  const parent = fieldOf(fn, "parent");
  return kindOf(fn) === "FunctionDeclaration" ? nameOf(fieldOf(fn, "id")) : kindOf(parent) === "VariableDeclarator" ? nameOf(fieldOf(parent, "id")) : null;
}

/** Whether a function is named as a component (React's convention: a capital letter). @param {unknown} fn */
const componentNamed = (fn) => /^[A-Z]/u.test(String(functionName(fn)));

/** The function a node stands in (its nearest). @param {unknown} node @returns {unknown} */
function functionOf(node) {
  let at = fieldOf(node, "parent");
  while (isRecord(at) && !FUNCTIONS.includes(kindOf(at))) {
    at = fieldOf(at, "parent");
  }
  return at;
}

/** `display/raw-text` (R90). @type {import("eslint").Rule.RuleModule} */
const rawText = {
  meta: {
    type: "problem",
    schema: [],
    messages: {
      raw: RAW_TEXT,
      tag: "No JSX tag of string type (D429, R90): {{type}}. An element is written by its literal name, a component by its own.",
      spread: "No spread child (D429, R90): each child is a position the rule reads.",
    },
  },
  create(context) {
    const { checker, typeOf } = servicesOf(context, "raw-text");
    const harness = relativeOf(context.cwd, context.filename).startsWith(HARNESS);
    const brands = BRANDS.map(({ type }) => type);

    /** @param {unknown} node @param {string} what @param {string} where @param {ts.Type} type */
    const report = (node, what, where, type) => {
      context.report({ node: asNode(node), messageId: "raw", data: { what, where, type: checker.typeToString(type) } });
    };

    /** Report a raw text a type holds (`rawIn`); whether it did. @param {unknown} node @param {ts.Type} type @param {string} where */
    const check = (node, type, where) => {
      const found = rawIn(checker, type, brands);
      if (found !== null) {
        report(node, found, where, type);
      }
      return found !== null;
    };

    /** The variable a name refers to, through the scopes around it (a global the program does not
     * declare has none). @param {unknown} node @returns {import("eslint").Scope.Variable | undefined} */
    const variableOf = (node) => {
      const name = String(nameOf(node));
      for (let scope = /** @type {import("eslint").Scope.Scope | null} */ (context.sourceCode.getScope(asNode(node))); scope !== null; scope = scope.upper) {
        const found = scope.set.get(name);
        if (found !== undefined) {
          return found;
        }
      }
      return undefined;
    };

    /** A component's own parameter: a name of the function's parameters (`defs` a `Parameter`), of a
     * function named as a component, never written (no default, assignment, update, loop or
     * destructuring target: every write ESLint's scope analysis knows).
     * @param {unknown} node @returns {{ fn: unknown, name: unknown } | null} */
    const unwrittenParameter = (node) => {
      const variable = variableOf(node);
      const definition = variable?.defs.length === 1 ? variable.defs[0] : undefined;
      if (variable === undefined || definition?.type !== "Parameter" || variable.references.some((reference) => reference.isWrite()) || !componentNamed(definition.node)) {
        return null;
      }
      return { fn: definition.node, name: definition.name };
    };

    /** Whether a JSX child is exactly a component's own `children`: the binding of the key
     * `children` of its first parameter's object pattern, with no default, never written (the
     * member form, `props.children`, is not exempt: `props` can be written through an alias or by a
     * callee, which scope analysis does not see; the app destructures). Such a child was read where a
     * caller's JSX gave it (a JSX child, the attribute `children`); a caller other than JSX (a call, a
     * component passed as a value) is OPEN (D429, 2b-1L2).
     * @param {unknown} node */
    const ownChildren = (node) => {
      const found = kindOf(node) === "Identifier" ? unwrittenParameter(node) : null;
      const property = fieldOf(found?.name, "parent");
      return (
        found !== null &&
        kindOf(property) === "Property" &&
        fieldOf(property, "computed") !== true &&
        keyName(fieldOf(property, "key")) === "children" &&
        listOf(found.fn, "params")[0] === fieldOf(property, "parent")
      );
    };

    /** A property a package declares of a type, if it has one. @param {ts.Type} type @param {string} name */
    const packageProperty = (type, name) => {
      const property = checker.getPropertyOfType(type, name);
      return property !== undefined && declarationsOf(property).some(inPackage) ? property : undefined;
    };

    /** The return types of a component a tag names, every union member and a deferred type's
     * constraint read: each call signature's, whoever declares the type (`ComponentType`, `FC` and a
     * route's `Component` return `ReactNode`, so a tag of such a type is refused). A member with a
     * `$$typeof` a package declares (`Fragment`, `StrictMode`, `Suspense`, a context, its `Consumer`,
     * and equally every type React declares as exotic, an app type that inherits or intersects one) is
     * exempt and NOT read: its render function is not read, so a raw text it renders is OPEN (R90f,
     * 2b-1L2; `RAW_OPEN`), and its children are read at their own JSX positions. The exception is a
     * package's `_result` (a lazy whose type keeps it): the component it loads is read.
     * @param {ts.Type} type @returns {ts.Type[]} */
    const componentReturns = (type) =>
      membersOf(type).flatMap((member) => {
        if ((member.flags & DEFERRED_FLAGS) !== 0) {
          const constraint = constraintOf(checker, member);
          // A tag of a type with no constraint is read as `unknown` (refused).
          return constraint === undefined ? [checker.getUnknownType()] : componentReturns(constraint);
        }
        if (packageProperty(member, "$$typeof") !== undefined) {
          const loaded = packageProperty(member, "_result");
          return loaded === undefined ? [] : componentReturns(checker.getTypeOfSymbol(loaded));
        }
        return member.getCallSignatures().map((signature) => signature.getReturnType());
      });

    /** A returned expression: a render position when the function is named as a component.
     * @param {unknown} node */
    const returned = (node) => {
      if (componentNamed(functionOf(node))) {
        check(node, typeOf(node), "as what a component returns");
      }
    };

    return {
      /** @param {import("eslint").Rule.Node} node */
      JSXExpressionContainer(node) {
        const expression = fieldOf(node, "expression");
        const parent = fieldOf(node, "parent");
        if (kindOf(expression) === "JSXEmptyExpression") {
          return;
        }
        if (kindOf(parent) !== "JSXAttribute") {
          if (!ownChildren(expression)) {
            check(expression, typeOf(expression), "as a JSX child");
          }
          return;
        }
        const name = context.sourceCode.getText(asNode(fieldOf(parent, "name")));
        if (!(harness && name.startsWith("data-"))) {
          check(expression, typeOf(expression), `as the attribute ${name}`);
        }
      },
      /** @param {import("eslint").Rule.Node} node */
      JSXSpreadChild(node) {
        context.report({ node, messageId: "spread" });
      },
      /** @param {import("eslint").Rule.Node} node */
      JSXOpeningElement(node) {
        const name = fieldOf(node, "name");
        if (kindOf(name) === "JSXIdentifier" && /^[a-z]/u.test(String(nameOf(name)))) {
          return;
        }
        const type = typeOf(name);
        if (membersOf(type).some((member) => (member.flags & (ts.TypeFlags.StringLike | ts.TypeFlags.Any | ts.TypeFlags.Unknown)) !== 0)) {
          context.report({ node: asNode(name), messageId: "tag", data: { type: checker.typeToString(type) } });
          return;
        }
        for (const given of componentReturns(type)) {
          check(name, given, "as what this component returns");
        }
      },
      /** @param {import("eslint").Rule.Node} node */
      ReturnStatement(node) {
        const argument = fieldOf(node, "argument");
        if (argument !== null) {
          returned(argument);
        }
      },
      /** @param {import("eslint").Rule.Node} node */
      ArrowFunctionExpression(node) {
        if (fieldOf(node, "expression") === true) {
          returned(fieldOf(node, "body"));
        }
      },
    };
  },
};

// ---------------------------------------------------------------------------------------------
// The reviewed sites.

/** The sites of `rule` in a file, each with the reports it stood for; at the end of the file, a
 * site that stood other than once is reported.
 * @param {import("eslint").Rule.RuleContext} context @param {string} rule */
function sitesOf(context, rule) {
  const file = relativeOf(context.cwd, context.filename);
  const mine = SITES.filter((site) => site.rule === rule && site.file === file).map((site) => ({ site, count: 0 }));
  return {
    /** Whether a report at `node` is a reviewed site (counted). @param {unknown} node */
    stands(node) {
      const text = context.sourceCode.getText(asNode(node));
      const found = mine.find(({ site }) => site.code === text);
      if (found !== undefined) {
        found.count += 1;
      }
      return found !== undefined;
    },
    /** @param {unknown} program */
    end(program) {
      for (const { site, count } of mine.filter((one) => one.count !== 1)) {
        context.report({ node: asNode(program), messageId: "site", data: { code: site.code, count: String(count) } });
      }
    },
  };
}

const SITE = "A reviewed site of D429 (`scripts/lint/display.mjs`'s SITES) stands once: `{{code}}` stood {{count}} times here. Remove the site, or the code.";

// ---------------------------------------------------------------------------------------------
// R91: the DOM.

/** Whether a type is (or holds) one the DOM library declares: a union's or an intersection's
 * member, a deferred type's constraint, or a type with a member the DOM library declares (a base's
 * members are its own).
 * @param {ts.Program} program @param {ts.TypeChecker} checker @param {ts.Type} type @returns {boolean} */
export function isDomType(program, checker, type) {
  if (type.isUnionOrIntersection()) {
    return type.types.some((member) => isDomType(program, checker, member));
  }
  if ((type.flags & DEFERRED_FLAGS) !== 0) {
    const constraint = constraintOf(checker, type);
    return constraint !== undefined && isDomType(program, checker, constraint);
  }
  const domDeclared = (/** @type {ts.Symbol | undefined} */ symbol) => declarationsOf(symbol).some((declaration) => isDomLibrary(program, declaration.getSourceFile()));
  // A type the DOM library declares, or one with a member it declares: one that extends one, or one
  // rebuilt from one (`Pick`, `Partial`, a mapped type: its members keep the DOM library's declarations).
  return domDeclared(type.getSymbol()) || checker.getPropertiesOfType(type).some(domDeclared);
}

/** Whether a symbol is a function, a method or a constructor (or a value of such a type).
 * @param {ts.TypeChecker} checker @param {ts.Symbol} symbol @param {ts.Node} at */
function isCallable(checker, symbol, at) {
  return (symbol.flags & (ts.SymbolFlags.Function | ts.SymbolFlags.Method)) !== 0 || membersOf(checker.getTypeOfSymbolAtLocation(symbol, at)).some((member) => member.getCallSignatures().length > 0 || member.getConstructSignatures().length > 0);
}

/** Whether a member expression stands where it is written: an assignment's target, an update, a
 * `delete`, a destructuring assignment's target, a default's target, a rest, a loop's variable.
 * @param {unknown} node */
function isWritten(node) {
  const parent = fieldOf(node, "parent");
  const kind = kindOf(parent);
  const target = ["AssignmentExpression", "AssignmentPattern", "ForOfStatement", "ForInStatement"].includes(kind) ? fieldOf(parent, "left") : kind === "Property" && kindOf(fieldOf(parent, "parent")) === "ObjectPattern" ? fieldOf(parent, "value") : kind === "UpdateExpression" ? fieldOf(parent, "argument") : null;
  return target === node || kind === "ArrayPattern" || kind === "RestElement" || (kind === "UnaryExpression" && fieldOf(parent, "operator") === "delete");
}

/** The kinds of a node that names something rather than stands as a value. */
const NAMING = ["ImportSpecifier", "ImportDefaultSpecifier", "ImportNamespaceSpecifier", "ExportSpecifier", "LabeledStatement", "BreakStatement", "ContinueStatement", "MetaProperty"];

/** Whether an Identifier stands as a value (a reference), not as a name the syntax gives (a key, a
 * member's property, a specifier, a label, a declaration's name) or in a type. @param {unknown} node @param {ts.Node} found */
function isReference(node, found) {
  const parent = fieldOf(node, "parent");
  const kind = kindOf(parent);
  const named = (kind === "MemberExpression" ? fieldOf(parent, "property") : ["Property", "PropertyDefinition", "MethodDefinition"].includes(kind) && fieldOf(parent, "shorthand") !== true ? fieldOf(parent, "key") : null) === node && fieldOf(parent, "computed") !== true;
  return !named && !NAMING.includes(kind) && ts.findAncestor(found, (up) => (ts.isTypeNode(up) ? true : ts.isStatement(up) ? "quit" : false)) === undefined;
}

/** `display/dom` (R91). @type {import("eslint").Rule.RuleModule} */
const dom = {
  meta: {
    type: "problem",
    schema: [],
    messages: {
      write: "No write to the DOM but through React (D429, R91): {{name}} is a member of {{type}}, which the DOM library declares. A reviewed site is named in scripts/lint/display.mjs's SITES.",
      call: "No function, method or constructor of the DOM library outside D429's list (R91): {{key}}. A dialog, a notification, a sheet or a DOM write shows text past the visible form; a member reviewed as harmless is added to CALLS with its reason.",
      computed: "No computed member of a value of the DOM library's (D429, R91): what it names is not read.",
      site: SITE,
    },
  },
  create(context) {
    const { program, checker, tsNode, typeOf } = servicesOf(context, "dom");
    const sites = sitesOf(context, "dom");

    /** @param {unknown} node @param {ts.Symbol | undefined} symbol @param {ts.Node} at */
    const checkSymbol = (node, symbol, at) => {
      const target = symbol !== undefined && (symbol.flags & ts.SymbolFlags.Alias) !== 0 ? checker.getAliasedSymbol(symbol) : symbol;
      const keys = domKeys(program, target);
      if (target === undefined || keys.length === 0 || !isCallable(checker, target, at) || keys.every((key) => CALLS.has(key)) || sites.stands(node)) {
        return;
      }
      context.report({ node: asNode(node), messageId: "call", data: { key: keys.filter((key) => !CALLS.has(key)).join(", ") } });
    };

    return {
      /** @param {import("eslint").Rule.Node} node */
      Identifier(node) {
        const found = tsNode(node);
        if (isReference(node, found)) {
          checkSymbol(node, ts.isShorthandPropertyAssignment(found.parent) ? checker.getShorthandAssignmentValueSymbol(found.parent) : checker.getSymbolAtLocation(found), found);
        }
      },
      /** @param {import("eslint").Rule.Node} node */
      MemberExpression(node) {
        const found = tsNode(node);
        const objectType = typeOf(fieldOf(node, "object"));
        const domValue = isDomType(program, checker, objectType);
        const computed = fieldOf(node, "computed") === true;
        const literal = writtenString(fieldOf(node, "property"));
        const member = computed ? (literal === null ? undefined : objectType.getProperty(literal)) : checker.getSymbolAtLocation(/** @type {ts.PropertyAccessExpression} */ (found).name);
        if (domValue && isWritten(node)) {
          if (!sites.stands(node)) {
            context.report({ node, messageId: "write", data: { name: computed ? `[${String(literal)}]` : String(nameOf(fieldOf(node, "property"))), type: checker.typeToString(objectType) } });
          }
          return;
        }
        if (computed && literal === null && domValue) {
          context.report({ node, messageId: "computed" });
          return;
        }
        checkSymbol(node, member, found);
      },
      /** A destructured member of a value of the DOM library's (`const { append } = el`).
       * @param {import("eslint").Rule.Node} node */
      "ObjectPattern > Property"(node) {
        const pattern = tsNode(fieldOf(node, "parent"));
        const type = checker.getTypeAtLocation(pattern);
        const key = fieldOf(node, "key");
        const computed = fieldOf(node, "computed") === true;
        const name = computed ? writtenString(key) : keyName(key);
        if (name !== null) {
          checkSymbol(node, type.getProperty(name), pattern);
        } else if (isDomType(program, checker, type)) {
          context.report({ node, messageId: "computed" });
        }
      },
      /** @param {unknown} node */
      "Program:exit"(node) {
        sites.end(node);
      },
    };
  },
};

// ---------------------------------------------------------------------------------------------
// R92: the address.

/** `display/address` (R92). The option `{ allow }` names the members of `ADDRESS` a file may read
 * (the router module: `Location.pathname`). @type {import("eslint").Rule.RuleModule} */
const address = {
  meta: {
    type: "problem",
    schema: [{ type: "object", properties: { allow: { type: "array", items: { type: "string" } } }, additionalProperties: false }],
    messages: {
      address: "The address is read in src/router/address.tsx alone (D428, D429, R92): {{key}} gives the page's address, the adversary's text (D314).",
    },
  },
  create(context) {
    const { program, checker, tsNode, typeOf } = servicesOf(context, "address");
    const options = /** @type {{ allow?: string[] }[]} */ (context.options);
    const allowed = new Set(options[0]?.allow);
    const refused = new Set(ADDRESS.filter((key) => !allowed.has(key)));

    /** @param {unknown} node @param {ts.Symbol | undefined} symbol */
    const checkSymbol = (node, symbol) => {
      const keys = domKeys(program, symbol).filter((key) => refused.has(key));
      if (keys.length > 0) {
        context.report({ node: asNode(node), messageId: "address", data: { key: keys.join(", ") } });
      }
    };

    /** A member of a type by name: its own, or a union member's. @param {ts.Type} type @param {string} name */
    const memberOf = (type, name) => type.getProperty(name) ?? membersOf(type).map((member) => member.getProperty(name)).find((symbol) => symbol !== undefined);

    return {
      /** @param {import("eslint").Rule.Node} node */
      MemberExpression(node) {
        const literal = writtenString(fieldOf(node, "property"));
        if (fieldOf(node, "computed") !== true) {
          checkSymbol(node, checker.getSymbolAtLocation(/** @type {ts.PropertyAccessExpression} */ (tsNode(node)).name));
        } else if (literal !== null) {
          checkSymbol(node, memberOf(typeOf(fieldOf(node, "object")), literal));
        }
      },
      /** @param {import("eslint").Rule.Node} node */
      "ObjectPattern > Property"(node) {
        const key = fieldOf(node, "key");
        const name = fieldOf(node, "computed") === true ? writtenString(key) : keyName(key);
        if (name !== null) {
          checkSymbol(node, memberOf(checker.getTypeAtLocation(tsNode(fieldOf(node, "parent"))), name));
        }
      },
    };
  },
};

// ---------------------------------------------------------------------------------------------
// R93: the operator's answers.

/** `display/answers` (R93). @type {import("eslint").Rule.RuleModule} */
const answers = {
  meta: {
    type: "problem",
    schema: [],
    messages: {
      answer: "No module outside src/api/ reads an operator answer's raw {{name}} (D426, D429, R93): it stays off every screen until the curation screens read it through the seam (R86).",
      site: SITE,
    },
  },
  create(context) {
    const sites = sitesOf(context, "answers");
    /** @param {import("eslint").Rule.Node} node @param {unknown} key */
    const report = (node, key) => {
      const name = fieldOf(node, "computed") === true ? writtenString(key) : keyName(key);
      if (name !== null && ANSWER_NAMES.includes(name) && !sites.stands(node)) {
        context.report({ node, messageId: "answer", data: { name } });
      }
    };
    return {
      /** @param {import("eslint").Rule.Node} node */
      MemberExpression(node) {
        report(node, fieldOf(node, "property"));
      },
      /** @param {import("eslint").Rule.Node} node */
      "ObjectPattern > Property"(node) {
        report(node, fieldOf(node, "key"));
      },
      /** @param {unknown} node */
      "Program:exit"(node) {
        sites.end(node);
      },
    };
  },
};

// ---------------------------------------------------------------------------------------------
// The elements and attributes.

/** `display/elements`. @type {import("eslint").Rule.RuleModule} */
const elements = {
  meta: {
    type: "problem",
    schema: [],
    messages: {
      element: "The element <{{name}}> is not on D429's allow-list (scripts/lint/display.mjs's ELEMENTS).",
      option: "An <option> is written by src/layout/Option.tsx alone (D428, D429): a screen's own would flatten a Text.",
      attribute: "The attribute {{name}} is not on D429's allow-list (scripts/lint/display.mjs's ATTRIBUTES).",
      spread: "No JSX spread (D429): each attribute and prop is a position the rules read.",
      ref: "ref is written in the reviewed modules alone (D429's REF_MODULES): a DOM node in a component's hands is a write waiting to happen.",
      literal: "{{name}} takes a literal (D429): {{type}}.",
    },
  },
  create(context) {
    const { checker, typeOf } = servicesOf(context, "elements");
    const file = relativeOf(context.cwd, context.filename);
    const harness = file.startsWith(HARNESS);
    return {
      /** @param {import("eslint").Rule.Node} node */
      JSXOpeningElement(node) {
        const tag = fieldOf(node, "name");
        const tagName = context.sourceCode.getText(asNode(tag));
        const intrinsic = kindOf(tag) === "JSXNamespacedName" || (kindOf(tag) === "JSXIdentifier" && /^[a-z]/u.test(tagName));
        if (intrinsic && !ELEMENTS.includes(tagName)) {
          context.report({ node, messageId: "element", data: { name: tagName } });
        }
        if (tagName === "option" && file !== OPTION_MODULE) {
          context.report({ node, messageId: "option" });
        }
        for (const attribute of listOf(node, "attributes")) {
          if (kindOf(attribute) === "JSXSpreadAttribute") {
            context.report({ node: asNode(attribute), messageId: "spread" });
            continue;
          }
          const key = fieldOf(attribute, "name");
          const name = context.sourceCode.getText(asNode(key));
          const listed = kindOf(key) === "JSXIdentifier" && (name.startsWith("aria-") || name.startsWith("data-") || ATTRIBUTES.includes(name));
          if (name === "ref" && !REF_MODULES.includes(file)) {
            context.report({ node: asNode(attribute), messageId: "ref" });
          }
          if (intrinsic && !listed) {
            context.report({ node: asNode(attribute), messageId: "attribute", data: { name } });
          }
          const value = fieldOf(attribute, "value");
          const literalOnly = name.startsWith("aria-") || (name.startsWith("data-") && !harness) || LITERAL_ATTRIBUTES.includes(name);
          const wrong = intrinsic && listed && literalOnly && kindOf(value) === "JSXExpressionContainer" ? notLiteral(checker, typeOf(fieldOf(value, "expression")), name === HREF_TEMPLATE.attribute ? HREF_TEMPLATE : null) : null;
          if (wrong !== null) {
            context.report({ node: asNode(attribute), messageId: "literal", data: { name, type: wrong } });
          }
        }
      },
    };
  },
};

// ---------------------------------------------------------------------------------------------
// The specifiers.

/** `display/specifiers`. The option `{ allowed }` is a `Specifiers` table (required). @type {import("eslint").Rule.RuleModule} */
const specifiers = {
  meta: {
    type: "problem",
    schema: [{ type: "object", properties: { allowed: { type: "object" } }, required: ["allowed"], additionalProperties: false }],
    messages: {
      specifier: "{{specifier}} {{what}} is not on D429's allow-list of specifiers (scripts/lint/display.mjs): a package's module is refused but the names a reason allows.",
      path: "No path into node_modules (D429): {{specifier}}.",
    },
  },
  create(context) {
    const options = /** @type {{ allowed: Specifiers }[]} */ (context.options);
    const allowed = /** @type {{ allowed: Specifiers }} */ (options[0]).allowed;

    /** @param {import("eslint").Rule.Node} node @param {unknown} source @param {{ name: string, type: boolean }[] | null} names */
    const judge = (node, source, names) => {
      const specifier = writtenString(source);
      if (specifier === null) {
        return;
      }
      if (specifier.startsWith(".")) {
        if (/(^|[\\/])node_modules([\\/]|$)/iu.test(specifier)) {
          context.report({ node, messageId: "path", data: { specifier } });
        }
        return;
      }
      const entry = Object.hasOwn(allowed, specifier) ? allowed[specifier] : undefined;
      if (names === null || names.length === 0) {
        context.report({ node, messageId: "specifier", data: { specifier, what: names === null ? "(a whole module)" : "(a side-effect import)" } });
        return;
      }
      const wrong = names.filter(({ name, type }) => entry === undefined || !(entry.values.includes(name) || (type && (entry.types === "*" || entry.types.includes(name)))));
      if (wrong.length > 0) {
        context.report({ node, messageId: "specifier", data: { specifier, what: `{ ${wrong.map(({ name }) => name).join(", ")} }` } });
      }
    };

    /** The names an import or an export statement takes, `null` for a whole module (a namespace, a
     * default, an export-all). @param {unknown} node @param {"importKind" | "exportKind"} kind @returns {{ name: string, type: boolean }[] | null} */
    const namesOf = (node, kind) => {
      const specifiers = listOf(node, "specifiers");
      if (specifiers.some((specifier) => !["ImportSpecifier", "ExportSpecifier"].includes(kindOf(specifier)))) {
        return null;
      }
      return specifiers.map((specifier) => ({
        name: keyName(fieldOf(specifier, kindOf(specifier) === "ImportSpecifier" ? "imported" : "local")),
        type: fieldOf(node, kind) === "type" || fieldOf(specifier, kind) === "type",
      }));
    };

    return {
      /** @param {import("eslint").Rule.Node} node */
      ImportDeclaration(node) {
        judge(node, fieldOf(node, "source"), namesOf(node, "importKind"));
      },
      /** @param {import("eslint").Rule.Node} node */
      ExportNamedDeclaration(node) {
        const source = fieldOf(node, "source");
        if (source !== null) {
          judge(node, source, namesOf(node, "exportKind"));
        }
      },
      /** @param {import("eslint").Rule.Node} node */
      ExportAllDeclaration(node) {
        judge(node, fieldOf(node, "source"), null);
      },
      /** @param {import("eslint").Rule.Node} node */
      ImportExpression(node) {
        judge(node, fieldOf(node, "source"), null);
      },
      /** A type import (`typeof import("react-dom/server")`): a type alone, judged by its specifier.
       * @param {import("eslint").Rule.Node} node */
      TSImportType(node) {
        const source = writtenString(fieldOf(fieldOf(node, "argument"), "literal"));
        if (source !== null && !source.startsWith(".") && !Object.hasOwn(allowed, source)) {
          context.report({ node, messageId: "specifier", data: { specifier: source, what: "(a type import)" } });
        }
      },
    };
  },
};

/** The rules, by their name in the plugin. */
export const RULES = { "raw-text": rawText, dom, address, answers, elements, specifiers };

/** The inline plugin `eslint.config.mjs` registers as `display`. */
export const display = { rules: RULES };
