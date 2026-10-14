/**
 * `Field` and `Typed` (D428): what the person types, the one kind of text an `<input>`'s `value` is
 * in the app (never a server text, which throws there, and never an address's or an operator's raw
 * text). `Field` is `Typed`'s one mint (this module's one type assertion: what the field holds is
 * what the person typed); a test refuses any other module that mints one (an assertion, a type
 * predicate or an overload signature that gives a `Typed`). A `Typed` is a request's material (M5.3),
 * a string to the generated request types; it is never shown as data.
 */
import type { ReactElement, ReactNode } from "react";

declare const typedBrand: unique symbol;

/** What the person typed, made by `Field` alone. */
export type Typed = string & { readonly [typedBrand]: true };

/** What the person typed into the field. */
function typed(raw: string): Typed {
  return raw as Typed;
}

/** A field that has not been typed into. */
export const UNTYPED: Typed = typed("");

/** A field of one line: `children` is its label's words, `value` what it holds, `onChange` handed
 * what the person types. */
export function Field({ children, value, onChange }: { readonly children: ReactNode; readonly value: Typed; readonly onChange: (value: Typed) => void }): ReactElement {
  return (
    <label>
      {children}
      <input
        type="text"
        autoComplete="off"
        spellCheck={false}
        value={value}
        onChange={(event) => {
          onChange(typed(event.currentTarget.value));
        }}
      />
    </label>
  );
}
