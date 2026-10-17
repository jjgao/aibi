/**
 * Numbers in the DOM (D419, D430): the one module that shows a number, imported by the index alone.
 *
 * - **`Num`** shows a server number by its exact source text (`1e-07`, `-0.0`, `1.10`,
 *   `9007199254740993`), never rounded, never converted: the box's text read as glyphs of JSON's
 *   number grammar (`numbers.ts`' `glyphsOf`), each a literal type, in a `<bdi class="number">` (a
 *   number reads left to right, whatever the text around it). **Its element holds the box, not the
 *   digits**: `Num` renders a module-private context's provider around `Digits`, which reads the box
 *   from the context as it renders, so `Num({ value }).props` gives the box back and `Digits` called
 *   outside a render has no box (D419's boundary: a detached root and its `textContent` would read
 *   the digits, deliberate code, closed in the app by D429's specifier allow-list, `react-dom/client`
 *   the entries' alone). `Num` takes a `ServerNumber` alone: a number that may be `null` is shown
 *   through `Figure` (`src/layout/Figure.tsx`), which says why it is null (R06, R14). A box whose text
 *   is not of the grammar (none the decoder makes) is shown as not shown, in fixed words.
 * - **`listOf(hit, at)`**: one of **D312's lists** (a catalogue hit's domain tags, or one facet's
 *   values, named by its key), found once, by position: the first `LISTED` members to show **and**
 *   "and N more" for the rest, never by an array's `.length` (the display lint refuses a
 *   non-literal number at a JSX position, D429, so `listOf` is how a count of a list the page made
 *   from what the server sent is shown: O-4 of D427). Members and remainder come from the one list, so
 *   `List` cannot show one list and count another. The page counts only what the server sent, every
 *   member of which is disclosed; a list the server cut itself (`tables`, with `tables_left_out`) is
 *   shown with the server's number through `Num`, never counted here.
 * - **`RetryAfter`**: a refusal's `Retry-After`, in whole seconds, where the server gave one (D265,
 *   D422): the client's parse of the header, a count the page shows and never computes with.
 *
 * The lint holds this module and `numbers.ts` to D419's number names (no `Number`, `String`, `Math`,
 * `parseFloat`, unary `+`, a bitwise operator, and the rest) and refuses these names of a rounding
 * or a formatting: `Intl`, `toFixed`, `toPrecision`, `toExponential`, `toLocaleString`. It holds
 * those names and no spelling else: a typed array's conversion (`Float32Array.of(n)[0]`), a slice
 * of the glyph array or a rounding narrowed by a type predicate is lint-clean, and held by the
 * tests' number corpus (`format.test.tsx`) alone. A tally of the members
 * (`slice(LISTED).map(() => <span>…</span>)`) is lint-clean too, a second door for a count the
 * page made of members the server sent (D430's open list).
 */
import { createContext, type ReactElement, useContext } from "react";

import { entriesOf, type ServerNumber, type ServerText } from "./box";
import type { components } from "./generated/openapi";
import { countGlyphs, glyphsOf, LISTED, remainingOf } from "./numbers";
import { sameText } from "./text";
import type { Boxed, BoxedRefusals } from "./typed";

/** A catalogue hit, as the typed answers give it. */
type Hit = Boxed<components["schemas"]["CatalogHit"]>;

/** D312's lists of a catalogue hit (`CatalogHit` is the one answer that has them): its domain tags
 * and its facets' values. A hit is one. */
export interface Lists {
  readonly domain_tags: Hit["domain_tags"];
  readonly facets: Hit["facets"];
}

/** One facet of a hit, named by its key (a `ServerText` from `entriesOf(hit.facets)`). */
export interface Facet {
  readonly facet: ServerText;
}

/** D312's lists of a hit: its domain tags, or one facet's values. */
export type Listed = "domain_tags" | Facet;

/** A refused outcome (D426): its refusals, its status and its `Retry-After` (the refused member of
 * `Outcome`, named; a test holds the two equal). */
export interface Refused {
  readonly kind: "refused";
  readonly status: number;
  readonly refusals: BoxedRefusals;
  readonly retryAfter: number | null;
}

/** The words of a number whose text is not one the page reads: fixed. */
export const NOT_READ = "not shown: not a number this page reads";

/** The number `Digits` shows: the box, never its text. */
const NUMBER = createContext<ServerNumber | null>(null);

/** The digits of the number of the nearest `Num`: its source text, as glyphs. */
function Digits(): ReactElement {
  const number = useContext(NUMBER);
  const glyphs = number === null ? null : glyphsOf(number);
  return glyphs === null ? <span className="number unread">{NOT_READ}</span> : <bdi className="number">{glyphs}</bdi>;
}

/** A server number, shown by its exact source text. */
export function Num({ value }: { readonly value: ServerNumber }): ReactElement {
  return (
    <NUMBER value={value}>
      <Digits />
    </NUMBER>
  );
}

/** A member of one of D312's lists of a hit: a data token. */
type Member = Hit["domain_tags"][number];

/** The members of one of D312's lists of a hit (none, for a facet the hit does not have). */
function membersAt(hit: Lists, at: Listed): readonly Member[] {
  if (at === "domain_tags") {
    return hit.domain_tags;
  }
  for (const [key, values] of entriesOf(hit.facets)) {
    if (sameText(key, at.facet)) {
      return values;
    }
  }
  return [];
}

/** One of D312's lists of a hit, as shown: its first `LISTED` members, and "and N more" (`null`
 * when it has no more). */
export interface ListShown {
  readonly members: readonly Member[];
  readonly more: ReactElement | null;
}

/** One of D312's lists of a hit, found once: the members to show **and** how many more there are,
 * of the one list `at` names (so that the two cannot be of different lists). */
export function listOf(hit: Lists, at: Listed): ListShown {
  const all = membersAt(hit, at);
  const more = remainingOf(all.length);
  return {
    members: all.slice(0, LISTED),
    more:
      more === 0 ? null : (
        <span className="more">
          and <bdi className="number">{countGlyphs(more)}</bdi> more
        </span>
      ),
  };
}

/** A refusal's `Retry-After`, where the server gave one: when to ask again. */
export function RetryAfter({ refused }: { readonly refused: Refused }): ReactElement | null {
  const seconds = refused.retryAfter;
  return seconds === null ? null : (
    <p className="retry">
      Try again after <bdi className="number">{countGlyphs(seconds)}</bdi> seconds (the server&apos;s Retry-After).
    </p>
  );
}
