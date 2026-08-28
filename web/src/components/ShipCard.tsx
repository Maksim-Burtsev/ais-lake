/** F8 — the card behind a ship tap, and the `?sel=` half of F10.
 *  docs/design "Night Passage and Blue Marble" :: 1440 × 900 (2c) for the desktop
 *  panel, "Batch 1 Map Shell" :: "Mobile · ship tapped" (5g) for the ≤390 px sheet.
 *  That sheet is `max-[391px]` throughout: Tailwind's max-* variants are exclusive,
 *  so 390 itself — the frame's own width — only matches one pixel higher.
 *
 *  GET /v1/ships/{mmsi} owns every word on it. The status sentence is the
 *  refinery's, read verbatim (F8: one source of truth) — nothing here composes
 *  prose about a ship, it only sets the leading clause bold and the figures in
 *  mono, the way the frame does.
 *
 *  ≤300 ms: the panel is on screen in the same frame as the click, because the
 *  MMSI alone is enough to draw it, and every card already read comes back from
 *  `seen` with no request at all. Unknowns are "—", never invented (F15).
 *
 *  Gaps, both deliberate: the frame's "· a 17-ship queue awaits" needs port queue
 *  numbers (F19, M5), so the destination and ETA are shown as reported facts
 *  instead of a sentence nobody can back up; ☆ (F24, M6) and "Follow the story →"
 *  (F12, M4) are drawn and disabled rather than wired to something that is not there.
 */

import { useEffect, useState } from 'react';
import { useUrlStore } from '../state/url';

interface Card {
  mmsi: number;
  identity: {
    imo: number | null;
    name: string | null;
    callsign: string | null;
    flag: string | null;
    class: string | null;
    sym: string | null;
    size_m: number | null;
    draught_m: number | null;
    destination: string | null;
    eta: string | null;
  };
  sentence: string | null;
  latest: { ts: number; sog: number; cog: number; state: string } | null;
}

/** Re-selecting a ship already read costs nothing. A session's cards are a few KB;
 *  ponytail: no eviction until that is measurably untrue. */
const seen = new Map<number, Card>();

const DASH = '—';

