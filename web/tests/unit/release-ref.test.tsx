/**
 * `ReleaseRef` (D430; D427's R27): `@<label>` only for a published release with a numbered label, `@draft` otherwise (a draft, and
 * fail closed), and a disagreement between the label and the status (or
 * a word the page does not know) shown as a draft with the disagreement said, never as published.
 */
import { describe, expect, it } from "vitest";

import { boxText } from "../../src/api/box";
import { DISAGREE, type Release, ReleaseRef } from "../../src/layout/ReleaseRef";
import { walked } from "./answers";
import { leafSweep } from "./injection";
import { render } from "./render";
import { componentOf, ORCHARD } from "./typed-support";

const MANIFEST = `sha256:${"ab".repeat(32)}`;

const shownOf = async (release: Release): Promise<{ readonly label: string; readonly text: string }> => {
  const shown = await render(<ReleaseRef value={release} />);
  const found = { label: shown.container.querySelector(".label")?.textContent ?? "", text: shown.container.textContent };
  shown.unmount();
  return found;
};

/** Every release of a walked answer, at any depth (`release`, and each of `releases`). */
function releasesOf(value: unknown): Release[] {
  if (Array.isArray(value)) {
    return value.flatMap((member: unknown) => releasesOf(member));
  }
  if (typeof value !== "object" || value === null) {
    return [];
  }
  return Object.entries(value).flatMap(([key, member]: [string, unknown]) =>
    key === "release" && typeof member === "object" && member !== null && "manifest" in member ? [member as Release] : key === "releases" && Array.isArray(member) ? (member as Release[]) : releasesOf(member),
  );
}

describe("ReleaseRef", () => {
  it("R27: shows @<label> only for a published release with a numbered label, and @draft otherwise", async () => {
    expect.hasAssertions();
    const draft = walked("ReleaseRef", JSON.stringify({ dataset: "orchard", label: "draft", manifest: MANIFEST, status: "draft" })) as Release;
    expect(await shownOf(draft)).toEqual({ label: "@draft", text: `orchard@draft · manifest ${MANIFEST}` });
    const published = walked("ReleaseRef", JSON.stringify({ dataset: "orchard", label: 3, manifest: MANIFEST, status: "published" })) as Release;
    expect(await shownOf(published)).toEqual({ label: "@3", text: `orchard@3 · manifest ${MANIFEST}` });
    const out = walked("ReleaseOut", JSON.stringify({ label: 12, manifest: MANIFEST, status: "published" })) as Release;
    expect((await shownOf(out)).label).toBe("@12");
    for (const [label, status] of [
      [boxText("draft"), boxText("published")],
      [(walked("ReleaseOut", JSON.stringify({ label: 4, manifest: MANIFEST, status: "draft" })) as Release).label, boxText("draft")],
      [boxText("draft"), boxText("withdrawn")],
      [boxText("4"), boxText("published")],
    ] as const) {
      const odd = await shownOf({ label, manifest: boxText(MANIFEST), status });
      expect(odd.label).toBe("@draft");
      expect(odd.text).toContain(DISAGREE);
    }
    const releases = ORCHARD.filter((answer) => answer.status === 200).flatMap((answer) => releasesOf(walked(componentOf(answer), answer.body)));
    expect(releases.length).toBeGreaterThanOrEqual(3);
    for (const release of releases) {
      const { label, text } = await shownOf(release);
      expect(label).toMatch(/^@(draft|[1-9][0-9]*)$/u);
      expect(text).not.toContain(DISAGREE);
    }
  });

  // Each sweep renders every ServerText position (the dataset, the label's word, the status, the manifest) under every
  // hostile row; they grow with the corpus, so they have the shared forms' timeout.
  it.each([
    ["a published release", { dataset: "orchard", label: 3, manifest: MANIFEST, status: "published" }],
    ["a draft", { dataset: "orchard", label: "draft", manifest: MANIFEST, status: "draft" }],
  ])("shows a hostile text in every position of %s as text alone, by construction", { timeout: 60_000 }, async (_name, json) => {
    const release = walked("ReleaseRef", JSON.stringify(json)) as Release;
    const found = await leafSweep(release, (value) => <ReleaseRef value={value} />, [], ["release", "label", "label draft", "disagree"]);
    expect(found.problems).toEqual([]);
    expect(found.leaves).toBeGreaterThan(0);
  });
});
