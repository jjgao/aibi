/** The end-to-end harness (M5.1c-1c gives it its flows). It is in the end-to-end build alone;
 * `scripts/check-bundle.mjs` looks for `HARNESS_MARKER` in the scripts of a build, and refuses a
 * production build that holds it and an end-to-end build that does not.
 *
 * The run-time oracle's flow (D419): its button, which has no words (the page's words stay the
 * marker), sends requests through the client to `/api` and `/operator`, answers and refusals,
 * and then writes how many bodies the decoder was given to its `data-oracle`; the matrix compares
 * that with the responses the page received. */
import { useState } from "react";

import { exchange, routes } from "../api";
import { decodedCount } from "../api/oracle";

export const HARNESS_MARKER = "aibi-e2e-harness-1b6f0c";

async function oracle(): Promise<number> {
  await exchange(routes.health());
  await exchange(routes.search_catalog(), { limit: 5 });
  await exchange(routes.list_analyses(), {});
  await exchange(routes.describe_dataset(), { dataset: "no_such_dataset" });
  await exchange(routes.datasets());
  return decodedCount();
}

export default function Harness() {
  const [count, setCount] = useState<number | null>(null);
  return (
    <p data-harness="">
      {HARNESS_MARKER}
      <button
        type="button"
        aria-label="oracle"
        data-oracle={count ?? ""}
        onClick={() => {
          void oracle().then(setCount);
        }}
      />
    </p>
  );
}
