/**
 * What the rest of `src/` may import of the client (D419, D426): outside `src/api/`, every import
 * of it is of this module (the lint holds it), and what this module exports is checked over the
 * TypeScript program (`tests/gates/api-graph.test.ts`): no `box`, `textOf`, `decode`, `exchange` or
 * other internal; no function that takes a server number and gives anything but a server number;
 * no function that takes a server text but the pinned takers (`text.ts`'s, and `entriesOf`); and no
 * export that gives a raw `string` but the pinned ones (`idOf`'s `DatasetId`, the client's errors'
 * fixed words). A public tool's answer is read through `typed.ts`'s functions alone, every string of
 * it a `ServerText` and every data-keyed map a `BoxedMap` (D426); the raw `exchange` and the raw
 * `JsonOut` are not here (the end-to-end harness reaches the oracle's requests through `oracle.ts`,
 * and the operator entry's raw answers through `operator.ts`). The generated types, the request's
 * JSON-value types and the boxes' types are types alone. `decodedCount`, the oracle's counter, is
 * `oracle.ts`'s: the end-to-end harness imports it there and nothing else may.
 */
export type { BoxedMap, ServerNumber, ServerText } from "./box";
export { entriesOf, isServerNumber } from "./box";
export type { JsonIn, JsonInNull } from "./json";
export type { components, operations, paths } from "./generated/openapi";
export * as routes from "./generated/routes";
export type { OperationId, Route } from "./route";
export { ClientError, REFUSALS, RESPONSE_CAP_BYTES, start, type RequestBody } from "./client";
export { DecodeError } from "./decode";
export {
  countCohort,
  describeDataset,
  explain,
  listAnalyses,
  runAnalysis,
  searchCatalog,
  SHAPE_MISMATCH,
  UNREACHABLE,
  validateDocument,
  type Answered,
  type Boxed,
  type BoxedJson,
  type BoxedRefusals,
  type BoxedSegment,
  type InvalidCause,
  type Outcome,
  type TypedOperation,
} from "./typed";
export { choiceOf, compareText, idOf, kindOf, memberOf, sameText, type Choice, type DatasetId, type GrammarName, type Kind, type Member, type VocabularyName } from "./text";
