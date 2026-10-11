/** The end-to-end harness (a stub: M5.1c-1c gives it its flows). It is in the end-to-end build
 * alone; `scripts/check-bundle.mjs` looks for `HARNESS_MARKER` in the scripts of a build, and
 * refuses a production build that holds it and an end-to-end build that does not. */
export const HARNESS_MARKER = "aibi-e2e-harness-1b6f0c";

export default function Harness() {
  return <p data-harness="">{HARNESS_MARKER}</p>;
}
