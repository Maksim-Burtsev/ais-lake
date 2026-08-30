/** The one piece of arithmetic behind the replay (F14), on its own so a plain
 *  node import can exercise it — no React, no store, no browser.
 */

export interface Gap {
  t_start: number;
  t_end: number;
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
  if (t <= (times[0] ?? 0)) return first;
  if (t >= (times[n - 1] ?? 0)) return last;
  let i = 0;
  while (i + 1 < n && (times[i + 1] ?? 0) <= t) i += 1;
  const a = coords[i];
  const b = coords[i + 1];
  const ta = times[i];
  const tb = times[i + 1];
  if (!a) return null;
  if (gaps.some((g) => t >= g.t_start && t < g.t_end)) return a;
  if (!b || ta === undefined || tb === undefined) return a;
  const span = tb - ta;
  const f = span > 0 ? (t - ta) / span : 0;
  return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f];
}

