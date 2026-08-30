/** S2 vessel page — docs/design "ais-lake MVP Batch 2 Vessel Page.dc.html"
 *  :: "Vessel page" (6a), "Vessel with no static data yet" (6d), "Mobile vessel
 *  page" (6f).
 *
 *  The page already exists as HTML when this mounts: api/app/ssr.py wrote it,
 *  and left the same payload behind in <script id="story-data"> so the takeover
 *  costs no request. A direct SPA navigation (no SSR, e.g. a client-side link)
 *  finds no payload and fetches /v1 instead — same two endpoints, same words.
 *
 *  Every sentence is the server's (F16, one source of truth). Nothing here
 *  composes prose; it sets the heading, the mono stamp and the dot colour.
 *  Unknowns are "—", never invented (F15).
 *
 *  Gaps, deliberate: the replay scrubber is step 4, so the voyage box draws the
 *  track as a static SVG polyline rather than a MapLibre canvas — it shows the
 *  shape of the passage, which is what the frame's box is for, at no map cost.
 *  The rail foot is frame 6a's: "Watch her live →" is still inert (M6), the ☆
 *  keeps three ships in localStorage (F24 stub), and under them F17's two file
 *  links and F18's copy-the-link sit as frame 10b's pills.
 */

import { useCallback, useEffect, useState } from 'react';
import { GapView, type GapNumbers } from './GapView';
import { Replay } from './Replay';
import { useUrlStore } from '../state/url';

const DASH = '—';

interface Identity {
  imo: number | null;
  name: string | null;
  callsign: string | null;
  flag: string | null;
  class: string | null;
  size_m: number | null;
  draught_m: number | null;
  destination: string | null;
  eta: string | null;
}
interface Card {
  mmsi: number;
  identity: Identity;
  sentence: string | null;
  /** The last fix we hold. Null for a ship who has said nothing in the window. */
  latest?: { ts: number } | null;
}
interface Event {
  event_id: string;
  kind: string;
  t_start: number;
  t_end: number | null;
  prose: string;
  port: { locode: string; name: string | null } | null;
  flag?: { label: string };
  /** F13 — present on every gap; the numbers the opened view shows. */
  numbers?: GapNumbers;
}
interface Story {
  mmsi: number;
  window_d: number;
  limit_line: string;
  events: Event[];
}

/** TWIN of api/app/ssr.py::slugify — the link the card points at has to be the
 *  path the server calls canonical, or every tap costs a 301. */
