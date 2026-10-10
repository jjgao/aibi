/**
 * The three JSON-value types the generated types reach through the document's `x-aibi-json` mark
 * (D416, D419), written by hand: a code generator renders the six recursive components that carry
 * the mark as types that refer to themselves through their own annotation (TS2502 in each, which
 * a test pins on the rendering without the transform), and what they mean is simply "any JSON
 * value" on each side of the wire.
 *
 * - `JsonIn`: a value a request carries where `null` is no JSON value (`{direction: "request",
 *   null: false}`: `ParameterValue`, `DocumentJson`, `SubstitutedDocumentJson`). Its numbers are
 *   JS numbers: what a person typed (a response's number never reaches a request through this
 *   type, which no `ServerNumber` is assignable to; how one goes back is the request half's,
 *   M5.1c-1c and M5.3).
 * - `JsonInNull`: the same with `null` (`{direction: "request", null: true}`: `RequestJson`,
 *   `DescriptorJson`).
 * - `JsonOut`: a value a response carries (`{direction: "response", null: true}`: `OutputJson`),
 *   its numbers boxed like every response number, as `decode` makes them.
 *
 * All three are read-only, as the generated types are (`immutable`), and as `decode` freezes what
 * it makes.
 */
import type { ServerNumber } from "./box";

export type JsonIn = string | boolean | number | readonly JsonIn[] | { readonly [member: string]: JsonIn };

export type JsonInNull = string | boolean | number | null | readonly JsonInNull[] | { readonly [member: string]: JsonInNull };

export type JsonOut = string | boolean | ServerNumber | null | readonly JsonOut[] | { readonly [member: string]: JsonOut };
