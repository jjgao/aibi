// @ts-check
/**
 * The lint (D418): typescript-eslint's `strictTypeChecked` over every file of the package, no
 * inline configuration (`noInlineConfig`: an `eslint-disable` comment silences nothing and is
 * itself reported), and for the app's source the basics of the policy: no console, no HTML or
 * script sink. Trusted Types is the barrier at run time (D411); the sink rules are the lint's
 * defence in depth, and they ban names, not spellings: every name in `SINK_NAMES` (one table) is
 * refused as an identifier (a property, a key, a binding, a call), a string or template key or
 * argument, a computed member, a JSX attribute or element name, whatever the position or the
 * case. Beside the table: `document.write` and `writeln`, a computed member of `globalThis`,
 * `window`, `self`, `top`, `parent` or `frames`, an `on...` or `srcdoc` attribute set by `setAttribute` (and
 * `setAttribute` or `createElement` with a name not written out), and a `javascript:` string.
 *
 * What the lint sees, positively: a sink only where its name is written out, as an identifier, a
 * property, a string or template, or a JSX name (and `document.write` and `writeln` on `document`
 * or `x.document`), and the globals `Function`, `Reflect` and `DOMParser` by reference. A sink
 * reached any other way (an alias such as `ownerDocument` or a destructuring, `.constructor`, a
 * computed or built name, a string a URL parser reads as `javascript:`) is Trusted Types' and the
 * policy's to stop at run time (D411's headers, pinned by the server's tests and the matrix; the
 * lint is defence in depth, not the barrier). M5.1c-1b adds
 * the number box's rules to this file, and lints what is here already.
 */
import js from "@eslint/js";
import { defineConfig } from "eslint/config";
import tseslint from "typescript-eslint";

const SINK = "No HTML or script sink: the policy refuses one (D411).";

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

const NAME = `/^(${SINK_NAMES.join("|")})$/i`;
const NAME_AS_TEXT = `/^(${SINK_NAMES.filter((name) => name !== "Function" && name !== "Reflect").join("|")})$/i`;

/** A call of a method or function named `method`, written `x.method(`, `method(` or `x["method"](`.
 * @param {string} method */
const called = (method) =>
  `CallExpression:matches([callee.name='${method}'], [callee.property.name='${method}'], [callee.property.value='${method}'])`;

/** Not a name written out: neither a string nor a template without a substitution.
 * @param {number} position */
const notWritten = (position) =>
  `:not([arguments.${String(position)}.type='Literal']):not([arguments.${String(position)}.type='TemplateLiteral'][arguments.${String(position)}.expressions.length=0])`;

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
    },
  },
  {
    files: ["src/**"],
    rules: {
      "no-console": "error",
      "no-eval": "error",
      "no-implied-eval": "error",
      "no-new-func": "error",
      "no-restricted-globals": ["error", ...SINK_GLOBALS.map((name) => ({ name, message: SINK }))],
      "no-restricted-syntax": [
        "error",
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
        {
          selector: `${called("setAttribute")}[arguments.0.type='Literal'][arguments.0.value=/^(on|srcdoc$)/i]`,
          message: SINK,
        },
        {
          selector: `${called("setAttribute")}:not([arguments.0.type='Literal'])`,
          message: "No attribute named at run time (it can name an on... handler or srcdoc).",
        },
        {
          selector: "MemberExpression[property.name='setAttribute']:not(CallExpression > MemberExpression.callee)",
          message: "setAttribute is only called, so that the name it sets is the one written out.",
        },
        {
          selector: `${called("createElement")}${notWritten(0)}`,
          message: "No element named at run time (it can name a script).",
        },
        {
          selector: `${called("createElementNS")}${notWritten(1)}`,
          message: "No element named at run time (it can name a script).",
        },
        { selector: "Literal[value=/^\\s*javascript:/i]", message: SINK },
        { selector: "TemplateElement[value.cooked=/^\\s*javascript:/i]", message: SINK },
      ],
    },
  },
);
