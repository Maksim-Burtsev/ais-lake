import type { ReactNode } from 'react';
import { FilterChips, SilentCountLine } from './FilterChips';
import { TopBar } from './TopBar';

/** S1 shell: the bar on top, everything else over the sea.
 *  The sea is a flat --sea-deep plate here — the MapLibre canvas replaces it
 *  in the map task; the chips overlay keeps its frame position either way
 *  (left 20px, top 74px from the page top = 18px under the 56px bar). */
export function AppShell({ children }: { children?: ReactNode }) {
  return (
    <div className="relative flex h-full min-h-screen flex-col bg-[var(--page)]">
      <TopBar />
      <main className="relative flex-1 bg-[var(--sea-deep)]">
        {children}
        <div className="pointer-events-none absolute top-[18px] left-[20px] z-20">
          <FilterChips />
        </div>
        {/* frame: left 20px, top 114px from the page top = 58px under the 56px bar */}
        <div className="pointer-events-none absolute top-[58px] left-[20px] z-20">
          <SilentCountLine />
        </div>
      </main>
    </div>
  );
}
