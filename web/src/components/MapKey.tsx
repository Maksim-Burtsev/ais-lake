/** F11 map key + ship count — docs/design/"ais-lake MVP Batch 1 Map Shell.dc.html"
 *  :: "First visit" and :: "First visit · Blue Marble day".
 *
 *  Exactly three rows. Moored and selected are not in the frame's key and must
 *  not be added: moored ships are furniture, and selection is something you just
 *  did — neither needs explaining to someone who has been here four seconds.
 *
 *  The under-way glyph is two-tone, which the map itself cannot draw: the sprite
 *  atlas is one alpha channel and `icon-color` is one colour (SYMBOLOGY.md §6).
 *  The key is inline SVG, not a sprite, so the lit/dark hull the water gives up
 *  is available here — an inconsistency on purpose, not by accident. The curve is
 *  the frame's own legend hull rather than `hullD()`: no class in CMETA has its
 *  2.4 length-to-beam ratio, because the key is not naming a class.
 *
 *  The count is the panel's LAST CHILD, not a sibling — the frame draws one box
 *  with a rule across it.
 */

import type { ReactNode } from 'react';
import { useLiveStore } from '../state/live';

const COUNT = new Intl.NumberFormat('en-US');

/** Full hull, then the port half painted over it. In night the port half is the
 *  shaded one; the day frame inverts the pair (dark hull, sunlit port side). */
const HULL =
  'M0,-6.4 C1.7,-4.3 2.7,-2.1 2.7,0 L2.7,4.8 Q2.7,6.4 1.3,6.4 L-1.3,6.4 Q-2.7,6.4 -2.7,4.8 L-2.7,0 C-2.7,-2.1 -1.7,-4.3 0,-6.4 Z';
const HULL_PORT = 'M0,-6.4 C-1.7,-4.3 -2.7,-2.1 -2.7,0 L-2.7,4.8 Q-2.7,6.4 -1.3,6.4 L0,6.4 Z';

const ROW = 'flex items-center gap-[9px] text-[12px] text-[var(--chrome-key-ink)]';

/** The frame's box: a 15-unit square around the origin, drawn at 14 px. */
const Glyph = ({ children }: { children: ReactNode }) => (
  <svg width="14" height="14" viewBox="-7.5 -7.5 15 15" aria-hidden="true">
    {children}
  </svg>
);

export function MapKey() {
  const ships = useLiveStore((s) => s.ships);
  return (
    // pointer-events-none: a legend is read, never clicked, and it sits over the
    // sea — every pan that starts on it has to reach the map.
    <div
      role="group"
      aria-label="Map key"
      className="pointer-events-none absolute bottom-[20px] left-[20px] z-10 flex flex-col gap-[7px] border border-[var(--chrome-key-border)] bg-[var(--chrome-key-fill)] px-[15px] pt-[12px] pb-[13px]"
    >
      <div className={ROW}>
        <Glyph>
          <path d={HULL} fill="var(--chrome-key-hull)" />
          <path d={HULL_PORT} fill="var(--chrome-key-hull-port)" />
        </Glyph>
        Under way
      </div>
      <div className={ROW}>
        <Glyph>
          <circle
            r="6.4"
            fill="none"
            stroke="var(--chrome-key-anchor-ring)"
            strokeWidth="1"
            strokeDasharray="2 2.4"
          />
          <circle r="2.6" fill="var(--chrome-key-anchor-dot)" />
        </Glyph>
        At anchor
      </div>
      <div className={ROW}>
        <Glyph>
          <circle r="5.8" fill="none" stroke="var(--chrome-key-silent)" strokeWidth="1" />
          <circle r="2.2" fill="var(--chrome-key-silent)" />
        </Glyph>
        Silent
      </div>
      <div className="mt-[2px] h-px bg-[var(--chrome-key-rule)]" />
      {/* the dash is the card's own idiom for a fact we do not have (F15): before
          anything has answered we cannot tell how many ships are out there, and a
          zero over a full sea would be the one lie this panel could tell. */}
      <div className="font-mono text-[11px] tracking-[0.1em] text-[var(--chrome-key-count)]">
        {ships === null ? '—' : COUNT.format(ships)} SHIPS
      </div>
    </div>
  );
}
