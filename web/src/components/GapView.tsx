/** F13 — the silence, opened. docs/design "ais-lake MVP Batch 2 Vessel Page.dc.html"
 *  :: "The silence, opened" (6b).
 *
 *  The timeline holds the words ("Went silent — 26 hours"); this view holds the
 *  numbers. It IS the expander F13 asks for, which is why the server hands every
 *  gap a `numbers` object and not only the flagged ones — an ordinary silence has
 *  to be able to show why it is ordinary.
 *
 *  Evidence over adjective: each claim under WHAT WE CAN SAY is a heading plus the
 *  count it rests on, and anything the detector did not write is simply absent —
 *  no invented denominators (F15).
 *
 *  Gaps, deliberate: the frame's gapMap / voyageGap panels and "Alert me on her
 *  next gap" belong to the replay step and M6; the panel is drawn empty, the
 *  button is not drawn at all.
 */

export interface GapNumbers {
  classification?: string;
  confidence?: number;
  cell_interval_s?: number;
  cell_occupancy?: number;
  cell_ships?: number;
  neighbors_online?: number;
}

export interface GapEvent {
  event_id: string;
  t_start: number;
  t_end: number | null;
  prose: string;
  numbers?: GapNumbers;
  flag?: { label: string };
}

const hhmm = (s: number) => new Date(s * 1000).toISOString().slice(11, 16);
/** "15 Aug 2025", UTC — the frame's "on 15 August", in the stamp's own voice. */
const onDay = (s: number) => new Date(s * 1000).toUTCString().slice(5, 16);

/** "Went silent — 26 hours" → "26 hours off the air". The server already
 *  humanised the duration; this only re-points the sentence at the reader. */
export const headline = (prose: string): string => {
  const tail = prose.split(' — ')[1];
  if (!tail) return 'Off the air';
  return `${tail.charAt(0).toUpperCase()}${tail.slice(1)} off the air`;
};

interface Claim {
  head: string;
  body: string;
}

/** The three blocks of frame 6b, built from whatever the detector actually wrote. */
export function claims(n: GapNumbers): Claim[] {
  const out: Claim[] = [];
  if (n.neighbors_online !== undefined)
    out.push({
      head: 'Coverage was fine',
      body: `${n.neighbors_online} ships nearby kept reporting through the silence. This is not a receiver outage.`,
    });
  if (n.cell_ships !== undefined)
    out.push({
      head: 'This is a busy stretch of water',
      body:
        `${n.cell_ships} ships pass through here` +
        (n.cell_interval_s !== undefined
          ? `, and a report normally arrives every ${Math.round(n.cell_interval_s / 60)} minutes.`
          : '.'),
    });
  if (n.classification !== undefined)
    out.push({
      head: n.classification === 'unusual' ? 'It stands out here' : 'It happens here',
      body:
        n.classification === 'unusual'
          ? 'A gap this long is rare for this patch of sea.'
          : 'Silences of this length are ordinary in this patch of sea.',
    });
  return out;
}

export function GapView({ event, ship, onBack }: { event: GapEvent; ship: string; onBack: () => void }) {
  const numbers = event.numbers ?? {};
  const blocks = claims(numbers);
  return (
    <div className="grid grid-cols-[880px_380px] gap-x-[64px] max-[900px]:block">
      <main>
        <p className="m-0 font-mono text-[11px] tracking-[0.16em] text-[var(--chrome-search-silent)]">
          {ship.toUpperCase()} → THE GAP
        </p>
        <h2 className="mt-[14px] mb-0 font-display text-[38px] leading-[1.1] font-medium text-[var(--chrome-card-name)] max-[900px]:text-[24px]">
          {headline(event.prose)}
        </h2>
        <p className="mt-[12px] mb-0 max-w-[760px] text-[16px] leading-[1.6] [text-wrap:pretty]">
          Between{' '}
          <span className="font-mono text-[14px] text-[var(--chrome-search-silent)]">
            {hhmm(event.t_start)}
          </span>{' '}
          and{' '}
          <span className="font-mono text-[14px] text-[var(--chrome-search-silent)]">
            {event.t_end ? hhmm(event.t_end) : 'now'}
          </span>{' '}
          on {onDay(event.t_start)} she sent nothing.
          {numbers.neighbors_online ? ' Everything around her kept transmitting.' : ''}
        </p>
        <div className="mt-[24px] h-[380px] border border-[var(--chrome-hairline)] max-[900px]:h-[190px]" />
        <p className="mt-[10px] mb-0 font-mono text-[10.5px] tracking-[0.1em] text-[var(--chrome-card-age)]">
          THE GAP IN THE CONTEXT OF THE WHOLE VOYAGE
        </p>
        <button
          type="button"
          onClick={onBack}
          className="mt-[26px] cursor-pointer border-0 bg-transparent p-0 font-mono text-[11px] tracking-[0.14em] text-[var(--chrome-card-age)]"
        >
          ← BACK TO THE TIMELINE
        </button>
      </main>

      <aside className="pt-[42px] max-[900px]:pt-[28px]">
        <h3 className="m-0 font-mono text-[10.5px] font-normal tracking-[0.16em] text-[var(--chrome-search-silent)]">
          WHAT WE CAN SAY
        </h3>
        <div className="mt-[16px] flex flex-col gap-[16px]">
          {blocks.length ? (
            blocks.map((claim) => (
              <div key={claim.head}>
                <div className="text-[14.5px] font-semibold text-[var(--chrome-card-name)]">
                  {claim.head}
                </div>
                <p className="mt-[5px] mb-0 text-[13.5px] leading-[1.6] text-[var(--chrome-card-sub)] [text-wrap:pretty]">
                  {claim.body}
                </p>
              </div>
            ))
          ) : (
            <p className="m-0 text-[13.5px] leading-[1.6] text-[var(--chrome-card-sub)]">
              Nothing was measured around this silence — only that it happened.
            </p>
          )}
        </div>
        <div className="my-[24px] h-px bg-[var(--chrome-hairline)]" />
        <h3 className="m-0 font-mono text-[10.5px] font-normal tracking-[0.16em] text-[var(--chrome-card-age)]">
          WHAT WE CANNOT
        </h3>
        <p className="mt-[10px] mb-0 text-[13.5px] leading-[1.6] text-[var(--chrome-card-sub)] [text-wrap:pretty]">
          AIS silence has ordinary explanations — a powered-down transponder at anchor is the
          commonest. We show the gap and the context. We do not label it.
        </p>
        <p className="mt-[18px] mb-0 text-[12.5px] text-[var(--chrome-card-age)]">
          How we judge silence — method notes coming with the docs.
        </p>
      </aside>
    </div>
  );
}
