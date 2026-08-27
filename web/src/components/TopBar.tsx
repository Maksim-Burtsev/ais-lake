import { useUrlStore } from '../state/url';
import { ThemeToggle } from './ThemeToggle';

/** S1 top bar — docs/design/"ais-lake MVP Batch 1 Map Shell.dc.html"
 *  :: "First visit" / "First visit · Blue Marble day".
 *  Search, region and refresh are visual stubs in M2-T1: correct copy and
 *  geometry, no behaviour behind them yet. */

const SEARCH_PLACEHOLDER = 'Search a ship, a port, a sea…';
const REFRESH_LABEL = 'LIVE · 10s';
const SIGN_IN_LABEL = 'Sign in';

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

function RegionStub({ region }: { region: string }) {
  return (
    <div className="flex items-center gap-[8px] border-r border-[var(--chrome-hairline)] pr-[18px] text-[13px] text-[var(--chrome-region-ink)]">
      {region} <Caret />
    </div>
  );
}

/** F1 refresh control: the LIVE dot, the cadence, and the sweep that runs the
 *  10-second cycle. Static UI here — the real cadence arrives with the socket. */
function RefreshStub() {
  return (
    <div className="flex flex-col gap-[5px]">
      <div className="flex h-[26px] items-center gap-[9px] border border-[var(--chrome-hairline-strong)] bg-[var(--chrome-live-fill)] px-[10px]">
        <span
          className="h-[5px] w-[5px] rounded-full bg-[var(--chrome-live-dot)]"
          style={{ animation: 'live-dot 2s ease-in-out infinite' }}
        />
        <span className="font-mono text-[10.5px] tracking-[0.16em] text-[var(--chrome-live-ink)]">
          {REFRESH_LABEL}
        </span>
        <Caret />
      </div>
      <div className="h-[2px] overflow-hidden bg-[var(--chrome-sweep-track)]">
        <div
          className="h-[2px] origin-left bg-[var(--chrome-sweep-fill)]"
          style={{ animation: 'live-sweep 10s linear infinite' }}
        />
      </div>
    </div>
  );
}

export function TopBar() {
  const region = useUrlStore((s) => s.region);

  return (
    <header className="relative z-30 flex h-[var(--bar-h)] items-center gap-[var(--bar-gap)] border-b border-[var(--chrome-hairline)] bg-[var(--chrome-bar-fill)] px-[var(--bar-pad-x)]">
      <div className="font-display text-[21px] font-medium whitespace-nowrap text-[var(--chrome-logo-ink)]">
        ais<span className="text-[var(--chrome-logo-dot)]">·</span>lake
      </div>
      <SearchStub />
      <div className="flex-1" />
      <RegionStub region={region} />
      <RefreshStub />
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
