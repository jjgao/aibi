/**
 * `IdStatus` (D433; D427's R43; §7.6, §12.2): an id and its status in words of its own, one for
 * each status of `Explanation.status` (a validation's statuses are a part of them); a status the
 * page does not know is the server's text, marked.
 */
import { describe, expect, it } from "vitest";

import { boxText } from "../../src/api/box";
import { VOCABULARIES } from "../../src/api/generated/shapes";
import { IdStatus, NOT_KNOWN, STATUS_WORDS } from "../../src/builder/Ids";
import { render } from "./render";

/** The text an id and a status show. */
async function shown(status: string): Promise<string> {
  const found = await render(<IdStatus id={boxText("drv:abc")} status={boxText(status)} />);
  const text = found.container.textContent;
  found.unmount();
  return text;
}

describe("IdStatus", () => {
  it("R43: ids not yet issued are shown as such, and each id status has words of its own", async () => {
    expect.hasAssertions();
    const statuses: readonly string[] = VOCABULARIES["Explanation.status"];
    expect([...statuses].sort()).toEqual(["discarded", "erased", "issued", "not_issued", "unknown", "unknown_release", "withdrawn"]);
    expect(Object.keys(STATUS_WORDS).sort()).toEqual([...statuses].sort());
    const texts: string[] = [];
    for (const status of statuses) {
      texts.push(await shown(status));
    }
    expect(new Set(texts).size).toBe(statuses.length);
    expect(texts).toEqual([...statuses].map((status) => `Id drv:abc: ${STATUS_WORDS[status as keyof typeof STATUS_WORDS]}`));
    const exact: Record<string, string> = {
      issued: "Id drv:abc: issued",
      not_issued: "Id drv:abc: not issued yet",
      unknown: "Id drv:abc: unknown",
      withdrawn: "Id drv:abc: withdrawn",
      discarded: "Id drv:abc: discarded",
      unknown_release: "Id drv:abc: of an unknown release",
      erased: "Id drv:abc: erased",
    };
    for (const status of statuses) {
      expect(await shown(status), status).toBe(exact[status]);
    }
    const validation: readonly string[] = VOCABULARIES["CohortCheck.status"];
    expect(validation.every((status) => statuses.includes(status))).toBe(true);
  });

  it("R43: a status the page does not know is the server's text, marked, never one it knows", async () => {
    expect.hasAssertions();
    expect(NOT_KNOWN).toBe("(not known to this page)");
    expect(await shown("revoked")).toBe(`Id drv:abc: revoked ${NOT_KNOWN}`);
    expect(await shown("Issued")).toBe(`Id drv:abc: Issued ${NOT_KNOWN}`);
  });
});
