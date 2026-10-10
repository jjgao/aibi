/**
 * The type-aware lint's table of the page's types, held against what the DOM library declares
 * (D423, round 3). The rule `aibi/page-objects` (`scripts/page-objects.mjs`) refuses by a list of
 * channels and of reachers; this file is its self-check, **generated from `lib.dom` through the type
 * checker**, not from a hand list (`scripts/page-members.mjs`):
 *
 * - every member each page type declares is classified: a channel, a reacher, an object (it gives a
 *   page object and is allowed as the object of a member access) or allowed (the checked-in fixture
 *   `tests/fixtures/page-members.json`); a member the library declares that no list knows (a
 *   TypeScript upgrade) fails, as does a member that gives a page object (its type, or its call's
 *   result, is or holds one) and is only "allowed", an "object" that gives none, and a fixture entry
 *   the library no longer declares;
 * - every global of the library that is a channel of `Window` (`open`, `postMessage`, `caches`, ...:
 *   a function or a binding, no member of a page object) is refused by the type rule in every
 *   position an alias can stand in.
 */
import path from "node:path";

import { ESLint } from "eslint";
import { describe, expect, it } from "vitest";
import ts from "typescript";

import { EVERY, isLibraryGlobal, PAGE_TYPES, REACHERS, WINDOW_CHANNELS } from "../../scripts/page-objects.mjs";
import { classify, domProgram, liveMembers, readFixture } from "../../scripts/page-members.mjs";
import { WEB } from "./program";

const live = liveMembers();
const fixture = readFixture();

describe("every member of each page type the DOM library declares is classified", () => {
  it("covers the rule's own list of types, and each type is found in the library", () => {
    expect(Object.keys(live).sort()).toEqual([...PAGE_TYPES.keys()].filter((name) => name !== "globalThis").sort());
    for (const [type, members] of Object.entries(live)) {
      expect(Object.keys(members).length, type).toBeGreaterThan(3);
    }
    expect(Object.keys(fixture).sort()).toEqual(Object.keys(live).sort());
  });

  it("reads what a member gives: its own type, and its call's result", () => {
    expect(live["Window"]?.["document"]).toBe("Document");
    expect(live["Window"]?.["location"]).toBe("Location");
    expect(live["Window"]?.["navigator"]).toBe("Navigator");
    expect(live["Document"]?.["defaultView"]).toBe("Window");
    // A method whose result is a page object (`open` returns a window), however it is classified.
    expect(live["Window"]?.["open"]).toBe("Window");
    expect(live["Document"]?.["open"]).not.toBeNull();
    expect(live["Window"]?.["alert"]).toBeNull();
    expect(live["Navigator"]?.["userAgent"]).toBeNull();
  });

  it("knows every member: a new one in the library (a TypeScript upgrade) fails here, to be classified", () => {
    const unknown = Object.entries(live).flatMap(([type, members]) => Object.keys(members).filter((name) => classify(type, name, fixture) === null).map((name) => `${type}.${name}`));
    expect(unknown).toEqual([]);
  });

  it("lists as a channel, a reacher or an object every member that gives a page object", () => {
    const loose = Object.entries(live).flatMap(([type, members]) =>
      Object.entries(members)
        .filter(([name, gives]) => gives !== null && classify(type, name, fixture) === "allowed")
        .map(([name, gives]) => `${type}.${name} gives ${String(gives)}`),
    );
    expect(loose).toEqual([]);
  });

  it("lists as an object only a member that gives a page object", () => {
    const plain = Object.entries(live).flatMap(([type, members]) =>
      Object.entries(members)
        .filter(([name, gives]) => gives === null && classify(type, name, fixture) === "object")
        .map(([name]) => `${type}.${name}`),
    );
    expect(plain).toEqual([]);
  });

  it("names in the fixture only members the library still declares, each once, in one list", () => {
    for (const [type, entry] of Object.entries(fixture)) {
      const members = live[type] ?? {};
      const listed = [...entry.object, ...entry.allowed];
      expect(listed.filter((name) => !(name in members)), `${type}: members the library no longer declares`).toEqual([]);
      expect(new Set(listed).size, `${type}: a member listed twice`).toBe(listed.length);
      expect(listed.filter((name) => classify(type, name, { [type]: { object: [], allowed: [] } }) !== null), `${type}: a member listed that a channel or a reacher already is`).toEqual([]);
    }
  });

  it("has every channel of every type a member of that type or of the library's globals (no misspelt channel)", () => {
    for (const [type, channels] of PAGE_TYPES) {
      if (channels === EVERY || type === "globalThis") {
        continue;
      }
      for (const channel of channels) {
        const declared = type in live && channel in (live[type] ?? {});
        // `Window`'s channels may be declared by the library as globals alone (`open`, `name`).
        expect(declared || (type === "Window" && globalNamed(channel) !== undefined), `${type}.${channel}`).toBe(true);
      }
    }
  });

  it("has every reacher name declared by the library on some page type or on an event or a node", () => {
    expect([...REACHERS].filter((name) => !Object.values(live).some((members) => name in members) && !["view", "ownerDocument"].includes(name))).toEqual([]);
  });

  it("is a classification with every category used (a self-check of the self-check)", () => {
    const seen = new Set(Object.entries(live).flatMap(([type, members]) => Object.keys(members).map((name) => classify(type, name, fixture))));
    expect([...seen].sort()).toEqual(["allowed", "channel", "object", "reacher"]);
  });
});

const { checker, file, program } = domProgram();

/** The library's global value named `name`, if it declares one. */
function globalNamed(name: string): ts.Symbol | undefined {
  return checker.getSymbolsInScope(file, ts.SymbolFlags.Value).find((symbol) => symbol.getName() === name && isLibraryGlobal(program, symbol));
}