export const slugify = (name: string | null | undefined): string =>
  (name ?? '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '') || 'ship';

export const shipPath = (name: string | null | undefined, mmsi: number): string =>
  `/ship/${slugify(name)}-${mmsi}`;

/** TWIN of api/app/ssr.py::DOTS — frame 6a's timeline dots, off the map palette. */
const DOT: Record<string, string> = {
  port_call: 'var(--chrome-search-moored)',
  load_delta: 'var(--chrome-search-moored)',
  anchorage: 'var(--chrome-search-anchored)',
  gap: 'var(--chrome-search-silent)',
  departure: 'var(--chrome-search-underway)',
};

/** "14 AUG · 02:10 – 11:40", UTC, same as the server's. */
const stamp = (start: number, end: number | null): string => {
  const at = (s: number) => new Date(s * 1000).toISOString().slice(11, 16);
  const day = new Date(start * 1000)
    .toUTCString()
    .slice(5, 11)
    .toUpperCase()
    .replace(' ', ' ');
  const head = `${day} · ${at(start)}`;
  return end && end > start ? `${head} – ${at(end)}` : head;
};

const grouped = (mmsi: number) => String(mmsi).replace(/(\d{3})(?=\d)/g, '$1 ');

const embedded = (): { card: Card; story: Story } | null => {
  const node = document.getElementById('story-data');
  try {
    return node?.textContent ? (JSON.parse(node.textContent) as { card: Card; story: Story }) : null;
  } catch {
    return null;
  }
};

const mmsiFromPath = (): string => /-(\d{9})$/.exec(location.pathname)?.[1] ?? '';

/** F24 stub — three ships, this device, no account. # F24 M6 sync */
const FOLLOW_KEY = 'follows';
const follows = (): number[] => {
  try {
    const raw: unknown = JSON.parse(localStorage.getItem(FOLLOW_KEY) ?? '[]');
    return Array.isArray(raw) ? raw.filter((v) => typeof v === 'number') : [];
  } catch {
    return []; // storage blocked or junk == follows nothing, same as WelcomeToast
  }
};
export const FOLLOW_CAP = 3;

/** frame 6a's rail foot — the amber CTA and the star — plus F17/F18's row under
 *  it: the two files the browser downloads itself (Content-Disposition names
 *  them) and the link to this exact moment, ?t= and all. */
function RailFoot({ mmsi }: { mmsi: number }) {
  const [followed, setFollowed] = useState(() => follows().includes(mmsi));
  const [toast, setToast] = useState<string | null>(null);

  const toggle = () => {
    const next = followed ? follows().filter((m) => m !== mmsi) : [...follows(), mmsi];
    if (next.length > FOLLOW_CAP) {
      setToast('Three ships is the limit while you are a guest.');
      return;
    }
    try {
      localStorage.setItem(FOLLOW_KEY, JSON.stringify(next));
    } catch {
      /* unwritable storage: the star still flips for this visit */
    }
    setFollowed(!followed);
  };

  const share = () => {
    navigator.clipboard
      .writeText(location.href)
      .then(() => setToast('Link copied — it opens right here, at this moment.'))
      .catch(() => setToast('Could not copy the link — the address bar has it.'));
  };

  const pill =
    'flex h-[32px] items-center border border-[var(--chrome-hairline)] bg-transparent px-[14px] text-[13px] text-[var(--chrome-card-particulars)] no-underline cursor-pointer';

  return (
    <>
      <div className="mt-[26px] flex gap-[10px]">
        <a
          href="/"
          className="flex h-[42px] flex-1 items-center justify-center bg-[var(--chrome-logo-dot)] text-[14px] font-semibold text-[var(--page)] no-underline"
        >
          Watch her live →
        </a>
        <button
          type="button"
          onClick={toggle}
          aria-pressed={followed}
          aria-label={followed ? 'Stop following her' : 'Follow her'}
          data-testid="follow"
          className="flex h-[42px] w-[42px] cursor-pointer items-center justify-center border border-[var(--chrome-hairline)] bg-transparent text-[16px] text-[var(--chrome-card-sub)]"
        >
          {followed ? '★' : '☆'}
        </button>
      </div>
      <div className="mt-[10px] flex flex-wrap gap-[8px]">
        <a className={pill} href={`/v1/ships/${mmsi}/track.csv`} data-testid="dl-csv">
          CSV
        </a>
        <a className={pill} href={`/v1/ships/${mmsi}/track.geojson`} data-testid="dl-geojson">
          GeoJSON
        </a>
        <button type="button" className={pill} onClick={share} data-testid="share">
          Copy link
        </button>
      </div>
      {toast ? (
        <p role="status" className="mt-[10px] mb-0 text-[13px] text-[var(--chrome-card-age)]">
          {toast}
        </p>
      ) : null}
    </>
  );
}

function Particulars({ mmsi, id }: { mmsi: number; id: Identity | undefined }) {
  const rows: [string, string][] = [
    ['MMSI', grouped(mmsi)],
    ['IMO', String(id?.imo ?? DASH)],
    ['CLASS', id?.class ?? DASH],
    ['FLAG', id?.flag ?? DASH],
    ['LOA × BEAM', id?.size_m ? `${id.size_m} × ${DASH} m` : DASH],
    ['DRAUGHT', id?.draught_m ? `${id.draught_m} m` : DASH],
    ['CALLSIGN', id?.callsign ?? DASH],
    ['BOUND FOR', id?.destination ?? DASH],
    ['ETA', id?.eta ?? DASH],
  ];
  return (
    <dl className="mt-[14px] flex flex-col font-mono text-[12px]">
      {rows.map(([label, value]) => (
        <div
          key={label}
          className="flex justify-between gap-[16px] border-b border-[var(--chrome-search-empty-rule)] py-[8px]"
        >
          <dt className="text-[var(--chrome-card-age)]">{label}</dt>
          <dd className="m-0 text-right text-[var(--chrome-card-particulars)]">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function StoryPage() {
  const seeded = embedded();
  const [card, setCard] = useState<Card | null>(seeded?.card ?? null);
  const [story, setStory] = useState<Story | null>(seeded?.story ?? null);
  const key = seeded ? '' : mmsiFromPath();

  // Only when the page was NOT server-rendered: a client-side arrival at this
  // route has no #story-data, and the two /v1 reads say the same thing the SSR
  // pass would have said.
  useEffect(() => {
    if (!key) return;
    let alive = true;
    const read = async <T,>(url: string): Promise<T | null> => {
      const response = await fetch(url);
      return response.ok ? ((await response.json()) as T) : null;
    };
    Promise.all([read<Card>(`/v1/ships/${key}`), read<Story>(`/v1/ships/${key}/story`)])
      .then(([c, s]) => {
        if (!alive) return;
        setCard(c);
        setStory(s);
      })
      .catch((error: unknown) => console.warn('story:', error));
    return () => {
      alive = false;
    };
  }, [key]);

  const id = card?.identity;
  const mmsi = card?.mmsi ?? story?.mmsi ?? Number(key || 0);
  const named = Boolean(id?.name);
  const subtitle = [id?.class ?? DASH, id?.flag ?? DASH, id?.size_m ? `${id.size_m} m` : DASH].join(
    ' · ',
  );
  // F13: ?gap=<event_id> opens that silence in place of the timeline. An id that
  // matches nothing (an old link, a typo) simply leaves the timeline standing.
  const gapId = useUrlStore((s) => s.gap);
  const patch = useUrlStore((s) => s.patch);
  const opened = story?.events.find((e) => e.kind === 'gap' && e.event_id === gapId);

  // F14 — the replay drives the timeline: what has already happened stays lit,
  // what has not yet been reached dims (frame 6c). Null = not replaying.
  const [playhead, setPlayhead] = useState<number | null>(null);
  const onTime = useCallback((value: number | null) => setPlayhead(value), []);

  return (
    <div className="min-h-screen bg-[var(--page)] text-[var(--chrome-card-sentence)]">
      <header className="flex h-[56px] items-center gap-[20px] border-b border-[var(--chrome-hairline)] px-[26px] max-[900px]:px-[16px]">
        <span className="font-display text-[21px] font-medium text-[var(--chrome-logo-ink)]">
          ais<span className="text-[var(--chrome-logo-dot)]">·</span>lake
        </span>
        <a
          href="/"
          className="font-mono text-[11px] tracking-[0.14em] text-[var(--chrome-card-age)] no-underline"
        >
          ← BACK TO THE MAP
        </a>
      </header>

      <div className="px-[56px] pt-[40px] pb-[56px] max-[900px]:px-[20px] max-[900px]:pt-[18px]">
        {opened ? (
          <GapView
            event={opened}
            ship={id?.name ?? 'This ship'}
            onBack={() => patch({ gap: undefined })}
          />
        ) : (
      <div className="grid grid-cols-[880px_380px] gap-x-[64px] max-[900px]:block">
        <main>
          <p className="m-0 font-mono text-[11px] tracking-[0.16em] text-[var(--chrome-card-age)]">
            THE MAP → VESSEL
          </p>
          {/* frame 6a: the name and her line of particulars share one baseline */}
          <div className="mt-[14px] flex flex-wrap items-baseline gap-x-[18px] gap-y-[4px]">
            <h1
              className="m-0 font-display text-[46px] leading-[1.05] font-medium tracking-[-0.01em] text-[var(--chrome-card-name)] max-[900px]:text-[26px]"
              style={named ? undefined : { fontStyle: 'italic', color: 'var(--chrome-card-sub)' }}
            >
              {id?.name ?? 'Unknown vessel'}
            </h1>
            <span className="text-[14px] text-[var(--chrome-card-sub)] max-[900px]:text-[12.5px]">
              {subtitle}
            </span>
          </div>
          <p className="mt-[14px] mb-0 max-w-[760px] text-[16px] leading-[1.6] [text-wrap:pretty] max-[900px]:mt-[10px] max-[900px]:text-[15px]">
            {card?.sentence ??
              `No movements recorded for her in the last ${story?.window_d ?? DASH} days.`}
          </p>
          {mmsi ? (
            <Replay
              mmsi={mmsi}
              latestTs={card?.latest?.ts ?? null}
              onTime={onTime}
              segments={(story?.events ?? []).map((e) => ({
                kind: e.kind,
                t_start: e.t_start,
                t_end: e.t_end,
                color: DOT[e.kind] ?? 'var(--chrome-card-age)',
              }))}
            />
          ) : null}

          <div className="mt-[36px] flex flex-col border-l border-[var(--chrome-hairline)]">
            {(story?.events ?? []).map((event) => (
              <article
                key={event.event_id}
                data-testid="timeline-entry"
                data-reached={playhead === null || event.t_start <= playhead ? 'yes' : 'no'}
                className="flex gap-[20px] pb-[28px] transition-opacity"
                style={
                  playhead !== null && event.t_start > playhead ? { opacity: 0.4 } : undefined
                }
              >
                <span
                  className="mt-[5px] -ml-[6px] h-[11px] w-[11px] flex-none rounded-full border-2 border-[var(--page)]"
                  style={{ background: DOT[event.kind] ?? 'var(--chrome-card-age)' }}
                />
                <div className="flex-1">
                  {/* frame 6f stacks the stamp under the heading on a phone */}
                  <div className="flex max-w-[720px] items-baseline justify-between gap-[20px] max-[900px]:flex-col max-[900px]:items-start max-[900px]:gap-[2px]">
                    <b
                      className="text-[15.5px] font-semibold max-[900px]:text-[14.5px]"
                      style={{
                        color:
                          event.kind === 'gap'
                            ? 'var(--chrome-search-silent)'
                            : 'var(--chrome-card-name)',
                      }}
                    >
                      {event.prose}
                    </b>
                    <time className="font-mono text-[11px] whitespace-nowrap text-[var(--chrome-card-particulars)] max-[900px]:text-[10.5px]">
                      {stamp(event.t_start, event.t_end)}
                    </time>
                  </div>
                  {/* the only line beside the prose: the gap detector's label. The
                      port is already IN the sentence, so repeating it says nothing. */}
                  {/* F13: the flagged silence says so and invites the numbers;
                      an ordinary gap gets the same door, quietly. */}
                  {event.kind === 'gap' ? (
                    <p className="mt-[6px] mb-0 flex max-w-[680px] flex-wrap items-baseline gap-x-[10px] text-[14px] text-[var(--chrome-card-sub)]">
                      {event.flag?.label ? <span>{event.flag.label}</span> : null}
                      <a
                        href={`?gap=${event.event_id}`}
                        onClick={(e) => {
                          e.preventDefault();
                          patch({ gap: event.event_id });
                        }}
                        className="text-[13px] no-underline"
                        style={{
                          color: event.flag
                            ? 'var(--chrome-search-silent)'
                            : 'var(--chrome-card-age)',
                        }}
                      >
                        See what else was nearby →
                      </a>
                    </p>
                  ) : null}
                </div>
              </article>
            ))}
            {story && story.events.length === 0 ? (
              <p className="m-0 text-[14px] text-[var(--chrome-card-sub)]">
                Nothing recorded for her in this window.
              </p>
            ) : null}
          </div>
        </main>

        <aside className="pt-[42px] max-[900px]:pt-[28px]">
          <h2 className="m-0 font-mono text-[10.5px] font-normal tracking-[0.16em] text-[var(--chrome-card-eyebrow)]">
            PARTICULARS
          </h2>
          <Particulars mmsi={mmsi} id={id} />
          <p className="mt-[16px] mb-0 text-[13px] text-[var(--chrome-card-age)] [text-wrap:pretty]">
            {story?.limit_line}
          </p>
          <p className="mt-[10px] mb-0 text-[13px] text-[var(--chrome-card-age)] [text-wrap:pretty]">
            Dashes mean not yet received, not unavailable. The flag is inferred from the MMSI
            prefix, which is why it is the one field that fills in first.
          </p>
          {mmsi ? <RailFoot mmsi={mmsi} /> : null}
        </aside>
      </div>
        )}
      </div>
    </div>
  );
}
