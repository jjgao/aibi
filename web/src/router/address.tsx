/**
 * The router module (D428): the one file of the app that reads the address through the router's
 * `useParams` or the page's `location.pathname` (the readers the lint and the tests below name are
 * refused elsewhere; the others are closed where their text would land, as the last paragraph says),
 * and the one, beside the operator entry's `url.ts`, that writes it.
 * The address is the adversary's (a link from another site, D314): no text of it reaches the DOM.
 * A dataset id is read from it through `idOfAddress` alone (the generated grammar of `idOf`), and
 * an address that is not of it is shown in fixed words, never echoed.
 *
 * - **`redirectAddress`**, before the router starts: the server serves the bundle at
 *   `/datasets/<one segment>` too (D410), which the hash router does not route; the raw, undecoded
 *   segment (no `decodeURIComponent`, so a malformed escape cannot throw) is checked against the
 *   grammar, and the address is replaced (no history entry) by `/#/datasets/<id>`, composed here from
 *   a literal and the `DatasetId`, or by `/` (the catalogue) when the segment is not an id. A segment
 *   with any escape is no id (`%` is outside the grammar), so `orch%61rd` lands on the catalogue.
 *   Whatever the address held besides (a query, a hash: `/datasets/<id>#/datasets/<other>`) is
 *   dropped. Any other path is left alone.
 * - **`DatasetAddress`**, the hash route `#/datasets/:id`: the router's parameter (which it decodes,
 *   `%252F` twice: safe, because the grammar is total, so `orch%61rd` is `orchard`, and a literal
 *   `a?b=c` or `a#b` reach it as `a`, the router splitting the address there before it decodes,
 *   while `a%23b` decodes to `a#b`, which is no id) through `idOfAddress`; an id renders the
 *   dataset's screen with it (`src/dataset/Dataset.tsx`, keyed by the id), anything else the fixed words of
 *   `Refused`. So the same refusal answers twice: a path segment that is no id lands on the catalogue
 *   page, a hash that is none shows `Refused`; and a path segment with an escape is none while the
 *   hash route decodes it (`/datasets/orch%61rd` lands on the catalogue, `#/datasets/orch%61rd` is `orchard`).
 *
 * What is checked outside this module, and no more. (1) The app names an allow-list of the router
 * package alone (`createHashRouter`, `RouteObject`, `RouterProvider`: the lint's
 * `no-restricted-imports`, and `tests/gates/router.test.ts`, which enumerates the installed package's
 * exports and holds every other refused); this module alone imports `useParams`. (2) The router
 * object, its `.state` and `.matches`, a `loader` the compiler types as a route's and
 * `RouterProvider`'s `onError` are referenced by this module and the entries' mains alone
 * (`tests/gates/router-surface.test.ts`, a stopgap kept as defence in depth beside D429's rules). (3) Neither `location`'s `pathname`, `search`,
 * `hash` or `href` nor `document.URL`, `referrer`, `baseURI`, `documentURI` or `.location` is read
 * by name (lint rows, with a cell for each); this module alone reads `location.pathname`. (4) The
 * routes the app gives the router have the keys of an allow-list alone (`path`, `index`, `element`,
 * `lazy` as a function, `children`, `errorElement`; no `Component`, D431), each `element` and
 * `errorElement` an element of one of the app's route screens, walked at run time with each `lazy`
 * result: a `loader`, `action`, `middleware`, `shouldRevalidate` or `handle` is refused however it is
 * typed. **Closed by D429 at the positions it reads** (they were open until 2b-1L, D428): the readers that hand the
 * address back without those names (an element's `baseURI`, the navigation entry's `name`, a
 * `hashchange` listener's `oldURL` and `newURL`, the Navigation API, an alias of a page object the
 * type rule does not see), a route's `middleware` or `shouldRevalidate` or an untyped route literal
 * in a route that is not one of `catalogueRoutes()`'s, a router a main passes to a parameter the
 * compiler types structurally, and an entry's main that renders what the router holds: no text of
 * theirs stands raw at a JSX position or at the DOM's APIs (R90, R91), and the address's members are
 * refused by type (R92); one that reaches a render slot other than at a JSX position (a route's
 * `element`, `root.render`) is R90's flow class, OPEN until 2b-1L2 (D429). The root's
 * error element (`src/catalogue/routes.tsx`) shows `Failure`'s fixed words, never an error's message.
 */
import type { ReactElement } from "react";
import { useParams } from "react-router";

import { type DatasetId, idOfAddress } from "../api";

/** A path the server serves the bundle at for a dataset (D410): `/datasets/<one segment>`. */
const DATASET_PATH = /^\/datasets\/([^/]*)$/u;

/** Replace `/datasets/<segment>` by `/#/datasets/<id>`, or by `/` when the segment is no id. */
export function redirectAddress(): void {
  const found = DATASET_PATH.exec(location.pathname);
  if (found === null) {
    return;
  }
  const id = idOfAddress(found[1] ?? "");
  history.replaceState(null, "", id === null ? "/" : `/#/datasets/${id}`);
}

/** The fixed words of an address that names no dataset. */
export function Refused(): ReactElement {
  return (
    <main>
      <h1>aibi</h1>
      <p>No dataset has that address.</p>
      <p>
        <a href="#/">The catalogue</a>
      </p>
    </main>
  );
}

/** The screen of the dataset the hash route names (`screen`, handed its id), or `Refused`. */
export function DatasetAddress({ screen }: { readonly screen: (id: DatasetId) => ReactElement }): ReactElement {
  const { id } = useParams();
  const found = id === undefined ? null : idOfAddress(id);
  return found === null ? <Refused /> : screen(found);
}
