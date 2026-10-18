/**
 * The provenance panel and the parameters (D435; D427's R26a and R28a; §8.1, P1): the live answers
 * (a), computed for its request, and (b), its re-run served from the result cache, through the real
 * seam.
 */
import { describe, expect, it, vi } from "vitest";

import { PROVENANCE_WORDS } from "../../src/result/Provenance";
import { type Json, LIVE, list, liveBody, object, RUNS, shownOf, words } from "./result-support";

// This file's cells render whole results under the suite's load (D435): its own time per cell, as a
// row's test takes no options of its own (D427); the unit project's default stays 5 s.
vi.setConfig({ testTimeout: 20_000 });

const panelOf = async (body: Json): Promise<string[]> => [...(await shownOf(body)).querySelectorAll('[aria-label="provenance"]')].map(words);

describe("the provenance panel (D435)", () => {
  it("R26a: the panel shows the derivation id, the analysis and its version, the releases, the packs, the semantics version, the engine, the digest, the issuance and the leaves, every id whole; each position its cohort id", async () => {
    expect.hasAssertions();
    for (const [index, text] of RUNS) {
      const body = JSON.parse(text) as Json;
      const shown = await shownOf(body);
      const panels = [...shown.querySelectorAll('[aria-label="provenance"]')].map(words);
      const results = list(body["results"]).map(object);
      expect(panels).toHaveLength(results.length);
      for (const [at, result] of results.entries()) {
        const derivation = object(result["derivation"]);
        const analysis = object(derivation["analysis"]);
        const panel = panels[at] ?? "";
        expect(panel, String(index)).toContain(`Derivation ${String(derivation["id"])} : analysis ${String(analysis["id"])} version ${String(analysis["version"])}`);
        for (const release of list(derivation["releases"]).map(object)) {
          expect(panel).toContain(`manifest ${String(release["manifest"])}`);
        }
        const packs = Object.keys(object(derivation["packs"]));
        expect(panel).toContain(packs.length === 0 ? `Packs: ${PROVENANCE_WORDS.none}` : `Packs: ${packs[0] ?? ""}`);
        expect(panel).toContain(`Semantics version ${JSON.stringify(derivation["semantics_version"])} ; engine ${String(derivation["engine"])}`);
        expect(panel).toContain(`Digest ${String(result["digest"])}`);
        for (const [pointer, ids] of Object.entries(object(object(result["source"])["leaves"]))) {
          expect(panel).toContain(`${pointer} ${list(ids).map(String).join(" , ")}`);
        }
        expect(panel).toContain(PROVENANCE_WORDS.document);
        const positions = [...(shown.querySelectorAll('section[aria-label="result"]')[at]?.querySelectorAll('section[aria-label="position"]') ?? [])].map(words);
        for (const [position, cohort] of list(result["cohorts"]).map(object).entries()) {
          expect(positions[position]).toContain(`cohort ${String(cohort["id"])}`);
        }
      }
    }
  });

  it("R28a: a cache hit says so and names the issuance its values come from; a computed result says that; the unused parameters are reported once per answer", async () => {
    expect.hasAssertions();
    const computed = liveBody(LIVE.a);
    const cached = liveBody(LIVE.b);
    const issuance = (body: Json): Json => object(object(list(body["results"])[0])["issuance"]);
    expect([issuance(computed)["cache_hit"], issuance(cached)["cache_hit"], issuance(cached)["values_from"]]).toEqual([false, true, issuance(computed)["id"]]);
    expect((await panelOf(computed))[0]).toContain(`Issuance ${String(issuance(computed)["id"])} : ${PROVENANCE_WORDS.computed}`);
    expect((await panelOf(cached))[0]).toContain(`Issuance ${String(issuance(cached)["id"])} : ${PROVENANCE_WORDS.cached} ${String(issuance(computed)["id"])} .`);
    // The answer's parameters, once for an answer of two results; unused ones as data.
    const two = liveBody(LIVE.a);
    two["results"] = [...list(two["results"]), ...list(liveBody(LIVE.d)["results"])];
    two["params"] = { used: { columns: [] }, unused: [{ data: "zq_unused" }] };
    const shown = words(await shownOf(two));
    expect(shown.split("Parameters used:").length - 1).toBe(1);
    expect(shown).toContain(`Parameters used: ${PROVENANCE_WORDS.used} Parameters not used: zq_unused`);
    expect(words(await shownOf(computed))).toContain(`Parameters used: ${PROVENANCE_WORDS.none} Parameters not used: ${PROVENANCE_WORDS.none}`);
  });
});
