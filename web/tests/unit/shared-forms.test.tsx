/**
 * Each shared component of D430 shows the same thing whatever form an ancestor sets (D428: each
 * layout component sets its own form around the door, so no component's output depends on an
 * ancestor): `Caveats`, `Inspection`, `Refusals`, `Figure`, `Count`, `List`, `ServerList`,
 * `Disclosure`, `ReleaseRef` and `Num`, each rendered alone and under each of the three forms, with
 * a text of every kind (a cut-length text, a bidi control, markup) in its data positions.
 */
import type { ReactElement } from "react";
import { describe, expect, it } from "vitest";

import { boxMap, boxText, type ServerNumber, type ServerText } from "../../src/api/box";
import { decode } from "../../src/api/decode";
import { Num, type Refused } from "../../src/api/format";
import { type BoxedCaveat, Caveats, Inspection } from "../../src/layout/Caveat";
import { Disclosure } from "../../src/layout/Disclosure";
import { Count, Figure } from "../../src/layout/Figure";
import { type Form, FORM } from "../../src/layout/form";
import { List, ServerList } from "../../src/layout/List";
import { Refusals } from "../../src/layout/Refusal";
import { ReleaseRef } from "../../src/layout/ReleaseRef";
import { Ref } from "../../src/layout/Text";
import { walked } from "./answers";
import { render } from "./render";

function n(source: string): ServerNumber {
  const [found] = decode(`[${source}]`) as readonly ServerNumber[];
  if (found === undefined) {
    throw new Error("no number");
  }
  return found;
}

const LONG = "\u202e<b>x</b>".repeat(400);
const text = (): ServerText => boxText(LONG);

const caveat = walked("Caveat", JSON.stringify({ code: "onco.X", severity: "block", message: [{ text: "w " }, { data: LONG }], affects: [`/${LONG}`] })) as BoxedCaveat;
const refusals = (walked("Refusals", JSON.stringify({ refusals: [{ code: "LIMIT_EXCEEDED", path: `/${LONG}`, message: [{ data: LONG }], alternatives: [{ data: LONG }], limit: { name: "max_x", max: 3 } }] })) as { readonly refusals: Refused["refusals"] }).refusals;

const COMPONENTS: readonly (readonly [string, () => ReactElement])[] = [
  ["Caveats", () => <Caveats caveats={[caveat]} />],
  ["Inspection", () => <Inspection caveats={[caveat]}>{<p>out</p>}</Inspection>],
  ["Refusals", () => <Refusals refused={{ kind: "refused", status: 429, refusals, retryAfter: 5 }} />],
  ["Figure", () => <Figure value={null} notEstimable={boxMap([[boxText("/estimate"), text()]])} at="/estimate" />],
  ["Count", () => <Count value={null} />],
  ["Num", () => <Num value={n("1.10")} />],
  ["List", () => <List hit={{ domain_tags: Array.from({ length: 70 }, () => ({ data: text() })), facets: boxMap([]) }} at="domain_tags" />],
  ["ServerList", () => <ServerList members={[text()]} each={(one) => <Ref value={one} />} leftOut={n("2")} />],
  ["Disclosure", () => <Disclosure value={{ min_cell_count: n("5") }} />],
  ["ReleaseRef", () => <ReleaseRef value={{ dataset: text(), label: boxText("draft"), manifest: text(), status: boxText("draft") }} />],
];

const FORMS: readonly Form[] = ["cut", "whole", "flat"];

describe("the shared components set their own forms", () => {
  // Four renders of up to 70 members: 5 s on a CI runner is not enough under the default timeout.
  it.each(COMPONENTS)("%s shows the same under each ancestor form", { timeout: 60_000 }, async (_name, make) => {
    const alone = await render(make());
    const expected = alone.container.innerHTML;
    alone.unmount();
    expect(expected).not.toBe("");
    for (const form of FORMS) {
      const under = await render(<FORM value={form}>{make()}</FORM>);
      expect(under.container.innerHTML, form).toBe(expected);
      under.unmount();
    }
  });
});
