/**
 * What the rest of `src/` may import of the client (D419): outside `src/api/`, every import of
 * it is of this module (the lint holds it), and what this module exports is checked over the
 * TypeScript program (`tests/gates/api-graph.test.ts`): no `box`, `textOf`, `decode` or other
 * internal, and no function that takes a server number and gives anything but a server number.
 * The generated types, the three JSON-value types and the `ServerNumber` type are types alone.
 * `decodedCount`, the oracle's counter, is `oracle.ts`'s: the end-to-end harness imports it there
 * and nothing else may.
 */
export type { ServerNumber } from "./box";
export { isServerNumber } from "./box";
export type { JsonIn, JsonInNull, JsonOut } from "./json";
export type { components, operations, paths } from "./generated/openapi";
export * as routes from "./generated/routes";
export type { OperationId, Route } from "./route";
export { ClientError, exchange, REFUSALS, RESPONSE_CAP_BYTES, start, type Answer, type RequestBody } from "./client";
export { DecodeError } from "./decode";
