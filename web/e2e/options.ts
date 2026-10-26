/**
 * Finding a list's option by its id (D441): an option's text is its id, then ` — ` and its name when
 * it has one, so a test that chooses a column or a table names its id and this finds the one option
 * whose text is that id or starts with it and the separator, and chooses it by its index (an option's
 * value is its index, D428). Shared by the browser matrix (`choose`) and the unit tests (`optionAt`).
 *
 * It reads each option's `textContent`, not its `text` (which strips and collapses white space, so an
 * empty name's `id — ` would read `id —`). An id holds no space (its grammar), so the part before the
 * first ` — ` is the id whatever the name holds (another option's id, the separator itself).
 */
import type { Locator } from "@playwright/test";

/** The words between an option's id and its name (`src/layout/Option.tsx`'s, spelled again here so
 * that a change to either is seen). */
export const SEPARATOR = " \u2014 ";

/** The placeholder, a list's first option (`src/layout/Option.tsx`'s `NO_OPTION`, spelled again here). */
const NO_OPTION = "(choose)";

/** How long `choose` waits for a list's first option beyond the placeholder, in milliseconds. */
const LIST_WAIT = 5_000;

/** The index of the one option whose text is `id`, or starts with `id` and the separator; anything
 * but exactly one is the test's failure. */
export function optionAt(texts: readonly (string | null)[], id: string): number {
  const found = texts.flatMap((text, index) => (text !== null && (text === id || text.startsWith(`${id}${SEPARATOR}`)) ? [index] : []));
  const [index] = found;
  if (index === undefined || found.length !== 1) {
    throw new Error(`${String(found.length)} options of the id ${id}`);
  }
  return index;
}

/** The id part of an option's text: before the first separator (an id holds no space). */
export function idPart(text: string): string {
  const at = text.indexOf(SEPARATOR);
  return at === -1 ? text : text.slice(0, at);
}

/** Choose, in a list of the browser matrix, the option of `id` (by its index). `"(choose)"` (the
 * placeholder, the list's first option) is found as any id is; a list with no option of the id fails
 * as `optionAt` says (`0 options of the id …`), after a bounded wait for the list's first option
 * beyond the placeholder. */
export async function choose(select: Locator, id: string): Promise<void> {
  // The list is waited for as `selectOption` waits for it (the test's timeout). `allTextContents`
  // does not wait: the options beyond the placeholder are attached before the texts are read (the
  // `selectOption` this replaced waited for its option). That wait is bounded, not the test's
  // timeout: a list with none (every option filtered out) reaches `optionAt`, which names the id,
  // and the placeholder never needs it.
  await select.waitFor({ state: "attached" });
  if (id !== NO_OPTION) {
    await select.locator("option").nth(1).waitFor({ state: "attached", timeout: LIST_WAIT }).catch(() => undefined);
  }
  const index = optionAt(await select.locator("option").allTextContents(), id);
  await select.selectOption({ index });
}
