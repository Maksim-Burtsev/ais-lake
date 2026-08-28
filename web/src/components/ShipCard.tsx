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

/** Re-selecting a ship already read paints in the same frame, then re-reads. It is
 *  a first frame, never the answer: the entry carries `latest` — a timestamp, a
 *  speed and a state, all of which age — so a card kept from 12:00 would tell you
 *  "AIS 4s AGO" at 12:08 about a ship that has crossed the map and may have
 *  anchored while the hull under it was already repainted. The freshness is the
 *  one promise the product rests on, so the fetch always goes out.
 *  ponytail: no eviction. The problem here was time, not bytes, and it is the
 *  re-read that fixes it; add an LRU only when a session's few KB measurably hurt. */
const seen = new Map<number, Card>();

const DASH = '—';

/** Without this the shell IS the failure: name "—", "— · unknown flag · —", both
 *  buttons dead — a card asserting we hold this vessel and know nothing about her,
 *  drawn pixel-for-pixel the same as a card still loading. A typo'd ?sel=, a stale
 *  share link and a ship aged out of the lake all land here. */
type Failure = 'missing' | 'unreadable' | null;
const FAILURE_COPY: Record<'missing' | 'unreadable', string> = {
  missing: 'No ship with this number is in the lake.',
  unreadable: 'We could not read her details just now.',
};

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
 *  to the em dash, or to the first " at " when there is no dash, and every figure is
 *  set in mono. Both are FOUND in the sentence — none of it is written here.
 *
 *  The dash wins because it is the sentence's own clause boundary. One `search`
 *  over an alternation cannot express that — it returns the leftmost match, so
 *  " at " always beat " — " and CLAUDE.md's own example, "Waited at anchor — 14
 *  hours", bolded "Waited" alone.
 *
 *  The durable fix is not in this file: refinery/state.py composes the sentence and
 *  already knows where its clause ends, so it should mark the boundary rather than
 *  have the client re-derive prose structure it did not write. F8 says the sentence
 *  is the server's, and this splitter is a second, undocumented contract with
 *  sentence_for() — one that a sentence carrying neither delimiter ("Went silent
 *  3 h ago") still loses, by setting the whole line bold. */
function Sentence({ text }: { text: string }) {
  const dash = text.indexOf(' — ');
  const cut = dash === -1 ? text.search(/ at /) : dash;
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

/** Nothing selected, nothing to draw. The panel itself is keyed on the MMSI, so
 *  picking another ship remounts it: every piece of per-ship state resets on its
 *  own instead of being reset inside an effect, which is what React's own rule
 *  about cascading renders is asking for. */
export function ShipCard() {
  const selection = useUrlStore((s) => s.selection);
  if (selection === undefined) return null;
  return <Panel key={selection} mmsi={selection} />;
}

function Panel({ mmsi }: { mmsi: number }) {
  const patch = useUrlStore((s) => s.patch);
  // The cache is read during render, not in an effect: it is this ship's first
  // frame, and null is the shell we draw from the MMSI alone while the read runs.
  const [card, setCard] = useState<Card | null>(() => seen.get(mmsi) ?? null);
  const [failure, setFailure] = useState<Failure>(null);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    let alive = true;
    let status = 0; // 404 is a different sentence from a network that never answered
    fetch(`/v1/ships/${mmsi}`)
      .then(async (response) => {
        status = response.status;
        if (!response.ok) throw new Error(`ship ${status}`);
        return (await response.json()) as Card;
      })
      .then((body) => {
        seen.set(mmsi, body);
        if (alive) setCard(body);
      })
      .catch((error: unknown) => {
        console.warn('ship card:', error);
        if (!alive) return;
        // Only when there is nothing to show: a card we already hold and merely
        // failed to refresh still beats an apology over a ship we demonstrably have.
        if (seen.has(mmsi)) return;
        setFailure(status === 404 ? 'missing' : 'unreadable');
      });
    return () => {
      alive = false;
    };
  }, [mmsi]);

  // Escape closes, same idiom as the region picker. A click on the water closes it
  // too — that one is MapCanvas's, since only the map knows what was under the tap.
  //
  // The card is the outermost thing an Escape can close, so it takes the key only
  // when nothing nearer wanted it: whoever consumes an Escape calls preventDefault
  // (Search's dropdown, the region picker), and this listener stands down for an
  // already-defaulted event. Precedence made explicit rather than left to the order
  // two window listeners happened to be registered in — which is why closing the
  // search dropdown used to strip ?sel= and take the card with it.
  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !event.defaultPrevented) patch({ selection: undefined });
    };
    window.addEventListener('keydown', key);
    return () => window.removeEventListener('keydown', key);
  }, [patch]);

  // The eyebrow counts up on its own. `now` starts at mount, and the panel is
  // remounted per ship, so the first tick is never stale by more than a second.
  useEffect(() => {
    if (!card?.latest) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, [card]);

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
        {failure ? (
          <p className="mt-0 mb-0 text-[14px] leading-[1.58] text-[var(--chrome-card-sentence)] [text-wrap:pretty]">
            {FAILURE_COPY[failure]}
          </p>
        ) : null}
      </div>

      {/* the frame's mono facts row. Off the sheet entirely on mobile. */}
      <div className="mt-[15px] flex flex-wrap gap-x-[22px] gap-y-[6px] font-mono text-[10.5px] tracking-[0.06em] text-[var(--chrome-card-particulars)] max-[391px]:hidden">
        <span>MMSI {grouped(mmsi)}</span>
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
