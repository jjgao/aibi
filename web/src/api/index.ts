/**
 * What the rest of `src/` may import of the client (D419, D426): outside `src/api/`, every import
 * of it is of this module (the lint holds it), and what this module exports is checked over the
 * TypeScript program (`tests/gates/api-graph.test.ts`): no `box`, `textOf`, `decode`, `exchange` or
 * other internal; no function that takes a server number but the pinned ones (`NUMBER_PINS`, D430,
 * below: none gives a number or its text);
 * no function that takes a server text but the pinned takers (`text.tsx`'s, and `entriesOf`), among
 * them `showText`, the one door from a server text to the DOM (D428): the index's pinned export that
 * `src/layout/` alone imports (a lint row, and a scan of the symbols the program resolves,
 * `tests/gates/layout-form.test.ts`); and no export that gives a raw
 * `string` or a door but the pinned ones (`idOf`'s and `idOfAddress`'s `DatasetId`, `showText`'s
 * element, the client's errors' fixed words). A public tool's answer is read through `typed.ts`'s functions alone, every string of
 * it a `ServerText` and every data-keyed map a `BoxedMap` (D426); the raw `exchange` and the raw
 * `JsonOut` are not here (the end-to-end harness reaches the oracle's requests through `oracle.ts`,
 * and the operator entry's raw answers through `operator.ts`). The generated types, the request's
 * JSON-value types and the boxes' types are types alone. `decodedCount`, the oracle's counter, is
 * `oracle.ts`'s: the end-to-end harness imports it there and nothing else may.
 *
 * **Numbers (D430).** The functions that take a server number are pinned by their exact signatures
 * (`NUMBER_PINS`, beside the guard): `Num`, which shows one by its source text (`format.tsx`, the one
 * module that shows a number); the sanctioned predicates `isZero`, `isPositive` and `largestOf`
 * (`numbers.ts`), which give a boolean or a key of the map, never a number or its text, and of which
 * none compares two numbers given apart; `listOf`, a list's shown members and "and N more" for D312's lists alone,
 * by position; and `RetryAfter`, a refusal's `Retry-After`. Nothing exported gives a server number's
 * text, or a JS number made from one.
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
export { type Facet, type Listed, type Lists, type ListShown, listOf, NOT_READ, Num, type Refused, RetryAfter } from "./format";
export { isPositive, isZero, largestOf, LISTED } from "./numbers";
export { choiceOf, compareText, idOf, idOfAddress, kindOf, memberOf, sameText, showText, type Choice, type DatasetId, type GrammarName, type Kind, type Member, type VocabularyName } from "./text";
