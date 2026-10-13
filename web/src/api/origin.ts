/**
 * The provenance of a decoded node (D425): where in which answer the client decoded it. A node the
 * operator's client may send a number back from (a dataset state's `session`, one of its
 * `labels[]`, one of a queue's `proposals[]`) is stamped with its `Origin` as it is decoded, before
 * it is frozen, and the four flows that send one (`curator.ts`'s `takeOver`, `rejectProposal`,
 * `acceptProposal`, `queueAt`) read it back and refuse a node without one, or with another.
 *
 * **A private-field brand, not a map.** The origin is held in an ES private field, `#origin`, that
 * a return-override class adds to the node itself (the base constructor returns its argument, so
 * the subclass's field lands on the node). No code outside this module can name the field (`#x in
 * y` outside the class body is a syntax error), so nothing reads or forges it, and a copy of a node
 * carries none: a spread, `Object.create(node)`, a `Proxy` around it, `structuredClone`, a JSON
 * round trip, a subclass, `Reflect.construct` and a hand-written literal are no stamped node. A
 * module `WeakMap` would hold the same, but a patched `WeakMap.prototype.get` forged an origin in a
 * probe (the reason the box's text is a private field too, D419); patching a builtin is deliberate
 * code, out of scope, and the lint refuses it.
 *
 * **Stamped before frozen.** `stamp` refuses a target that is not extensible (a frozen node), so
 * the order is a run-time invariant: `decode` stamps through its hook while it still holds every
 * object unfrozen, then freezes them all (a coming change of the language forbids adding a private
 * field to a non-extensible object; this order does not depend on it). A node is stamped once
 * (a second stamp throws, the engine's own refusal of a private field added twice).
 *
 * `stamp` and `originOf` are `provenance.ts`'s alone (the lint's table of the internals of
 * `src/api/`, the import-graph test): `idAt` reads a node's origin back; no other code does.
 */
import type { OperationId } from "./route";

/** Where a node was decoded: the operation and the path of its answer, the dataset the request's
 * route named, and the operator client's era (its epoch, an object a lock, a forget or an unlock
 * replaces: a node of an earlier epoch, or of another client, is of another era). Recorded for 2xx
 * answers alone. */
export interface Origin {
  readonly operation: OperationId;
  readonly path: string;
  readonly dataset: string;
  readonly era: object;
}

/** The words of a refused stamp: fixed. */
export const UNSTAMPABLE = "A node is stamped before it is frozen, and once (D425).";

let write: (target: object, origin: Origin) => void;
let read: (value: object) => Origin | null;

/** The base of the return override: its constructor returns its argument, so a subclass's fields
 * are added to that object. */
class Through {
  constructor(target: object) {
    return target;
  }
}

/** The stamp: `new Stamp(node, origin)` adds `#origin` to `node`. */
class Stamp extends Through {
  readonly #origin: Origin;

  private constructor(target: object, origin: Origin) {
    super(target);
    this.#origin = origin;
  }

  static {
    write = (target, origin) => {
      new Stamp(target, origin);
    };
    read = (value) => (#origin in value ? value.#origin : null);
  }
}

Object.freeze(Through.prototype);
Object.freeze(Through);
Object.freeze(Stamp.prototype);
Object.freeze(Stamp);

/** Stamp a node with its origin: it must be an object that is still extensible (not yet frozen)
 * and not stamped already. `provenance.ts`'s alone. */
export function stamp(target: object, origin: Origin): void {
  if (!Object.isExtensible(target) || read(target) !== null) {
    throw new TypeError(UNSTAMPABLE);
  }
  write(target, Object.freeze({ operation: origin.operation, path: origin.path, dataset: origin.dataset, era: origin.era }));
}

/** A node's origin, `null` for anything not stamped (a copy of a node included). `provenance.ts`'s
 * alone (`idAt`). */
export function originOf(value: unknown): Origin | null {
  return typeof value === "object" && value !== null ? read(value) : null;
}
