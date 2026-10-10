/**
 * The report's scan (D423, b2 of the plan's review): after every other reporter has written its
 * files, this one reads `playwright-report/` and `test-results/` (what CI uploads, and what a
 * failure leaves; the HTML report's data is a zip archive embedded as base64, which is read
 * unzipped) for the curator token, every session handle and CSRF token the tests saw
 * (`servers.ts`'s `remember`), and any token's or handle's shape, and fails the run if one is
 * there, naming the file and how many it holds, never the secret. It reads what the run printed too
 * (m5): the stdout and stderr of the workers, and every error and step error the other reporters
 * print (Playwright's API-call errors carry the request's headers, the token among them), joined
 * so that a secret split across two chunks is found. It then removes the directory that held the
 * token (the global teardown leaves it for this scan while this reporter runs).
 * Playwright 1.63 titles a `fill`, `type` or `insertText` step with the value typed, which is why
 * the matrix enters the token with `locator.evaluate`; this scan is what holds that.
 */
import { readdirSync, readFileSync, rmSync, statSync } from "node:fs";
import path from "node:path";
import { inflateRawSync } from "node:zlib";

import type { FullResult, Reporter, TestCase, TestError, TestResult, TestStep } from "@playwright/test/reporter";

import { runPendingReleases } from "./interrupt.mjs";
import { SCANNING_VARIABLE, SERVERS_VARIABLE, type Servers } from "./servers";

const WEB = path.join(import.meta.dirname, "..");
const SCANNED = ["playwright-report", "test-results"];
const SHAPES = [/(?<![A-Za-z0-9_-])aibi_[A-Za-z0-9_-]{43}(?![A-Za-z0-9_-])/u, /(?<![A-Za-z0-9_-])ses_[A-Za-z0-9_-]{43}(?![A-Za-z0-9_-])/u];

function files(directory: string): string[] {
  let names: string[];
  try {
    names = readdirSync(directory);
  } catch {
    return [];
  }
  return names.flatMap((name) => {
    const full = path.join(directory, name);
    return statSync(full).isDirectory() ? files(full) : [full];
  });
}

/** The entries of a zip archive, by its central directory (stored or deflated alone; anything
 * else is given as it is, so that a secret in it is still found if it is plain). */
export function unzipped(zip: Buffer): Buffer[] {
  let end = -1;
  for (let at = zip.length - 22; at >= 0 && at >= zip.length - 22 - 65_535; at -= 1) {
    if (zip.readUInt32LE(at) === 0x06054b50) {
      end = at;
      break;
    }
  }
  if (end === -1) {
    return [zip];
  }
  const entries: Buffer[] = [];
  const count = zip.readUInt16LE(end + 10);
  let at = zip.readUInt32LE(end + 16);
  for (let index = 0; index < count && zip.readUInt32LE(at) === 0x02014b50; index += 1) {
    const method = zip.readUInt16LE(at + 10);
    const size = zip.readUInt32LE(at + 20);
    const local = zip.readUInt32LE(at + 42);
    const start = local + 30 + zip.readUInt16LE(local + 26) + zip.readUInt16LE(local + 28);
    const data = zip.subarray(start, start + size);
    entries.push(method === 8 ? inflateRawSync(data) : data);
    at += 46 + zip.readUInt16LE(at + 28) + zip.readUInt16LE(at + 30) + zip.readUInt16LE(at + 32);
  }
  return entries;
}

/** A file's texts: itself, and every zip archive embedded in it as a base64 data URL (the HTML
 * report keeps its data so), unzipped, and every `.zip` file, unzipped. */
export function texts(file: string): string[] {
  const bytes = readFileSync(file);
  const found = [bytes.toString("latin1")];
  if (file.endsWith(".zip")) {
    found.push(...unzipped(bytes).map((entry) => entry.toString("latin1")));
  }
  for (const embedded of found[0]?.matchAll(/data:application\/zip;base64,([A-Za-z0-9+/=]+)/gu) ?? []) {
    found.push(...unzipped(Buffer.from(embedded[1] ?? "", "base64")).map((entry) => entry.toString("latin1")));
  }
  return found;
}

/** The files under the scanned directories that hold a secret or a secret's shape, each with how
 * many of the secrets it holds and how many shapes. */
export function findings(root: string, secrets: readonly string[]): string[] {
  const found: string[] = [];
  for (const directory of SCANNED) {
    for (const file of files(path.join(root, directory))) {
      const read = texts(file);
      const held = secrets.filter((secret) => read.some((text) => text.includes(secret))).length;
      const shapes = SHAPES.filter((shape) => read.some((text) => shape.test(text))).length;
      if (held + shapes > 0) {
        found.push(`${path.relative(root, file)}: ${String(held)} secret(s), ${String(shapes)} shape(s)`);
      }
    }
  }
  return found;
}

