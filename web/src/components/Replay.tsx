/** F14 — voyage replay, docs/design "…Vessel Page.dc.html" :: 6c "Replay running"
 *  (the scrubber row under the voyage box) and 6f (the same row, one column).
 *
 *  Same track data as the static box it replaces: /v1/ships/{mmsi}/track, one
 *  GeoJSON Feature whose properties.times run parallel to the coordinates, plus
 *  the gaps the detector found. Still an SVG, not a second MapLibre canvas — the
 *  box is a fixed diagram of one passage, nothing pans, and a map instance would
 *  buy no interaction.
 *
 *  Honesty rule for gaps: inside one we hold the hull at the last real fix and
 *  draw the crossing dashed. We do not know where she was; we will not draw a
 *  guess and animate it.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { positionAt, type Gap, type Point } from './track';
import { useUrlStore } from '../state/url';
import { age } from './ShipCard';

/** The whole run takes 15 s at 1× (F14); the buttons multiply that. */
const RUNTIME_MS = 15_000;
const SPEEDS = [1, 2, 4] as const;

/** "15 AUG 07:34", UTC — the frame's replay clock. */
const clock = (t: number): string => {
  const d = new Date(t * 1000);
  return `${d.toUTCString().slice(5, 11).toUpperCase()} ${d.toISOString().slice(11, 16)}`;
};

interface Track {
  coords: Point[];
  times: number[];
  gaps: Gap[];
}


/** Longitude/latitude → the 880×420 box, north up, scaled to fit. */
const project = (coords: readonly Point[]) => {
  const xs = coords.map((p) => p[0]);
  const ys = coords.map((p) => p[1]);
  const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
  const k = Math.min(880 / (x1 - x0 || 1), 420 / (y1 - y0 || 1)) * 0.86;
  return (p: Point): Point => [
    440 + (p[0] - (x0 + x1) / 2) * k,
    210 - (p[1] - (y0 + y1) / 2) * k,
  ];
};

export interface Segment {
  kind: string;
  t_start: number;
  t_end: number | null;
  color: string;
}

