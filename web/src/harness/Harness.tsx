/** The end-to-end harness. It is in the end-to-end build alone; `scripts/check-bundle.mjs` looks
 * for `HARNESS_MARKER` in the scripts of a build, and refuses a production build that holds it
 * and an end-to-end build that does not. Its controls have no words (the page's words stay the
 * marker).
 *
 * - The run-time oracle's flow (D419): its button sends requests through the client to `/api` and
 *   `/operator`, answers and refusals, and then writes how many bodies the decoder was given to
 *   its `data-oracle`; the matrix compares that with the responses the page received.
 * - The operator's flows (D423): a button for each operation of the page's operator client (the
 *   one the shell unlocks), over the dataset, manifest and descriptor its fields hold; the outcome
 *   is written to `output`'s data attributes: its kind, status, refusal codes, reason, what was
 *   invalid, whether the client holds a handle for the dataset, and the decoder's count. Never the
 *   token, a handle or the CSRF token, which the client gives no one.
 */
import { useRef, useState } from "react";

import { exchange, routes } from "../api";
import { decodedCount } from "../api/oracle";
import { operator, type Outcome } from "../api/operator";

export const HARNESS_MARKER = "aibi-e2e-harness-1b6f0c";

async function oracle(): Promise<number> {
  await exchange(routes.health());
  await exchange(routes.search_catalog(), { limit: 5 });
  await exchange(routes.list_analyses(), {});
  await exchange(routes.describe_dataset(), { dataset: "no_such_dataset" });
  await exchange(routes.datasets());
  return decodedCount();
}

/** What the fields hold when an operation runs. */
interface Fields {
  readonly dataset: string;
  readonly manifest: string;
  readonly descriptor: string;
}

/** The operations the harness drives, by the name of their button. */
const OPERATIONS: Readonly<Record<string, (fields: Fields) => Promise<Outcome>>> = {
  datasets: () => operator.datasets(),
  dataset: ({ dataset }) => operator.dataset(dataset),
  queue: ({ dataset }) => operator.queue(dataset),
  open: ({ dataset }) => operator.open(dataset),
  confirm: ({ dataset, descriptor }) => operator.change(dataset, [{ op: "confirm", descriptor }]),
  remove_descriptor: ({ dataset, descriptor }) => operator.change(dataset, [{ op: "remove_descriptor", descriptor }]),
  publish: ({ dataset }) => operator.publish(dataset),
  discard: ({ dataset }) => operator.discard(dataset),
  withdraw: ({ dataset, manifest }) => operator.withdraw(dataset, manifest),
};

interface Shown {
  readonly outcome: Outcome;
  readonly holds: boolean;
  readonly decoded: number;
  readonly runs: number;
}

export default function Harness() {
  const [count, setCount] = useState<number | null>(null);
  const [shown, setShown] = useState<Shown | null>(null);
  const runs = useRef(0);
  const dataset = useRef<HTMLInputElement>(null);
  const manifest = useRef<HTMLInputElement>(null);
  const descriptor = useRef<HTMLInputElement>(null);
  const outcome = shown?.outcome;
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
      <input ref={dataset} aria-label="dataset" type="text" autoComplete="off" />
      <input ref={manifest} aria-label="manifest" type="text" autoComplete="off" />
      <input ref={descriptor} aria-label="descriptor" type="text" autoComplete="off" />
      {Object.entries(OPERATIONS).map(([name, run]) => (
        <button
          key={name}
          type="button"
          aria-label={name}
          onClick={() => {
            const fields = { dataset: dataset.current?.value ?? "", manifest: manifest.current?.value ?? "", descriptor: descriptor.current?.value ?? "" };
            void run(fields).then((found) => {
              runs.current += 1;
              setShown({ outcome: found, holds: operator.holds(fields.dataset), decoded: decodedCount(), runs: runs.current });
            });
          }}
        />
      ))}
      <output
        aria-label="outcome"
        data-runs={shown?.runs ?? 0}
        data-kind={outcome?.kind ?? ""}
        data-status={outcome !== undefined && "status" in outcome ? outcome.status : ""}
        data-codes={outcome?.kind === "refused" ? outcome.codes.join(" ") : ""}
        data-reason={outcome !== undefined && "reason" in outcome ? outcome.reason : ""}
        data-what={outcome?.kind === "invalid" ? outcome.what : ""}
        data-holds={shown === null ? "" : shown.holds ? "yes" : "no"}
        data-decoded={shown?.decoded ?? ""}
      />
    </p>
  );
}
