/** S1 filter row (F7) — same frames as TopBar. The chip is the URL: each one
 *  patches `filter` and the map dims everything that does not match. "Recently
 *  silent" is the loud one — coral chip, its own dismiss, and a sentence under
 *  the row saying what the water is showing.
 *  docs/design "Batch 1 Map Shell" :: "Filter · Recently silent". */

import limits from '../limits.json';
import { useLiveStore } from '../state/live';
import { useUrlStore, type VesselFilter } from '../state/url';

/** Label -> the URL value it stands for; "All ships" is the absent filter. */
export const FILTERS: readonly [string, VesselFilter | undefined][] = [
  ['All ships', undefined],
  ['Tankers', 'tankers'],
  ['Cargo', 'cargo'],
  ['Waiting at anchor', 'anchored'],
  ['Recently silent', 'silent'],
];

const BASE = 'flex h-[var(--chip-h)] cursor-pointer items-center gap-[7px] border-0 px-[13px] text-[12.5px]';
const IDLE =
  'flex h-[var(--chip-h)] cursor-pointer items-center px-[13px] text-[12.5px] border border-[var(--chrome-chip-border)] text-[var(--chrome-chip-ink)] bg-[var(--chrome-chip-fill)]';
const ACTIVE = `${BASE} text-[var(--chrome-chip-active-ink)] bg-[var(--chrome-chip-active-fill)]`;
const SILENT = `${BASE} text-[var(--chrome-chip-silent-ink)] bg-[var(--chrome-chip-silent-fill)]`;

export function FilterChips() {
  const filter = useUrlStore((s) => s.filter);
  const patch = useUrlStore((s) => s.patch);

  return (
    <div className="pointer-events-auto flex gap-[var(--chip-gap)] whitespace-nowrap">
      {FILTERS.map(([label, key]) => {
        const isActive = key === filter;
        return (
          <button
            key={label}
            type="button"
            aria-pressed={isActive}
            // the ✕ is part of the chip: clicking the active silent chip dismisses it
            onClick={() => patch({ filter: isActive && key === 'silent' ? undefined : key })}
            className={isActive ? (key === 'silent' ? SILENT : ACTIVE) : IDLE}
          >
            {label}
            {isActive && key === 'silent' && <span className="text-[11px] opacity-70">✕</span>}
          </button>
        );
      })}
    </div>
  );
}

/** The window the sentence speaks in, from the one limits file (F27) — the same
 *  number the api cuts the map snapshot at, never one typed in here. */
const WINDOW_H = limits.map_vessel_age_s.max / 3600;

/** The result line under the chips — a sentence, not a count badge.
 *  The frame's mono "· longest gap 3 h 30 m" tail waits for M3: nothing measures
 *  gap length yet. */
export function SilentCountLine() {
  const filter = useUrlStore((s) => s.filter);
  const silent = useLiveStore((s) => s.silent);
  if (filter !== 'silent') return null;
  return (
    <span className="text-[13px] text-[var(--chrome-chip-ink)]">
      {silent === 0
        ? `No ships have gone silent here in the last ${WINDOW_H} h.`
        : `${silent} ${silent === 1 ? 'ship' : 'ships'} went silent here in the last ${WINDOW_H} h`}
    </span>
  );
}