/** The frame's compact "AIS 8s AGO". It is a live map, so this keeps counting. */
const age = (ms: number): string => {
  const s = Math.max(0, Math.round(ms / 1000));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.round(s / 60)}m`;
  if (s < 86_400) return `${Math.round(s / 3600)}h`;
  return `${Math.round(s / 86_400)}d`;
};

/** 249 118 000 — the frame groups an MMSI in threes. */
const grouped = (mmsi: number) => String(mmsi).replace(/(\d{3})(?=\d)/g, '$1 ');

/** Typography over the server's own words: the leading clause in the frame runs up
 *  to the first " at " or " — ", and every figure is set in mono. Both are FOUND in
 *  the sentence — none of it is written here. */
function Sentence({ text }: { text: string }) {
  const cut = text.search(/ at | — /);
  const head = cut === -1 ? text : text.slice(0, cut);
  return (
    <p className="mt-0 mb-0 text-[14px] leading-[1.58] text-[var(--chrome-card-sentence)] [text-wrap:pretty] max-[391px]:leading-[1.55]">
      <strong className="font-semibold text-[var(--chrome-card-strong)]">{head}</strong>
      {(cut === -1 ? '' : text.slice(cut)).split(/(\d[\d.:]*)/).map((part, i) =>
        i % 2 ? (
          <span key={i} className="font-mono text-[13px] text-[var(--chrome-card-figure)]">
            {part}
          </span>
        ) : (
          part
        ),
      )}
    </p>
  );
}

export function ShipCard() {
  const selection = useUrlStore((s) => s.selection);
  const patch = useUrlStore((s) => s.patch);
  const [card, setCard] = useState<Card | null>(null);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (selection === undefined) {
      setCard(null);
      return;
    }
    const cached = seen.get(selection);
    setCard(cached ?? null); // null = the shell, drawn from the MMSI while we read
    if (cached) return;
    let alive = true;
    fetch(`/v1/ships/${selection}`)
      .then(async (response) => {
        if (!response.ok) throw new Error(`ship ${response.status}`);
        return (await response.json()) as Card;
      })
      .then((body) => {
        seen.set(selection, body);
        if (alive) setCard(body);
      })
      .catch((error: unknown) => console.warn('ship card:', error));
    return () => {
      alive = false;
    };
  }, [selection]);

  // Escape closes, same idiom as the region picker. A click on the water closes it
  // too — that one is MapCanvas's, since only the map knows what was under the tap.
  useEffect(() => {
    if (selection === undefined) return;
    const key = (event: KeyboardEvent) => {
      if (event.key === 'Escape') patch({ selection: undefined });
    };
    window.addEventListener('keydown', key);
    return () => window.removeEventListener('keydown', key);
  }, [selection, patch]);

  useEffect(() => {
    if (!card?.latest) return;
    setNow(Date.now()); // the clock may have been sitting idle since the last card
    const timer = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, [card]);

  if (selection === undefined) return null;
  const id = card?.identity;
  const fix = card?.latest;

  return (
    // frame: right 22px, top 78px from the page top = 22px under the 56px bar
    <aside
      aria-label="Selected vessel"
      className="pointer-events-auto absolute top-[22px] right-[22px] z-30 box-border w-[352px] border border-[var(--chrome-card-border)] bg-[var(--chrome-card-fill)] px-[21px] pt-[19px] pb-[21px] shadow-[var(--chrome-card-shadow)] max-[391px]:top-auto max-[391px]:right-0 max-[391px]:bottom-0 max-[391px]:left-0 max-[391px]:w-auto max-[391px]:border-0 max-[391px]:border-t max-[391px]:bg-[var(--chrome-card-sheet-fill)] max-[391px]:px-[20px] max-[391px]:pt-[10px] max-[391px]:pb-[26px] max-[391px]:shadow-[var(--chrome-card-sheet-shadow)]"
    >
      <div
        aria-hidden="true"
        className="mx-auto mb-[14px] hidden h-[4px] w-[38px] rounded-[2px] bg-[var(--chrome-card-handle)] max-[391px]:block"
      />
      <div className="flex items-center justify-between font-mono text-[10px] tracking-[0.16em] text-[var(--chrome-card-eyebrow)]">
        <span>SELECTED VESSEL</span>
        <span className="text-[var(--chrome-card-age)]">
          {fix ? `AIS ${age(now - fix.ts * 1000)} AGO` : `AIS ${DASH}`}
        </span>
      </div>
      <div className="mt-[11px] font-display text-[31px] leading-[1.12] font-medium text-[var(--chrome-card-name)] max-[391px]:mt-[8px] max-[391px]:text-[30px]">
        {id?.name ?? DASH}
      </div>
      <div className="mt-[5px] text-[12.5px] text-[var(--chrome-card-sub)] max-[391px]:mt-[4px]">
        {card
          ? [id?.class ?? DASH, id?.flag ?? 'unknown flag', id?.size_m ? `${id.size_m} m` : DASH]
              .join(' · ')
          : DASH}
      </div>
      <div className="my-[15px] h-px bg-[var(--chrome-card-rule)] max-[391px]:hidden" />
      <div className="max-[391px]:mt-[14px]">
        {card?.sentence ? <Sentence text={card.sentence} /> : null}
      </div>

      {/* the frame's mono facts row. Off the sheet entirely on mobile. */}
      <div className="mt-[15px] flex flex-wrap gap-x-[22px] gap-y-[6px] font-mono text-[10.5px] tracking-[0.06em] text-[var(--chrome-card-particulars)] max-[391px]:hidden">
        <span>MMSI {grouped(selection)}</span>
        <span>DRAUGHT {id?.draught_m ? `${id.draught_m} m` : DASH}</span>
        {id?.destination ? <span>BOUND FOR {id.destination}</span> : null}
        {id?.eta ? <span>ETA {id.eta}</span> : null}
      </div>

      <div className="mt-[17px] flex gap-[9px] max-[391px]:mt-[16px] max-[391px]:gap-[10px]">
        <button
          type="button"
          disabled
          aria-label="Follow the story — the vessel story page is not built yet"
          className="flex h-[38px] flex-1 cursor-default items-center justify-center border-0 text-[13.5px] font-semibold opacity-55 max-[391px]:h-[46px] max-[391px]:text-[14.5px]"
          style={{
            background: 'var(--chrome-card-action-fill)',
            color: 'var(--chrome-card-action-ink)',
          }}
        >
          Follow the story →
        </button>
        <button
          type="button"
          disabled
          aria-label="Follow this ship — the watchlist is not built yet"
          className="flex h-[38px] w-[38px] cursor-default items-center justify-center border border-[var(--chrome-card-star-border)] bg-transparent text-[15px] text-[var(--chrome-card-star-ink)] opacity-55 max-[391px]:h-[46px] max-[391px]:w-[46px] max-[391px]:text-[17px]"
        >
          ☆
        </button>
      </div>
    </aside>
  );
}
