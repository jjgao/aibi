/**
 * The confirmation step of a destructive operation (D412): the action's button opens, as state
 * inside the screen (never a route or an address), its full effect in fixed words and two
 * buttons; the operation runs only on "Confirm". The screens (M5.5) use it for every withdraw,
 * discard and publish.
 */
import { useState } from "react";

/** Each destructive action: its button's words and its full effect. */
export const EFFECTS = {
  withdraw: {
    action: "Withdraw the release",
    effect: "Withdrawing the release stops serving it to everyone who reads the dataset. Its label is never given again. This cannot be undone.",
  },
  discard: {
    action: "Discard the session",
    effect: "Discarding the session drops its draft and every change made in it. This cannot be undone.",
  },
  publish: {
    action: "Publish the draft",
    effect: "Publishing the draft makes it the dataset's next release, served to everyone who reads the dataset, and ends the session.",
  },
} as const;

export type Destructive = keyof typeof EFFECTS;

export function Confirm({ action, onConfirm }: { readonly action: Destructive; readonly onConfirm: () => void }) {
  const [asking, setAsking] = useState(false);
  const { action: words, effect } = EFFECTS[action];
  if (!asking) {
    return (
      <button
        type="button"
        onClick={() => {
          setAsking(true);
        }}
      >
        {words}
      </button>
    );
  }
  return (
    <div role="group" aria-label={words}>
      <p>{effect}</p>
      <button
        type="button"
        onClick={() => {
          setAsking(false);
          onConfirm();
        }}
      >
        Confirm
      </button>
      <button
        type="button"
        onClick={() => {
          setAsking(false);
        }}
      >
        Cancel
      </button>
    </div>
  );
}
