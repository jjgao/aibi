// @ts-check
/**
 * What an interrupted run leaves behind, and the guard against it (D423): the per-run directory that
 * holds the curator token (mode 0600), the directory of the servers' configurations and logs, and
 * the servers themselves. `guardInterrupts` is the one place that decides; `global-setup.ts` uses it
 * and `selftest.spec.ts` drives it in a child process of its own (`interrupt-child.mjs`).
 *
 * - A SIGTERM or a SIGHUP, which Playwright's main process does not handle and which would kill it
 *   with no `exit` event at all, stops the servers, removes both directories and exits (128 plus the
 *   signal's number). It is `process.on`, not `once`, and a signal that comes while the first is
 *   being handled is ignored: the cleanup of a slow server is not cut by a second signal.
 * - Whatever ends the process otherwise (a SIGINT, which is Playwright's: it ends the run, the
 *   reporter scans and removes, then `exit`; a forced exit; a second Ctrl-C), the `exit` event kills
 *   a server still alive and removes the directories, a failed start's scratch directory aside (its
 *   `serve.log` is named by the error).
 * - `release()` removes every listener: once the run's own teardown has the servers, nothing of the
 *   guard stays (watch and UI modes set up and tear down again and again).
 */
import { rmSync } from "node:fs";
import os from "node:os";

/**
 * @typedef {object} Held
 * @property {string} secrets the directory that holds the curator token
 * @property {string} scratch the directory of the servers' configurations and logs
 * @property {() => import("node:child_process").ChildProcess[]} running the servers still alive
 * @property {() => Promise<void>} teardown stops the servers and removes what a normal end removes
 * @property {() => boolean} keepScratch whether the scratch directory stays (a failed start's, or asked for)
 */

/**
 * @param {Held} held
 * @returns {{ closing: () => boolean, release: () => void }}
 */
export function guardInterrupts(held) {
  let closing = false;
  /** @param {string} directory */
  const remove = (directory) => {
    rmSync(directory, { recursive: true, force: true });
  };
  const lastResort = () => {
    for (const child of held.running()) {
      child.kill("SIGKILL");
    }
    if (!held.keepScratch()) {
      remove(held.scratch);
    }
    remove(held.secrets);
  };
  /** @param {"SIGTERM" | "SIGHUP"} signal */
  const interrupted = (signal) => {
    if (closing) {
      return;
    }
    closing = true;
    remove(held.secrets);
    void (async () => {
      while (held.running().length > 0) {
        await held.teardown();
      }
      await held.teardown();
    })().finally(() => {
      process.exit(128 + os.constants.signals[signal]);
    });
  };
  const onTerm = () => {
    interrupted("SIGTERM");
  };
  const onHangup = () => {
    interrupted("SIGHUP");
  };
  process.on("exit", lastResort);
  process.on("SIGTERM", onTerm);
  process.on("SIGHUP", onHangup);
  return {
    closing: () => closing,
    release() {
      process.off("exit", lastResort);
      process.off("SIGTERM", onTerm);
      process.off("SIGHUP", onHangup);
    },
  };
}
