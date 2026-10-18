/**
 * R08 (D428, the display table's row for the address's door): a dataset id from the address is
 * checked by the dataset grammar before any use, never decoded before the check and never echoed.
 * The row's test file names no hook (D427's gate refuses one), so the address is put back by the
 * test itself; the rest of the address's cells (the corpus through the real hash router, the
 * redirect's table, the root's error element) are `address.test.tsx`.
 */
import { createHashRouter } from "react-router";
import { RouterProvider } from "react-router/dom";
import { expect, it } from "vitest";

import { NotFound } from "../../src/catalogue/NotFound";
import { catalogueRoutes } from "../../src/catalogue/routes";
import { redirectAddress } from "../../src/router/address";
import { isUnsafe } from "./injection";
import { render } from "./render";

it("R08: an id from the path is checked by the grammar before any use and never echoed: a refused id gives fixed words", async () => {
  expect.hasAssertions();
  try {
    window.history.replaceState(null, "", "/datasets/%E2%80%AEtxt.exe");
    redirectAddress();
    expect(`${window.location.pathname}${window.location.search}${window.location.hash}`).toBe("/");
    // Never decoded before the check: decoded, `orch%61rd` would be the id `orchard`, and a malformed escape would throw.
    window.history.replaceState(null, "", "/datasets/orch%61rd");
    redirectAddress();
    expect(`${window.location.pathname}${window.location.search}${window.location.hash}`).toBe("/");
    window.history.replaceState(null, "", "/datasets/%E2%80");
    expect(() => {
      redirectAddress();
    }).not.toThrow();
    expect(`${window.location.pathname}${window.location.search}${window.location.hash}`).toBe("/");
    window.history.replaceState(null, "", "/#/datasets/%E2%80%AEtxt.exe");
    const shown = await render(<RouterProvider router={createHashRouter(catalogueRoutes(() => Promise.resolve({ NotFound })))} />);
    const words = await shown.until((found) => found.startsWith("aibi No dataset has that address."));
    expect(words).toBe("aibi No dataset has that address. The catalogue");
    expect(shown.container.textContent.includes("txt.exe")).toBe(false);
    expect(Array.from(shown.container.textContent).filter((character) => isUnsafe(character.codePointAt(0) ?? 0))).toEqual([]);
    shown.unmount();
  } finally {
    window.history.replaceState(null, "", "/");
  }
});
