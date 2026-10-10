// @ts-check
/**
 * How a run holds `guardInterrupts` (D423): the one function `global-setup.ts` calls, with what it
 * has (its directories, its servers as they are started, its own teardown), so that the wiring is a
 * thing `selftest.spec.ts` drives in a process of its own (`interrupt-child.mjs`), not a few lines
 * of a setup that starts five servers.
 */
import { guardInterrupts } from "./interrupt.mjs";

/**
 * @param {object} run
 * @param {string} run.scratch the directory of the servers' configurations and logs
 * @param {string} run.secrets the directory that holds the curator token
 * @param {import("node:child_process").ChildProcess[]} run.children the servers, as they are started (read live)
 * @param {(keep?: boolean) => Promise<void>} run.teardown stops the servers and removes what a normal end removes
 * @param {() => boolean} run.scanning whether the scan reporter reads the token's directory after the teardown
 * @param {() => boolean} run.keepAsked whether the scratch directory is kept on request (`AIBI_E2E_KEEP`)
 */
export function guardRun({ scratch, secrets, children, teardown, scanning, keepAsked }) {
  let keepScratch = false;
  const guard = guardInterrupts({
    secrets,
    scratch,
    running: () => children.filter((child) => child.exitCode === null && child.signalCode === null),
    teardown: () => teardown(),
    keepScratch: () => keepScratch || keepAsked(),
  });
  return {
    /** Whether an interrupt came: no server is started then. */
    closing: guard.closing,
    /** A failed start: the servers stop, the scratch directory stays for its `serve.log` (unless an
     * interrupt is why), and the guard stays while the reporter has the token's directory. */
    async failed() {
      keepScratch = !guard.closing();
      await teardown(keepScratch);
      guard.releaseWhenScanned(scanning());
    },
    /** The run's end (the global teardown): the servers stop; the guard stays while the reporter has
     * the token's directory, and goes at once without one. */
    async finish() {
      await teardown();
      guard.releaseWhenScanned(scanning());
    },
  };
}
