// @ts-check
/**
 * The page's objects by type (D423): the data of what the page's own types give out of the page's
 * memory, and the lint rule `aibi/page-objects` that holds it. They are here, not in the lint's
 * configuration, so that the gates read the same data (`tests/gates/page-types.test.ts` checks it
 * against what the DOM library declares, member by member) and the configuration registers the rule
 * as an inline plugin.
 */
import ts from "typescript";

// ---------------------------------------------------------------------------------------------
// The page's objects by type (D423). The rules above match names; this one matches what a value IS,
// with the type checker the lint already loads (`projectService`, `strictTypeChecked`), so that an
// alias reached through a member (`window.navigator`, `document.defaultView`, `el.ownerDocument`,
// `event.view`, a return value, a parameter) is seen as the page's object it is.

/** What a type of the page's is refused to give: `*` for every member, else those named. */
export const EVERY = "*";

/** The page's object types of the DOM library (`lib.dom.d.ts`; `globalThis` for the global object's
 * own type) and the members of each that are channels out of the page's memory (D423). A value of
 * one of these types, or of a type that contains one (a union, an intersection, a generic's
 * constraint, a subtype), may stand only as the object of a member access that is no channel. */
export const PAGE_TYPES = new Map(/** @type {[string, string | string[]][]} */ ([
  ["Window", ["name", "open", "caches", "localStorage", "sessionStorage", "indexedDB", "postMessage"]],
  ["globalThis", ["name", "open", "caches", "localStorage", "sessionStorage", "indexedDB", "postMessage"]],
  ["Location", ["hash", "href", "replace", "assign"]],
  ["Navigator", ["storage", "clipboard", "locks", "registerProtocolHandler", "mediaSession"]],
  ["Document", ["cookie", "open", "write", "writeln"]],
  ["ShadowRoot", []],
  ["History", ["pushState", "replaceState"]],
  ["Storage", EVERY],
  ["StorageManager", EVERY],
  ["Clipboard", EVERY],
  ["CacheStorage", EVERY],
  ["Cache", EVERY],
]));

/** The window's own channels, which are also globals (`open(u)`, `postMessage(m)`, `caches`): the
 * members of `Window`'s list. */
export const WINDOW_CHANNELS = (() => {
  const found = PAGE_TYPES.get("Window");
  return Array.isArray(found) ? found : [];
})();

/** Whether a symbol is a global the default library declares (and nothing of the program shadows).
 * @param {import("typescript").Program} program @param {import("typescript").Symbol} symbol */
export function isLibraryGlobal(program, symbol) {
  const declarations = symbol.declarations ?? [];
  return declarations.length > 0 && declarations.every((declaration) => program.isSourceFileDefaultLibrary(declaration.getSourceFile()));
}

/** The members that give a page object from another value (`el.ownerDocument`, `event.view`,
 * `window.window`): refused wherever they give one, whatever the value they are read from. */
export const REACHERS = new Set(["defaultView", "ownerDocument", "view", "window", "self", "top", "parent", "frames", "opener"]);

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
export function pageTypeOf(checker, program, type, seen = new Set(), depth = 0) {
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
export const pageObjects = {
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
    const windowChannels = new Set(WINDOW_CHANNELS);
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
        const global =
          tsNode === undefined || ts.isTypeNode(tsNode.parent)
            ? undefined
            : ts.isShorthandPropertyAssignment(tsNode.parent)
              ? checker.getShorthandAssignmentValueSymbol(tsNode.parent)
              : checker.getSymbolAtLocation(tsNode);
        if (global !== undefined && windowChannels.has(global.getName()) && isLibraryGlobal(program, global)) {
          // The window's own channel as a function or a binding (`open`, `postMessage`, `caches`):
          // not a member of a page object, so a value of the page's types never stands for it.
          context.report({ node, messageId: "channel" });
        }
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

