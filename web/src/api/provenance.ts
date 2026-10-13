/**
 * Which response positions feed the four request ids (D425), the recording of their nodes'
 * provenance as an answer is decoded, and `idAt`, **the one function that reads a box's text** (MA3
 * of round 3). `FEEDS` is `classes.json`'s `from` for the four positions
 * (a gate holds the two equal, and the server's core test holds `from` to the document): each
 * names the operation whose 2xx answer holds the node, the node's path from the answer's root
 * (`labels[]`: each element of the root's `labels`), and the member of the node that holds the
 * number (`read`).
 *
 * `recorder` gives `decode` the hook it calls before it freezes what it made (`client.ts`'s
 * `received`, for a 2xx answer that `send` read alone: an answer of `exchange`, which the public
 * pages read, or of any other status is recorded nowhere): the nodes at the feeds' paths of that
 * operation, and only those, are stamped with their `Origin` (the operation, the path, the
 * dataset the request's route named, the client's era). Recording only there keeps the cost off
 * every other answer and off every other node (the queue's notes, a field's counts) and shrinks
 * what a mistake could stamp. `recorder` is `client.ts`'s alone (the lint's table of the
 * internals of `src/api/`).
 *
 * **`idAt`: the one reader of a box's text.** The text of a box is read by `decode.ts` and here,
 * and nowhere else: `textOf`, `requestId` (the grammar's one maker of a `RequestId`) and `originOf`
 * are this module's (and, for `textOf`, `decode.ts`'s) by lint rows and the import-graph test, so
 * that no member of the operator client's closure, however added, can read a box's digits and hand
 * them out. `idAt` reads one only from a node the recorder stamped for the feed's operation and
 * path, in the caller's era, from the member the feed names: an id, never a count. `idAt` takes a
 * position's name (`FeedName`) and looks its feed up in `FEEDS`, which is frozen, so no caller names
 * the member read. `curator.ts`'s four flows are the only importers of `idAt`, and each is given a
 * decoded node and nothing else. This module's exports are held exactly (`FEEDS`, `idAt`, `nodesAt`
 * and `recorder`, and their types), and `textOf`, `requestId` and `originOf` are each called once,
 * inside `idAt` (`request-half.test.ts` (h)): no other function of it reads a box.
 */
import { isServerNumber, textOf } from "./box";
import { type RequestId, requestId } from "./ids";
import type { JsonOut } from "./json";
import { originOf, stamp } from "./origin";
import type { OperationId } from "./route";

/** A response position that feeds a request id. */
export interface Feed {
  readonly operation: OperationId;
  readonly path: string;
  readonly read: string;
}

/** The four request ids' feeds, by position (`classes.json`'s `from`), frozen through and through:
 * `readonly` is not held through an alias (m14 of round 4), so a feed that a body could write
 * through one (`loose.read = "value"`) is a feed that cannot be written. `idAt` takes a position's
 * name, never a feed, and looks the feed up here. */
export const FEEDS = Object.freeze({
  "Accept.proposal": Object.freeze({ operation: "queue", path: "proposals[]", read: "id" }),
  "TakeOver.session": Object.freeze({ operation: "dataset_state", path: "session", read: "session" }),
  "path:proposal": Object.freeze({ operation: "queue", path: "proposals[]", read: "id" }),
  "query:release": Object.freeze({ operation: "dataset_state", path: "labels[]", read: "label" }),
} as const satisfies Readonly<Record<string, Feed>>);

/** A position of `FEEDS`, by its name: what `idAt` is given in place of a feed. */
export type FeedName = keyof typeof FEEDS;

/** What `send` knows of a request beside its answer: the dataset its route named and the client's
 * era (`Origin`). */
export interface Tag {
  readonly dataset: string;
  readonly era: object;
}

type JsonRecord = { readonly [member: string]: JsonOut };

function isRecord(value: JsonOut | undefined): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value) && !isServerNumber(value);
}

/** The nodes at a path from a root: each step a member the node owns (`name`), or each element of
 * one (`name[]`); the nodes at the end that are objects (not a list, not a box). Rooted: a node of
 * the same name deeper down is not at the path. */
export function nodesAt(root: JsonOut, path: string): JsonRecord[] {
  let current: JsonOut[] = [root];
  for (const step of path.split(".")) {
    const each = step.endsWith("[]");
    const name = each ? step.slice(0, -2) : step;
    const next: JsonOut[] = [];
    for (const node of current) {
      if (!isRecord(node) || !Object.hasOwn(node, name)) {
        continue;
      }
      const found = node[name];
      if (!each) {
        next.push(found ?? null);
      } else if (Array.isArray(found)) {
        const items: readonly JsonOut[] = found;
        next.push(...items);
      }
    }
    current = next;
  }
  return current.filter(isRecord);
}

/** The paths of an operation's feeds, each once. */
function pathsOf(operation: OperationId): string[] {
  const feeds: readonly Feed[] = Object.values(FEEDS);
  return [...new Set(feeds.filter((feed) => feed.operation === operation).map((feed) => feed.path))];
}

/** The hook that stamps an answer's nodes at the paths its operation feeds, `null` for an
 * operation that feeds nothing (no hook, no cost). */
export function recorder(operation: OperationId, tag: Tag): ((root: JsonOut) => void) | null {
  const paths = pathsOf(operation);
  if (paths.length === 0) {
    return null;
  }
  return (root) => {
    for (const path of paths) {
      for (const node of nodesAt(root, path)) {
        stamp(node, { operation, path, dataset: tag.dataset, era: tag.era });
      }
    }
  };
}

/** Why `idAt` refused a node (an `Invalid` of the operator client): `node`, a value the client did
 * not decode and stamp (a copy of a node, a literal, a node `exchange` decoded, a box); `origin`, a
 * node of another operation or path than the feed's; `stale`, a node of another era; `member`, a
 * node without a box at the member the feed reads; `id`, a box whose text is no request id. */
export type Refusal = "node" | "origin" | "stale" | "member" | "id";

/** What a flow sends back from a node: the dataset its origin names, the request id read from it,
 * and the node. */
export interface Fed {
  readonly dataset: string;
  readonly id: RequestId;
  readonly node: JsonRecord;
}

/** The request id a decoded node holds at a position of `FEEDS`, in an era: the only reader of a
 * box's text. It takes the position's *name* and looks the feed up itself (m14 of round 4: given a
 * feed, a caller could name the member read, and so read a box that is no id), so the node must be
 * one the recorder stamped (`originOf`), for that feed's operation and path, in this era, and own
 * the member the feed reads, holding a box whose text is a request id (`requestId`); anything else
 * is refused, and nothing of a box is given but the id. */
export function idAt(node: JsonOut, position: FeedName, era: object): Fed | Refusal {
  const feed: Feed = FEEDS[position];
  const origin = originOf(node);
  if (origin === null || !isRecord(node)) {
    return "node";
  }
  if (origin.operation !== feed.operation || origin.path !== feed.path) {
    return "origin";
  }
  if (origin.era !== era) {
    return "stale";
  }
  const found = Object.hasOwn(node, feed.read) ? node[feed.read] : undefined;
  if (!isServerNumber(found)) {
    return "member";
  }
  const id = requestId(textOf(found));
  return id === null ? "id" : { dataset: origin.dataset, id, node };
}