export function Replay({
  mmsi,
  segments,
  latestTs,
  onTime,
}: {
  mmsi: number;
  segments: Segment[];
  latestTs?: number | null;
  onTime: (t: number | null) => void;
}) {
  const [track, setTrack] = useState<Track | null>(null);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState<(typeof SPEEDS)[number]>(1);
  const [scrubbed, setT] = useState<number | null>(null);
  const deepLink = useUrlStore((s) => s.t);
  const patch = useUrlStore((s) => s.patch);
  const bar = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let alive = true;
    fetch(`/v1/ships/${mmsi}/track`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`track ${r.status}`))))
      .then(
        (body: {
          geometry: { coordinates: Point[] };
          properties?: { times?: number[] };
          gaps?: Gap[];
        }) => {
          if (!alive) return;
          const coords = body.geometry.coordinates ?? [];
          if (coords.length < 2) return;
          setTrack({ coords, times: body.properties?.times ?? [], gaps: body.gaps ?? [] });
        },
      )
      .catch((error: unknown) => console.warn('track:', error));
    return () => {
      alive = false;
    };
  }, [mmsi]);

  const [t0, t1]: [number, number] = track?.times.length
    ? [track.times[0] ?? 0, track.times[track.times.length - 1] ?? 0]
    : [0, 0];

  // A shared ?t= lands paused on that moment; without one the replay sits at the
  // end of the passage, which is the page's own resting state. Derived, not
  // seeded by an effect: until someone scrubs there is nothing to remember.
  const t: number | null = scrubbed ?? (t1 ? Math.min(t1, Math.max(t0, deepLink ?? t1)) : null);

  // One write per ~500 ms, replaceState — playing must not spam the address bar.
  const wrote = useRef(0);
  const publish = useCallback(
    (value: number, force = false) => {
      const now = Date.now();
      if (!force && now - wrote.current < 500) return;
      wrote.current = now;
      patch({ t: Math.round(value) });
    },
    [patch],
  );

  useEffect(() => {
    if (!playing || !t1) return;
    let raf = 0;
    let last = performance.now();
    const step = (now: number) => {
      const rate = ((t1 - t0) / RUNTIME_MS) * speed;
      const next = (t ?? t0) + (now - last) * rate;
      last = now;
      if (next >= t1) {
        setT(t1);
        setPlaying(false);
        publish(t1, true);
        return;
      }
      setT(next);
      publish(next);
      raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
    // `t` is read through the closure each frame; re-subscribing on it is the point.
  }, [playing, speed, t, t0, t1, publish]);

  useEffect(() => {
    onTime(playing || (t !== null && t < t1) ? t : null);
  }, [t, t1, playing, onTime]);

  const to = useMemo(() => (track ? project(track.coords) : null), [track]);
  const points = useMemo(
    () => (track && to ? track.coords.map(to) : []),
    [track, to],
  );
  const hull = track && to && t !== null ? positionAt(track.coords, track.times, track.gaps, t) : null;

  /** The passage so far, drawn lit; a gap crossed inside it stays dashed. */
  const sailed = useMemo(() => {
    if (!track || t === null) return [];
    return points.filter((_, i) => (track.times[i] ?? Infinity) <= t);
  }, [points, track, t]);

  const gapLines = useMemo(() => {
    if (!track || !to) return [];
    return track.gaps
      .map((g) => {
        const a = positionAt(track.coords, track.times, track.gaps, g.t_start);
        // An open gap has no far end yet: dash it to the last point we have, so
        // the silence runs to the edge of what we know instead of back to the
        // voyage's first fix (which is what a null timestamp used to draw).
        const b =
          g.t_end === null
            ? (track.coords[track.coords.length - 1] ?? null)
            : positionAt(track.coords, track.times, track.gaps, g.t_end);
        return a && b ? { g, a: to(a), b: to(b) } : null;
      })
      .filter((v): v is NonNullable<typeof v> => v !== null);
  }, [track, to]);

  const frac = t !== null && t1 > t0 ? (t - t0) / (t1 - t0) : 1;

  // force=false on a drag: one replaceState per pointermove trips Safari's
  // 100-calls-per-30-s limit. The final position is published on pointerup.
  const scrub = (clientX: number, force = false) => {
    const box = bar.current?.getBoundingClientRect();
    if (!box || !t1) return;
    const f = Math.min(1, Math.max(0, (clientX - box.left) / box.width));
    const value = t0 + f * (t1 - t0);
    setPlaying(false);
    setT(value);
    publish(value, force);
  };

  const dragging = useRef(false);
  useEffect(() => {
    const move = (e: PointerEvent) => dragging.current && scrub(e.clientX);
    const up = (e: PointerEvent) => {
      if (dragging.current) scrub(e.clientX, true);
      dragging.current = false;
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
    return () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
    };
  });

  // The live strip: she is as recent as her last fix, counted honestly. A silent
  // ship shows her silence — "AIS 6h AGO" is the sentence, not an absence.
  const [now, setNow] = useState(() => Date.now());
  const [fix, setFix] = useState<number | null>(latestTs ?? null);
  useEffect(() => {
    const tick = window.setInterval(() => setNow(Date.now()), 1000);
    const poll = window.setInterval(() => {
      fetch(`/v1/ships/${mmsi}`)
        .then((r) => (r.ok ? r.json() : null))
        .then((body: { latest?: { ts: number } | null } | null) => {
          if (body?.latest) setFix(body.latest.ts);
        })
        .catch(() => undefined);
    }, 10_000);
    return () => {
      window.clearInterval(tick);
      window.clearInterval(poll);
    };
  }, [mmsi]);

  const replaying = t !== null && (playing || t < t1);

  return (
    <div className="mt-[26px]">
      {/* 6c: while she is replaying, the live badge steps aside for the clock */}
      <div
        data-testid="live-strip"
        className="mb-[8px] flex items-center gap-[8px] font-mono text-[10.5px] tracking-[0.16em]"
        style={{ color: replaying ? 'var(--chrome-search-underway)' : 'var(--chrome-card-age)' }}
      >
        {replaying ? (
          <span data-testid="replay-clock">REPLAY · {clock(t)}</span>
        ) : (
          <>
            <span
              className="h-[5px] w-[5px] rounded-full"
              style={{ background: 'var(--chrome-search-underway)' }}
            />
            <span>{fix ? `AIS ${age(now - fix * 1000)} AGO` : 'AIS —'}</span>
          </>
        )}
      </div>

      <div className="h-[420px] border border-[var(--chrome-hairline)] max-[900px]:h-[210px]">
        <svg viewBox="0 0 880 420" preserveAspectRatio="xMidYMid meet" className="h-full w-full">
          {points.length > 1 ? (
            <>
              <polyline
                points={points.map((p) => `${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(' ')}
                fill="none"
                stroke="var(--chrome-search-anchored)"
                strokeWidth={2}
                strokeOpacity={0.35}
                vectorEffect="non-scaling-stroke"
              />
              {sailed.length > 1 ? (
                <polyline
                  data-testid="replay-sailed"
                  points={sailed.map((p) => `${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(' ')}
                  fill="none"
                  stroke="var(--chrome-search-anchored)"
                  strokeWidth={2}
                  vectorEffect="non-scaling-stroke"
                />
              ) : null}
              {gapLines.map(({ g, a, b }) => (
                <line
                  key={g.t_start}
                  x1={a[0]}
                  y1={a[1]}
                  x2={b[0]}
                  y2={b[1]}
                  stroke="var(--chrome-search-silent)"
                  strokeWidth={2}
                  strokeDasharray="5 5"
                  vectorEffect="non-scaling-stroke"
                />
              ))}
              {hull && to ? (
                <circle
                  data-testid="replay-hull"
                  cx={to(hull)[0]}
                  cy={to(hull)[1]}
                  r={5}
                  fill="var(--chrome-search-underway)"
                />
              ) : null}
            </>
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

      {/* 6c scrubber row: the button, the coloured legs, the playhead, the ticks */}
      <div className="mt-[14px] flex items-center gap-[14px]">
        <button
          type="button"
          aria-label={playing ? 'Pause replay' : 'Play replay'}
          data-testid="replay-play"
          onClick={() => {
            if (!playing && t !== null && t >= t1) setT(t0);
            setPlaying(!playing);
          }}
          className="flex h-[38px] w-[38px] flex-none items-center justify-center border bg-transparent text-[13px] max-[900px]:h-[34px] max-[900px]:w-[34px] max-[900px]:text-[12px]"
          style={{
            borderColor: playing ? 'var(--chrome-search-underway)' : 'var(--chrome-hairline)',
            color: 'var(--chrome-search-underway)',
          }}
        >
          {playing ? '❙❙' : '▶'}
        </button>
        <div className="flex flex-1 flex-col gap-[6px]">
          <div
            ref={bar}
            data-testid="replay-bar"
            className="relative h-[10px] cursor-pointer bg-[var(--chrome-hairline)]"
            onPointerDown={(e) => {
              dragging.current = true;
              scrub(e.clientX);
            }}
          >
            {t1 > t0
              ? segments.map((s) => {
                  const left = ((s.t_start - t0) / (t1 - t0)) * 100;
                  const width = (((s.t_end ?? s.t_start) - s.t_start) / (t1 - t0)) * 100;
                  return (
                    <div
                      key={`${s.kind}-${s.t_start}`}
                      className="absolute top-0 h-full"
                      style={{
                        left: `${Math.max(0, Math.min(100, left))}%`,
                        width: `${Math.max(1.5, Math.min(100, width))}%`,
                        background: s.color,
                        opacity: s.kind === 'gap' ? 0.75 : 1,
                      }}
                    />
                  );
                })
              : null}
          </div>
          <div className="relative h-0">
            <div
              data-testid="replay-playhead"
              className="absolute top-[-19px] h-[16px] w-[2px] bg-[var(--chrome-card-name)]"
              style={{ left: `${(frac * 100).toFixed(2)}%` }}
            />
          </div>
          <div className="flex justify-between font-mono text-[10.5px] tracking-[0.08em] text-[var(--chrome-card-age)] max-[900px]:text-[9.5px]">
            {/* 6c spells the start out; 6f has room for the day and nothing else */}
            <span className="max-[900px]:hidden">{t1 ? clock(t0) : '—'}</span>
            <span className="hidden max-[900px]:inline">{t1 ? clock(t0).slice(0, 6) : '—'}</span>
            <span className="max-[900px]:hidden">{t1 ? clock((t0 + t1) / 2) : ''}</span>
            <span style={{ color: 'var(--chrome-search-underway)' }}>
              {playing ? `PLAYING · ${speed}×` : replaying ? `REPLAY · ${clock(t)}` : 'NOW'}
            </span>
          </div>
        </div>
        <div className="flex flex-none gap-[8px] max-[900px]:hidden">
          {SPEEDS.map((s) => (
            <button
              key={s}
              type="button"
              data-testid={`replay-speed-${s}`}
              onClick={() => setSpeed(s)}
              className="flex h-[34px] items-center border bg-transparent px-[14px] text-[13px]"
              style={{
                borderColor: s === speed ? 'var(--chrome-search-underway)' : 'var(--chrome-hairline)',
                color: s === speed ? 'var(--chrome-search-underway)' : 'var(--chrome-card-age)',
              }}
            >
              {s}×
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
