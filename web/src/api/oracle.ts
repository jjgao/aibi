/**
 * The run-time oracle (D419): how many bodies the decoder was given since the page loaded, which
 * the end-to-end matrix compares with the responses the page received, and the requests the
 * harness's oracle button sends to make them (`exercise`: answers and refusals, to `/api` and
 * `/operator`, the three public tools through the typed answers' seam, D426, and the health check
 * and the operator's datasets, which no typed function reads, through `exchange` here). It is the
 * end-to-end harness's alone (`src/harness/`): the index does not export it, the lint lets no other
 * module import this one, and the build's gate (`plugins/gate.ts`) holds that nothing but the
 * harness imports it, so a production build holds it nowhere.
 *
 * `corpus()` gives the injection corpus (D428) as boxes, made by the typed answers' walk
 * (`walkComponent`, the tests' door, over a list of segments): the policy matrix renders each
 * through the visible form's components and asserts that nothing violates the documents' policy.
 * (`javascript:` is the unit corpus's alone: the lint refuses the string in app code.)
 */
import { exchange } from "./client";
import { decodedCount } from "./decode";
import * as generated from "./generated/routes";
import { type BoxedSegment, describeDataset, listAnalyses, searchCatalog, walkComponent } from "./typed";

export { decodedCount } from "./decode";

/** The oracle's requests, in order; then the decoder's count. */
export async function exercise(): Promise<number> {
  await exchange(generated.health());
  await searchCatalog({ limit: 5 });
  await listAnalyses({});
  await describeDataset({ dataset: "no_such_dataset" });
  await exchange(generated.datasets());
  return decodedCount();
}

/** The corpus's texts: markup, a bidi override, a lone surrogate, the visible form's own end tag, a
 * data token spelled like the server's words, what D312 leaves raw, the joiners it keeps (a ZWJ sequence), controls and a long text. */
const CORPUS: readonly string[] = Object.freeze([
  '<img src=x onerror="alert(1)">',
  "\u202etxt.exe",
  "a\ud800b",
  "</bdi><b>bold</b>",
  "The page could not reach the server; check the connection and try again.",
  "\u034f\u3164\ufe0f\u2800\u00a0",
  "a\u200cb \u{1f468}\u200d\u{1f469}\u200d\u{1f467}",
  "tab\tline\r\nfeed\u0007\u0085\u2028",
  "x".repeat(5000),
]);

/** Whether the walked value is the list of segments it was walked as. */
function isSegments(value: unknown): value is readonly BoxedSegment[] {
  return Array.isArray(value);
}

/** The corpus, each text as a data segment and as a text segment, boxed by the walk. */
export function corpus(): readonly BoxedSegment[] {
  const walked = walkComponent(
    { t: "arr", i: { t: "seg" } },
    CORPUS.flatMap((text) => [{ data: text }, { text }]),
  );
  return walked.ok && isSegments(walked.value) ? walked.value : [];
}