/** How many of `secrets` and of the shapes the texts hold, as a finding for `place`, or none: the
 * texts are joined (a secret split across two chunks is found) and the secrets are counted, never
 * shown. */
export function outputFindings(place: string, chunks: readonly string[], secrets: readonly string[]): string[] {
  const joined = chunks.join("");
  const held = secrets.filter((secret) => joined.includes(secret)).length;
  const shapes = SHAPES.filter((shape) => shape.test(joined)).length;
  return held + shapes > 0 ? [`${place}: ${String(held)} secret(s), ${String(shapes)} shape(s)`] : [];
}

/** The deepest chain of causes the scan follows. */
const CAUSE_DEPTH = 64;

/** The texts of an error: its message, its stack, the code snippet beside it, the value thrown, and
 * those of its `cause`, and the cause's cause, to a bounded depth (64: Playwright prints a chain without a bound, and a cycle stops it), a cycle read once (the list
 * reporter prints `[cause]: ...`, and a cause holds whatever the error it wraps held).
 * @param seen the errors already read */
export function errorTexts(error: TestError, seen: Set<TestError> = new Set(), depth = 0): string[] {
  if (seen.has(error) || depth > CAUSE_DEPTH) {
    return [];
  }
  seen.add(error);
  const own = [error.message ?? "", error.stack ?? "", error.snippet ?? "", error.value ?? "", "\n"];
  return error.cause === undefined ? own : [...own, ...errorTexts(error.cause, seen, depth + 1)];
}

/** What a scan is given when a test drives it: the secrets to look for (else the run's, read from the
 * files `global-setup.ts` made, which the scan removes), where the report is (else the package's)
 * and where it writes. */
export interface ScanOptions {
  readonly secrets?: readonly string[];
  readonly root?: string;
  readonly write?: (text: string) => void;
}

export default class ScanReporter implements Reporter {
  /** What the run printed or reported as an error: the workers' stdout and stderr, test errors,
   * step errors and the runner's own. */
  private readonly printed: string[] = [];

  private readonly options: ScanOptions;

  constructor(options?: ScanOptions) {
    this.options = options ?? {};
    process.env[SCANNING_VARIABLE] = "1";
  }

  onStdOut(chunk: string | Buffer): void {
    this.printed.push(chunk.toString());
  }

  onStdErr(chunk: string | Buffer): void {
    this.printed.push(chunk.toString());
  }

  onStepEnd(_test: TestCase, _result: TestResult, step: TestStep): void {
    if (step.error !== undefined) {
      this.printed.push(...errorTexts(step.error));
    }
  }

  onTestEnd(_test: TestCase, result: TestResult): void {
    for (const error of result.errors) {
      this.printed.push(...errorTexts(error));
    }
  }

  onError(error: TestError): void {
    this.printed.push(...errorTexts(error));
  }

  printsToStdio(): boolean {
    return false;
  }

  /** A status that fails the run if the report holds a secret (the base type reads it from a
   * promise). */
  onEnd(result: FullResult): Promise<{ status?: FullResult["status"] } | undefined> {
    return Promise.resolve(this.scan(result));
  }

  private scan(result: FullResult): { status?: FullResult["status"] } | undefined {
    try {
      return this.scanned(result);
    } finally {
      // The token's directory is gone (`readSecrets`): the interrupt guard, kept for this window, goes.
      runPendingReleases();
    }
  }

  private scanned(result: FullResult): { status?: FullResult["status"] } | undefined {
    const write = this.options.write ?? ((text: string) => process.stderr.write(text));
    const secrets = this.options.secrets === undefined ? readSecrets() : [...this.options.secrets];
    if (secrets === undefined) {
      return undefined;
    }
    const found = [...findings(this.options.root ?? WEB, secrets), ...outputFindings("the run's stdout, stderr and errors", this.printed, secrets)];
    if (found.length > 0) {
      write(`The report holds a secret (D423):\n${found.join("\n")}\n`);
      return { status: "failed" };
    }
    write(`The report's scan: ${String(secrets.length)} secrets and the token's and handle's shapes, in none of ${SCANNED.join(", ")}, nor in the run's output (D423).\n`);
    return result.status === "passed" ? undefined : { status: result.status };
  }
}

/** The secrets of the run: the token and every one the tests saw, read from the files
 * `global-setup.ts` made, whose directory is then removed; `undefined` if no server was started. */
function readSecrets(): string[] | undefined {
  const given = process.env[SERVERS_VARIABLE];
  if (given === undefined) {
    return undefined;
  }
  const { tokenFile, secretsFile } = JSON.parse(given) as Servers;
  try {
    return [readFileSync(tokenFile, "utf8"), ...readFileSync(secretsFile, "utf8").split("\n")].filter((secret) => secret.length >= 16);
  } finally {
    rmSync(path.dirname(tokenFile), { recursive: true, force: true });
  }
}
