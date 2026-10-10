/**
 * The token's shell (D412, D423), in fixed words: while the client is locked, why, and a form
 * for the operator's name and the curator token; while it is unlocked, the forget-token action.
 *
 * The token field is an **uncontrolled** `<input type="password" autocomplete="off">`, read by
 * its ref when the form is sent and emptied at once: a controlled one (`value={state}`) is written
 * by React into the input's `value` attribute, where the token would stand in the DOM. Nothing
 * here holds the token beyond the call that hands it to the client (a test walks the React tree
 * for it); nothing shows a name, a token or any text a request or an answer carried. While an unlock
 * is pending, Cancel aborts its request: the client stays locked and holds nothing.
 *
 * `autocomplete="off"` and the password managers' opt-out attributes (`data-1p-ignore`,
 * `data-lpignore`, `data-bwignore`) are requests, not a barrier: Chrome may still offer to save the
 * token once the form is gone (a residual of D423, which the person consents to).
 */
import { type SyntheticEvent, useRef, useState, useSyncExternalStore } from "react";

import { type LockReason, operator, type Unlocked } from "../api/operator";

/** Why the client is locked, in the shell's words. */
export const LOCKED_WORDS: Readonly<Record<LockReason, string>> = {
  start: "Enter your name and the curator token to begin.",
  forgotten: "The token is forgotten. Enter it again to go on.",
  idle: "Locked after ten minutes without input. Enter the token again to go on.",
  token: "The server refused the token. Enter it again to go on.",
  failures: "Too many refused tokens from this address. Wait a minute, then enter the token again.",
  page: "Locked when the page was left. Enter the token again to go on.",
  restored: "Locked: the page was restored from the browser's cache. Enter the token again to go on.",
};

/** What an unlock that did not change why the client is locked comes to, in the shell's words. */
export function unlockWords(outcome: Unlocked): string | null {
  switch (outcome.kind) {
    case "invalid":
      return outcome.what === "name"
        ? "The name is not one the server accepts: 1 to 200 characters, with no control, line-break or bidi formatting character and no token in it."
        : "The token is not in its form: aibi_ and 43 characters.";
    case "unknown":
      return outcome.reason === "aborted"
        ? "Unlocking was cancelled. Nothing is held. Enter the token again to go on."
        : "The server could not be reached, or its answer could not be read. Nothing is known of the outcome: try again.";
    case "refused":
      return "The server refused the request.";
    case "unlocked":
    case "locked":
      return null;
  }
}

export const UNLOCKED_WORDS = "Unlocked. The token is held by this page alone, until you forget it, leave the page or stop for ten minutes.";

const subscribe = (listener: () => void) => operator.subscribe(listener);
const view = () => operator.view();

export function Shell() {
  const state = useSyncExternalStore(subscribe, view);
  const name = useRef<HTMLInputElement>(null);
  const token = useRef<HTMLInputElement>(null);
  const cancel = useRef<AbortController | null>(null);
  const [pending, setPending] = useState(false);
  const [words, setWords] = useState<string | null>(null);

  if (!state.locked) {
    return (
      <section aria-label="token">
        <p role="status">{UNLOCKED_WORDS}</p>
        <button
          type="button"
          onClick={() => {
            setWords(null);
            operator.forget();
          }}
        >
          Forget the token
        </button>
      </section>
    );
  }

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    const field = token.current;
    const given = field?.value ?? "";
    if (field !== null) {
      field.value = "";
    }
    const controller = new AbortController();
    cancel.current = controller;
    setPending(true);
    setWords(null);
    void operator.unlock(given, name.current?.value ?? "", controller.signal).then((outcome) => {
      cancel.current = null;
      setPending(false);
      setWords(unlockWords(outcome));
    });
  };

  return (
    <section aria-label="token">
      <p role="status">{pending ? "Unlocking." : LOCKED_WORDS[state.reason ?? "start"]}</p>
      <form onSubmit={submit}>
        <label>
          Your name <input ref={name} name="operator" type="text" autoComplete="off" spellCheck={false} />
        </label>
        <label>
          Curator token{" "}
          <input
            ref={token}
            name="token"
            type="password"
            autoComplete="off"
            spellCheck={false}
            data-1p-ignore="true"
            data-lpignore="true"
            data-bwignore="true"
          />
        </label>
        <button type="submit" disabled={pending}>
          Unlock
        </button>
        {pending ? (
          <button
            type="button"
            onClick={() => {
              cancel.current?.abort();
            }}
          >
            Cancel
          </button>
        ) : null}
      </form>
      {words === null ? null : <p role="alert">{words}</p>}
    </section>
  );
}
