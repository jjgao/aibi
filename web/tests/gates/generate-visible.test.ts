/**
 * The visible form's generator reads `schemas/visible-form.json` strictly (`scripts/generate-visible.mjs`,
 * D428): each check refuses a file that is not the one the server's export writes, so that the page
 * never escapes by a list it misread. One fixture per check (each check's own control, round 1 of
 * #117: the order, the kept members and the counts could each be dropped with nothing failing), made
 * from the committed file by one edit.
 */
import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import { renderVisible, VisibleError } from "../../scripts/generate-visible.mjs";
import { VISIBLE } from "../../scripts/generate-types.mjs";

const COMMITTED = readFileSync(VISIBLE, "utf8");

type File = Record<string, unknown>;

const unsafeOf = (file: File): number[][] => file["unsafe"] as number[][];
const rawOf = (file: File): File => file["raw_by_d312"] as File;

/** The committed file as an object. */
const parsed = (): File => JSON.parse(COMMITTED) as File;

/** The committed file edited by `change`, as text. */
function edited(change: (file: File) => void): string {
  const file = parsed();
  change(file);
  return JSON.stringify(file);
}

const UNSAFE_COUNT = unsafeOf(parsed()).length;

describe("the generator reads the visible form's file strictly", () => {
  it("renders the committed file, to a module that names its version", () => {
    const text = renderVisible(COMMITTED);
    expect(text).toContain('export const UNIDATA_VERSION = "15.0.0";');
    expect(text).toContain("export const CUT = 2000;");
    expect(text).toContain("export const LISTED = 64;");
    expect(text).toContain("export const KEPT: readonly number[] = Object.freeze([0x9, 0xa, 0x200c, 0x200d]);");
    expect(renderVisible(COMMITTED)).toBe(text);
  });

  const MALFORMED: readonly (readonly [string, string, string])[] = [
    ["the file is no object", "[]", "the file is no object"],
    ["the file is null", "null", "the file is no object"],
    [
      "no version",
      edited((file) => {
        delete file["unidata_version"];
      }),
      "unidata_version is no version",
    ],
    [
      "a version of two numbers",
      edited((file) => {
        file["unidata_version"] = "15.0";
      }),
      "unidata_version is no version",
    ],
    [
      "a version that is a number",
      edited((file) => {
        file["unidata_version"] = 15;
      }),
      "unidata_version is no version",
    ],
    [
      "a cut of 2,001",
      edited((file) => {
        file["cut"] = 2001;
      }),
      "the cut is not 2000",
    ],
    [
      "no cut",
      edited((file) => {
        delete file["cut"];
      }),
      "the cut is not 2000",
    ],
    [
      "a list cut of 65 (D430)",
      edited((file) => {
        file["listed"] = 65;
      }),
      "the list cut is not 64",
    ],
    [
      "no list cut (D430)",
      edited((file) => {
        delete file["listed"];
      }),
      "the list cut is not 64",
    ],
    [
      "the order fold, cut, escape",
      edited((file) => {
        file["order"] = ["fold", "cut", "escape"];
      }),
      "the order is not cut, fold, escape",
    ],
    [
      "the order without the escape",
      edited((file) => {
        file["order"] = ["cut", "fold"];
      }),
      "the order is not cut, fold, escape",
    ],
    [
      "a kept member added (U+200B)",
      edited((file) => {
        file["kept"] = [9, 10, 0x200b, 0x200c, 0x200d];
      }),
      "the kept members are not tab, line feed, ZWNJ and ZWJ",
    ],
    [
      "a kept member dropped (ZWJ)",
      edited((file) => {
        file["kept"] = [9, 10, 0x200c];
      }),
      "the kept members are not tab, line feed, ZWNJ and ZWJ",
    ],
    [
      "the kept members in another order",
      edited((file) => {
        file["kept"] = [10, 9, 0x200c, 0x200d];
      }),
      "the kept members are not tab, line feed, ZWNJ and ZWJ",
    ],
    [
      "the unsafe list is no list",
      edited((file) => {
        file["unsafe"] = {};
      }),
      "unsafe is no list",
    ],
    [
      "a range of three members",
      edited((file) => {
        unsafeOf(file)[0] = [0, 8, 9];
      }),
      "unsafe[0] is no range after the one before it",
    ],
    [
      "a range that is no pair of integers",
      edited((file) => {
        unsafeOf(file)[1] = [11.5, 31];
      }),
      "unsafe[1] is no range after the one before it",
    ],
    [
      "a range past the code points",
      edited((file) => {
        unsafeOf(file).push([0x10ffff, 0x110000]);
      }),
      `unsafe[${String(UNSAFE_COUNT)}] is no range after the one before it`,
    ],
    [
      "a negative code point",
      edited((file) => {
        unsafeOf(file)[0] = [-1, 8];
      }),
      "unsafe[0] is no range after the one before it",
    ],
    [
      "a reversed range",
      edited((file) => {
        unsafeOf(file)[0] = [8, 0];
      }),
      "unsafe[0] is no range after the one before it",
    ],
    [
      "ranges out of order",
      edited((file) => {
        const unsafe = unsafeOf(file);
        const [first = [], second = []] = unsafe;
        unsafe[0] = second;
        unsafe[1] = first;
      }),
      "unsafe[1] is no range after the one before it",
    ],
    [
      "overlapping ranges",
      edited((file) => {
        unsafeOf(file)[1] = [8, 31];
      }),
      "unsafe[1] is no range after the one before it",
    ],
    [
      "a range that repeats the one before",
      edited((file) => {
        unsafeOf(file)[1] = [0, 8];
      }),
      "unsafe[1] is no range after the one before it",
    ],
    [
      "no raw_by_d312 record",
      edited((file) => {
        delete file["raw_by_d312"];
      }),
      "raw_by_d312 is not the record",
    ],
    [
      "a raw_by_d312 that is a list",
      edited((file) => {
        file["raw_by_d312"] = [];
      }),
      "raw_by_d312 is not the record",
    ],
    [
      "another braille blank",
      edited((file) => {
        rawOf(file)["braille_blank"] = 0x2801;
      }),
      "raw_by_d312 is not the record",
    ],
    [
      "a raw range member that is no list",
      edited((file) => {
        rawOf(file)["space_separators"] = 1;
      }),
      "raw_by_d312.space_separators is no list",
    ],
    [
      "a raw range out of order",
      edited((file) => {
        rawOf(file)["default_ignorable_assigned"] = [
          [10, 12],
          [5, 6],
        ];
      }),
      "raw_by_d312.default_ignorable_assigned[1] is no range after the one before it",
    ],
    [
      "a count that is not its ranges'",
      edited((file) => {
        rawOf(file)["space_separators_count"] = 15;
      }),
      "raw_by_d312.space_separators_count is not its ranges' count",
    ],
    [
      "no count",
      edited((file) => {
        delete rawOf(file)["default_ignorable_unassigned_count"];
      }),
      "raw_by_d312.default_ignorable_unassigned_count is not its ranges' count",
    ],
    [
      "a count that is a string",
      edited((file) => {
        rawOf(file)["default_ignorable_assigned_count"] = "267";
      }),
      "raw_by_d312.default_ignorable_assigned_count is not its ranges' count",
    ],
  ];

  it.each(MALFORMED)("refuses %s", (_case, text, message) => {
    expect(() => renderVisible(text)).toThrow(new VisibleError(message));
  });

  it("refuses text that is no JSON, as a SyntaxError that the generation words", () => {
    expect(() => renderVisible("{")).toThrow(SyntaxError);
  });
});
