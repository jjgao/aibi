/**
 * The display table's canaries (D427): the gate (`tests/gates/display.ts`) runs this file with
 * every child run of the unit project and reads both results. The first proves that
 * `expect.hasAssertions()` still fails a test with no assertion under the project's setup (a setup
 * file that neutered it would make this `test.fails` fail); the second that a literal test passes
 * and is reported at its call's own line and column. Change a title or the form of a call here and
 * the gate's constants with it.
 */
import { expect, it, test } from "vitest";

test.fails("D427 canary: a test with no assertion fails under expect.hasAssertions()", () => {
  expect.hasAssertions();
});

it("D427 canary: a literal test passes, reported at its own line and column", () => {
  expect.hasAssertions();
  expect(true).toBe(true);
});
