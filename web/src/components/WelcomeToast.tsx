/** F11 welcome toast — docs/design/"ais-lake MVP Batch 1 Map Shell.dc.html"
 *  :: "First visit" and :: "First visit · Blue Marble day".
 *
 *  One sentence, one ✕. No scrim, no "Got it", no timer — none of them are in
 *  the frame, and the point of the frame is that the ships keep moving behind
 *  the line. Dismissed once, it is gone for good: that is the whole of F11's
 *  acceptance criterion, so the flag is written before the state flips.
 *
 *  Storage blocked (locked-down browser, third-party frame): the read and the
 *  write are both caught, and a failed READ counts as "not dismissed" — the same
 *  fall-back-to-default shape theme.ts uses. A visitor we cannot remember is
 *  treated as a first visitor and gets the welcome; being greeted twice is a
 *  smaller failure than never being told the ships are real. A failed WRITE
 *  still closes the toast, for this session only.
 */

import { useState } from 'react';

/** Flat key, like theme.ts's `theme`: this app owns its whole origin. */
export const WELCOME_STORAGE_KEY = 'welcome-dismissed';

/** Approved copy, verbatim — docs/design/"ais-lake MVP Batch 1 Map Shell.dc.html"
 *  :: "First visit", and docs/05-design-prompts.md §Batch 1. NOT the spec: F11
 *  states the behaviour, the frame owns the words. */
const COPY = 'Real ships, live. Click any of them.';

function wasDismissed(): boolean {
  try {
    return localStorage.getItem(WELCOME_STORAGE_KEY) === '1';
  } catch {
    return false; // unreadable storage == unknown visitor == first visit
  }
}

export function WelcomeToast() {
  // Lazy initialiser: one read on mount, so StrictMode's double render cannot
  // flash the toast back after a dismiss.
  const [gone, setGone] = useState(wasDismissed);
  if (gone) return null;

  const dismiss = () => {
    try {
      localStorage.setItem(WELCOME_STORAGE_KEY, '1');
    } catch {
      /* storage blocked — it stays gone for this session and comes back next */
    }
    setGone(true);
  };

  return (
    <div
      role="status"
      // above the chips (z20), under the ship card (z30) — the frame puts it over
      // everything it shares a screen with, and it shares none with the card.
      className="pointer-events-auto absolute bottom-[26px] left-1/2 z-[25] flex h-[44px] -translate-x-1/2 items-center gap-[16px] border border-[var(--chrome-toast-border)] bg-[var(--chrome-toast-fill)] pr-[10px] pl-[18px] shadow-[var(--chrome-toast-shadow)]"
    >
      <span className="text-[14px] text-[var(--chrome-toast-ink)]">{COPY}</span>
      <button
        type="button"
        aria-label="Dismiss"
        onClick={dismiss}
        className="flex h-[26px] w-[26px] cursor-pointer items-center justify-center border-0 bg-transparent p-0 text-[14px] text-[var(--chrome-toast-dismiss)]"
      >
        ✕
      </button>
    </div>
  );
}
