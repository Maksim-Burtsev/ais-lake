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
 *  ☆ (F24) and "Watch her live" are drawn per frame and inert until M6.
 */

import { useEffect, useState } from 'react';

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
}
interface Event {
  event_id: string;
  kind: string;
  t_start: number;
  t_end: number | null;
  prose: string;
  port: { locode: string; name: string | null } | null;
  flag?: { label: string };
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

/** The voyage box: the track's own shape, scaled to fit, north up. */
function Voyage({ mmsi }: { mmsi: number }) {
  const [line, setLine] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    fetch(`/v1/ships/${mmsi}/track`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`track ${r.status}`))))
      .then((body: { geometry: { coordinates: [number, number][] } }) => {
        const pts = body.geometry.coordinates;
        if (!alive || pts.length < 2) return;
        const xs = pts.map((p) => p[0]);
        const ys = pts.map((p) => p[1]);
        const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
        const k = Math.min(880 / (x1 - x0 || 1), 420 / (y1 - y0 || 1)) * 0.86;
        setLine(
          pts
            .map(([x, y]) => `${(440 + (x - (x0 + x1) / 2) * k).toFixed(1)},${(210 - (y - (y0 + y1) / 2) * k).toFixed(1)}`)
            .join(' '),
        );
      })
      .catch((error: unknown) => console.warn('track:', error));
    return () => {
      alive = false;
    };
  }, [mmsi]);

  return (
    <div className="mt-[26px] h-[420px] border border-[var(--chrome-hairline)] max-[900px]:h-[210px]">
      <svg viewBox="0 0 880 420" preserveAspectRatio="xMidYMid meet" className="h-full w-full">
        {line ? (
          <polyline
            points={line}
            fill="none"
            stroke="var(--chrome-search-anchored)"
            strokeWidth={2}
            vectorEffect="non-scaling-stroke"
          />
        ) : (
          <text
            x="440"
            y="210"
            textAnchor="middle"
            className="font-mono text-[11px] tracking-[0.16em]"
            fill="var(--chrome-card-age)"
          >
            VOYAGE MAP
          </text>
        )}
      </svg>
    </div>
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

      <div className="grid grid-cols-[880px_380px] gap-x-[64px] px-[56px] pt-[40px] pb-[56px] max-[900px]:block max-[900px]:px-[20px] max-[900px]:pt-[18px]">
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
          {mmsi ? <Voyage mmsi={mmsi} /> : null}

          <div className="mt-[36px] flex flex-col border-l border-[var(--chrome-hairline)]">
            {(story?.events ?? []).map((event) => (
              <article key={event.event_id} className="flex gap-[20px] pb-[28px]">
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
                  {event.flag?.label ? (
                    <p className="mt-[6px] mb-0 max-w-[680px] text-[14px] text-[var(--chrome-card-sub)] [text-wrap:pretty]">
                      {event.flag.label}
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
        </aside>
      </div>
    </div>
  );
}
