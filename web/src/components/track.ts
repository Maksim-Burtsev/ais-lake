/** The one piece of arithmetic behind the replay (F14), on its own so a plain
 *  node import can exercise it — no React, no store, no browser.
 */

export interface Gap {
  t_start: number;
  /** null while the silence is still open — she has not come back yet. */
  t_end: number | null;
}
export type Point = [lon: number, lat: number];

/** Where she was at `t`, or the truth that we do not know: inside a gap the last
 *  real fix stands until the gap closes. Linear between fixes, clamped at both
 *  ends. */
export function positionAt(
  coords: readonly Point[],
  times: readonly number[],
  gaps: readonly Gap[],
  t: number,
): Point | null {
  const n = Math.min(coords.length, times.length);
  const first = coords[0];
  const last = coords[n - 1];
  if (n === 0 || !first || !last) return null;
  /** The fix at or before `at` — where she last actually was. */
  const fixAt = (at: number): number => {
    let k = 0;
    while (k + 1 < n && (times[k + 1] ?? 0) <= at) k += 1;
    return k;
  };
  // An open gap (t_end null) never closes: from t_start onward she stands at the
  // last real fix before the silence, and we never interpolate past it — even if
  // the array happens to carry later points. Checked before the tail clamp for
  // exactly that reason.
  const silence = gaps.find((g) => t >= g.t_start && (g.t_end === null || t < g.t_end));
  if (silence) return coords[fixAt(silence.t_start)] ?? null;
  if (t <= (times[0] ?? 0)) return first;
  if (t >= (times[n - 1] ?? 0)) return last;
  const i = fixAt(t);
  const a = coords[i];
  const b = coords[i + 1];
  const ta = times[i];
  const tb = times[i + 1];
  if (!a) return null;
  if (!b || ta === undefined || tb === undefined) return a;
  const span = tb - ta;
  const f = span > 0 ? (t - ta) / span : 0;
  return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f];
}

