/**
 * The readers of the address, through the REAL hash router (D428, round 1 of #117): at an address
 * with a right-to-left override in the id and in the query, each reader of the corpus
 * (`tests/gates/router-readers.ts`) puts the address in the DOM, raw. This is the premise of the
 * router gate (`tests/gates/router.test.ts`), which holds that the app may use none of them: a
 * reader that stopped showing the address would be one the gate no longer needs, and a new one is
 * added to the corpus and refused.
 */
import type { ReactElement } from "react";
import { createHashRouter } from "react-router";
import { RouterProvider } from "react-router/dom";
import { afterEach, describe, expect, it } from "vitest";

import { ADDRESS, READERS, type Reader } from "../gates/router-readers";
import { render } from "./render";

afterEach(() => {
  window.history.replaceState(null, "", "/");
});

/** A screen at `datasets/:id` that shows what a reader gives. */
function screen(read: Reader["read"]): () => ReactElement {
  return function Leak() {
    const value = read();
    return <p>{typeof value === "string" ? value : "nothing"}</p>;
  };
}

describe("each reader of the address shows it raw, through the real hash router", () => {
  it.each(READERS)("$label", async ({ read, address }) => {
    window.history.replaceState(null, "", address ?? ADDRESS);
    const Leak = screen(read);
    const shown = await render(
      <RouterProvider
        router={createHashRouter([
          { path: "datasets/:id", element: <Leak /> },
          { path: "*", element: <Leak /> },
        ])}
      />,
    );
    await shown.until((words) => words !== "");
    const text = shown.container.textContent;
    // The override itself, or the address that spells it (percent-encoded), or the file name after it.
    expect(text.includes("‮") || text.includes("%E2%80%AE") || text.includes("txt.exe")).toBe(true);
    shown.unmount();
  });
});
