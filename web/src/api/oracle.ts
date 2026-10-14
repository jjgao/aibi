/**
 * The run-time oracle (D419): how many bodies the decoder was given since the page loaded, which
 * the end-to-end matrix compares with the responses the page received, and the requests the
 * harness's oracle button sends to make them (`exercise`: answers and refusals, to `/api` and
 * `/operator`, the three public tools through the typed answers' seam, D426, and the health check
 * and the operator's datasets, which no typed function reads, through `exchange` here). It is the
 * end-to-end harness's alone (`src/harness/`): the index does not export it, the lint lets no other
 * module import this one, and the build's gate (`plugins/gate.ts`) holds that nothing but the
 * harness imports it, so a production build holds it nowhere.
 */
import { exchange } from "./client";
import { decodedCount } from "./decode";
import * as generated from "./generated/routes";
import { describeDataset, listAnalyses, searchCatalog } from "./typed";

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
