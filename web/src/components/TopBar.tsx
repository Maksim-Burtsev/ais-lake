import { useLiveStore } from '../state/live';
import { REFRESH, useUrlStore } from '../state/url';
import { RegionPicker } from './RegionPicker';
import { ThemeToggle } from './ThemeToggle';

/** S1 top bar — docs/design/"ais-lake MVP Batch 1 Map Shell.dc.html"
 *  :: "First visit" / "First visit · Blue Marble day".
 *  Search is still a visual stub: correct copy and geometry, no behaviour behind
 *  it yet. Refresh (F1) and the region picker (F6) are live. */

const SEARCH_PLACEHOLDER = 'Search a ship, a port, a sea…';
const SIGN_IN_LABEL = 'Sign in';

/** Anonymous is the only tier until sign-in lands (M6); faster cadences are
 *  shown-but-disabled, which is the wall the spec asks for. */
const TIER = 'anon';

function Caret() {
  return (
    <span aria-hidden="true" className="text-[10px] text-[var(--chrome-caret)]">
      ▾
    </span>
  );
}

function SearchStub() {
  return (
    <div className="flex h-[32px] max-w-[360px] flex-1 items-center gap-[9px] border border-[var(--chrome-hairline)] bg-[var(--chrome-search-fill)] px-[11px]">
      <svg width="13" height="13" viewBox="0 0 14 14" className="block flex-none" aria-hidden="true">
        <circle
          cx="5.8"
          cy="5.8"
          r="4.4"
          fill="none"
          stroke="var(--chrome-search-ink)"
          strokeWidth="1.3"
        />
        <line
          x1="9.2"
          y1="9.2"
          x2="12.6"
          y2="12.6"
          stroke="var(--chrome-search-ink)"
          strokeWidth="1.3"
        />
      </svg>
      <span className="text-[13px] text-[var(--chrome-search-placeholder)]">
        {SEARCH_PLACEHOLDER}
      </span>
    </div>
  );
}

/** F1 refresh control: the LIVE dot (socket health), the cadence selector, and
 *  the sweep that runs the cycle. The <select> is the native one, laid
 *  transparent over the pill — keyboard and screen readers for free. */
function Refresh() {
  const interval = useUrlStore((s) => s.interval);
  const patch = useUrlStore((s) => s.patch);
  const status = useLiveStore((s) => s.status);

  return (
    <div className="flex flex-col gap-[5px]" data-live={status}>
      <div className="relative flex h-[26px] items-center gap-[9px] border border-[var(--chrome-hairline-strong)] bg-[var(--chrome-live-fill)] px-[10px]">
        <span
          className="h-[5px] w-[5px] rounded-full bg-[var(--chrome-live-dot)]"
          style={
            status === 'live'
              ? { animation: 'live-dot 2s ease-in-out infinite' }
              : { opacity: status === 'down' ? 0.3 : 0.6 }
          }
        />
        <span className="font-mono text-[10.5px] tracking-[0.16em] text-[var(--chrome-live-ink)]">
          LIVE · {interval}s
        </span>
        <Caret />
        <select
          aria-label="Refresh interval"
          value={interval}
          onChange={(event) => patch({ interval: Number(event.target.value) })}
          className="absolute inset-0 cursor-pointer appearance-none border-0 bg-transparent text-transparent opacity-0"
        >
          {REFRESH.options.map((seconds) => (
            <option key={seconds} value={seconds} disabled={seconds < REFRESH.floor[TIER]}>
              {seconds}s
            </option>
          ))}
        </select>
      </div>
      <div className="h-[2px] overflow-hidden bg-[var(--chrome-sweep-track)]">
        {/* key: restart the animation on a cadence change — that restart IS the
            visible "switch takes effect within one cycle". */}
        <div
          key={interval}
          data-sweep=""
          className="h-[2px] origin-left bg-[var(--chrome-sweep-fill)]"
          style={{ animation: `live-sweep ${interval}s linear infinite` }}
        />
      </div>
    </div>
  );
}

export function TopBar() {
  return (
    <header className="relative z-30 flex h-[var(--bar-h)] items-center gap-[var(--bar-gap)] border-b border-[var(--chrome-hairline)] bg-[var(--chrome-bar-fill)] px-[var(--bar-pad-x)]">
      <div className="font-display text-[21px] font-medium whitespace-nowrap text-[var(--chrome-logo-ink)]">
        ais<span className="text-[var(--chrome-logo-dot)]">·</span>lake
      </div>
      <SearchStub />
      <div className="flex-1" />
      <RegionPicker />
      <Refresh />
      <ThemeToggle />
      <button
        type="button"
        className="cursor-pointer border-0 bg-transparent p-0 text-[13px] text-[var(--chrome-signin-ink)]"
      >
        {SIGN_IN_LABEL}
      </button>
    </header>
  );
}
