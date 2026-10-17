/**
 * `Link` (D428, D431; D427's R55): a link to a dataset's screen is `#/datasets/<id>`, a fragment of
 * the page's own document, composed by `Link` from a `DatasetId` alone (`idOf` of a server text,
 * `idOfAddress` of the address: the generated grammar bounds its characters). Text that is not of
 * the grammar gives no id, so no link: never `javascript:`, another origin, a path or a query. The
 * type side (a string or a box refused as a target) is `tests/gates/mints.test.ts`'s; the lint's
 * `HREF_TEMPLATE` holds the attribute's form.
 */
import { describe, expect, it } from "vitest";

import { boxText } from "../../src/api/box";
import { idOf, idOfAddress } from "../../src/api/text";
import { Link } from "../../src/layout/Link";
import { render } from "./render";

describe("Link", () => {
  it("R55: a link to a dataset's screen is #/datasets/<id>, composed by Link from a DatasetId alone, never from text, the address or another origin", async () => {
    expect.hasAssertions();
    for (const id of [idOf(boxText("orchard")), idOfAddress("orchard"), idOf(boxText("a_1"))]) {
      expect(id).not.toBeNull();
      if (id !== null) {
        const shown = await render(<Link to={id}>x</Link>);
        const anchor = shown.container.querySelector("a");
        expect(anchor?.getAttribute("href")).toBe(`#/datasets/${id}`);
        expect([...(anchor?.attributes ?? [])].map((attribute) => attribute.name)).toEqual(["href"]);
        shown.unmount();
      }
    }
    // Texts that would make another target are no id: no link can be made of them.
    for (const text of ["javascript:alert(1)", "//evil.example", "https://evil.example/x", "a/b", "a?b=c", "a#b", "../x", "%2e%2e", "ORCHARD", "orch%61rd", "\u202etxt", "", "a b"]) {
      expect(idOf(boxText(text)), text).toBeNull();
      expect(idOfAddress(text), text).toBeNull();
    }
  });

  it("R55: the target is the page's own fragment, whatever the children", async () => {
    expect.hasAssertions();
    const id = idOfAddress("orchard");
    if (id === null) {
      throw new Error("orchard is an id");
    }
    const shown = await render(
      <Link to={id}>
        <span>javascript:alert(1)</span>
      </Link>,
    );
    expect(shown.container.querySelector("a")?.getAttribute("href")).toBe("#/datasets/orchard");
    shown.unmount();
  });
});
