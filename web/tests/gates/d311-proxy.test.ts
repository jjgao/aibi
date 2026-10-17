/**
 * D311's pages stay the server's default until M5.2-rest (D431, D410): **a proxy, stated as such.**
 * The behaviour is the server's test (`server/tests/core/api/test_d311_until_m52_rest.py`: `serve`'s
 * own entry, run without a `web_bundle`, answers `GET /` and `GET /datasets/<id>` with D311's page
 * while any `M5.2-rest` row's module is absent). This cell is an early, cheap signal beside it: while
 * any row of `M5.2-rest` is not built, the server's `serve.py`, `bundle.py` and `app.py` name no
 * packaged bundle (`importlib.resources`, `resources.files(`, a literal `"aibi/web"` or
 * `("aibi", "web")`) and `app.py` still includes the page's router (`include_router(page_router(`).
 * It catches a spelling, not the behaviour (n5's patterns; the control: today's three files pass).
 */
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { readTable } from "../../scripts/display-table.mjs";
import { WEB } from "./program";

const API = path.join(WEB, "..", "server", "src", "aibi", "core", "api");

/** The spellings of a packaged bundle. */
const PACKAGED = ["importlib.resources", "resources.files(", '"aibi/web"', "'aibi/web'", '("aibi", "web")', "('aibi', 'web')"];

/** The spelling of the page's router included. */
const PAGES = "include_router(page_router(";

/** What the scan finds in the three files' texts: each packaged spelling, and the page's router missing. */
export function proxyProblems(files: Readonly<Record<string, string>>): string[] {
  const found: string[] = [];
  for (const [name, text] of Object.entries(files)) {
    for (const spelling of PACKAGED) {
      if (text.includes(spelling)) {
        found.push(`${name}: ${spelling}`);
      }
    }
  }
  if (!(files["app.py"] ?? "").includes(PAGES)) {
    found.push(`app.py: no ${PAGES}`);
  }
  return found;
}

const FILES = Object.fromEntries(["serve.py", "bundle.py", "app.py"].map((name) => [name, readFileSync(path.join(API, name), "utf8")]));

describe("D311's pages stay the default until M5.2-rest (a proxy of the server's test)", () => {
  const rest = readTable().rows.filter((row) => row.owner === "M5.2-rest");

  it("M5.2-rest owns rows, and while any of their modules is absent the server names no packaged bundle and includes the page's router", () => {
    expect(rest.map((row) => row.id)).toEqual(["R53b", "R60", "R61", "R62", "R65b", "R66b", "R67", "R68b"]);
    const absent = rest.filter((row) => !existsSync(path.join(WEB, row.module)));
    if (absent.length === 0) {
      // M5.2-rest is built: the proxy no longer applies (the server's test says so too).
      return;
    }
    expect(proxyProblems(FILES)).toEqual([]);
  });

  it("the control: today's three files pass, and each spelling is found where it is written", () => {
    expect(proxyProblems(FILES)).toEqual([]);
    expect(proxyProblems({ ...FILES, "serve.py": `${FILES["serve.py"] ?? ""}\nfrom importlib.resources import files\nroot = files("aibi") / "web"\n` })).toEqual(["serve.py: importlib.resources"]);
    expect(proxyProblems({ ...FILES, "bundle.py": `${FILES["bundle.py"] ?? ""}\nroot = resources.files(aibi)\n` })).toEqual(["bundle.py: resources.files("]);
    expect(proxyProblems({ ...FILES, "serve.py": 'BUNDLE = "aibi/web"\n' })).toEqual(['serve.py: "aibi/web"']);
    expect(proxyProblems({ ...FILES, "serve.py": 'BUNDLE = Path(*("aibi", "web"))\n' })).toEqual(['serve.py: ("aibi", "web")']);
    expect(proxyProblems({ ...FILES, "app.py": (FILES["app.py"] ?? "").replace(PAGES, "include_router(bundle_router(") })).toEqual([`app.py: no ${PAGES}`]);
  });
});