/** The globals of the library that are a channel of `Window`, found by the checker. */
const GLOBAL_CHANNELS = WINDOW_CHANNELS.filter((name) => globalNamed(name) !== undefined);

const PARSER = {
  languageOptions: {
    parserOptions: {
      projectService: {
        allowDefaultProject: ["src/*.tsx", "src/api/probe.ts", "src/operator/probe.tsx"],
        defaultProject: "tsconfig.app.json",
        maximumDefaultProjectFileMatchCount_THIS_WILL_SLOW_DOWN_LINTING: 16,
      },
    },
  },
};
const eslint = new ESLint({ cwd: WEB, overrideConfig: PARSER });

async function typeRuleRefuses(code: string, file_: string): Promise<boolean> {
  const [result] = await eslint.lintText(code, { filePath: path.join(WEB, file_) });
  const messages = result?.messages ?? [];
  expect(messages.filter((message) => message.fatal === true).map((message) => message.message)).toEqual([]);
  return messages.some((message) => message.ruleId === "aibi/page-objects");
}

const body = (statements: string): string => `export const f = async (t: string, use: (x: unknown) => void) => { ${statements} };`;

/** Every position a global channel can stand in. `@G@` is its name. */
const POSITIONS: readonly (readonly [string, string])[] = [
  ["a call", "use(@G@(t));"],
  ["a call as a statement", "@G@(t);"],
  ["an optional call", "use(@G@?.(t));"],
  ["a call whose result is only a member's object", "use(@G@(t)?.focus);"],
  ["an alias", "const o = @G@; use(o);"],
  ["an alias, called", "const o = @G@; use(o(t));"],
  ["an alias, called, its result's member", "const o = @G@; use(o(t)?.focus);"],
  ["an argument", "use(@G@);"],
  ["an array element", "use([@G@]);"],
  ["an object's value", "use({ a: @G@ });"],
  ["a shorthand member", "const o = { @G@ }; use(o);"],
  ["a spread", "use({ ...@G@ });"],
  ["a returned value", "const g = () => @G@; use(g);"],
  ["a destructuring's source", "const { length: a } = @G@; use(a);"],
  ["a default value", "((a = @G@) => use(a))();"],
  ["a conditional", "use(t === '' ? @G@ : null);"],
  ["a logical operand", "use(@G@ ?? null);"],
  ["a typeof", "use(typeof @G@);"],
  ["a template", "use(`${@G@}`);"],
  ["a call through .call", "use(@G@.call(null, t));"],
  ["a call through .apply", "use(@G@.apply(null, [t]));"],
  ["a bound function", "use(@G@.bind(null));"],
  ["a bound function, called", "use(@G@.bind(null)(t));"],
  ["a member of it", "use(@G@.length);"],
  ["an optional chain on it", "use(@G@?.length);"],
  ["a written value", "@G@ = t;"],
  ["a computed member of it", "use(@G@[t]);"],
];

const LAYERS = ["src/probe.tsx", "src/api/probe.ts", "src/operator/probe.tsx"] as const;

describe("every global of the library that is a channel of Window is refused in every position (generated)", () => {
  it("finds them through the checker: the window's own functions and bindings, by symbol", () => {
    expect(GLOBAL_CHANNELS.length).toBeGreaterThanOrEqual(6);
    expect(GLOBAL_CHANNELS).toContain("open");
    expect(GLOBAL_CHANNELS).toContain("postMessage");
    expect(GLOBAL_CHANNELS).toContain("localStorage");
    // A channel that is no global (a member of `Window` alone) is not among them.
    expect(WINDOW_CHANNELS.filter((name) => !GLOBAL_CHANNELS.includes(name)).every((name) => globalNamed(name) === undefined)).toBe(true);
  });

  it("refuses each, in each position, by the type rule itself", async () => {
    const accepted: string[] = [];
    for (const name of GLOBAL_CHANNELS) {
      for (const [position, template] of POSITIONS) {
        const code = body(template.replaceAll("@G@", name));
        if (!(await typeRuleRefuses(code, "src/probe.tsx"))) {
          accepted.push(`${name} | ${position} | ${code}`);
        }
      }
    }
    expect(accepted).toEqual([]);
  });

  it.each(LAYERS.slice(1))("refuses each, in a call, an alias, .call and an argument, in %s", async (layer) => {
    const accepted: string[] = [];
    for (const name of GLOBAL_CHANNELS) {
      for (const [position, template] of POSITIONS.filter(([label]) => ["an alias", "a call through .call", "an argument", "a call"].includes(label))) {
        const code = body(template.replaceAll("@G@", name));
        if (!(await typeRuleRefuses(code, layer))) {
          accepted.push(`${name} | ${position}`);
        }
      }
    }
    expect(accepted).toEqual([]);
  });

  it("lets a local binding of the same name pass the type rule (decided by the symbol, not the name)", async () => {
    for (const name of GLOBAL_CHANNELS.filter((one) => one !== "name")) {
      const code = body(`const ${name} = (x: string) => x; use(${name}(t)); const o = ${name}; use(o);`);
      expect(await typeRuleRefuses(code, "src/probe.tsx"), name).toBe(false);
    }
  });

  it("refuses the members of a page object that open a window or write a page, however the object was reached", async () => {
    for (const code of [
      "document.open('/c?' + t, '', '')?.focus();",
      "document.open('/c?' + t);",
      "const d = window.document; d.open('/c?' + t, '', '');",
      "document.write(t);",
      "document.writeln(t);",
      "window.open('/c?' + t)?.focus();",
      "void navigator.locks;",
      "void navigator.registerProtocolHandler;",
      "void navigator.mediaSession;",
    ]) {
      expect(await typeRuleRefuses(body(code), "src/probe.tsx"), code).toBe(true);
    }
  });
});
