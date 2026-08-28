/** Rung-2 vessel sprites (SYMBOLOGY.md §1, §2, §4).
 *
 *  The hull curves are ported verbatim from `hullD()` / `lozD()` / `detail()` in
 *  docs/design/"ais-lake Vessel Symbology v2.dc.html" — same strings, handed to
 *  Path2D instead of an <svg>. Everything is drawn white: the sprites go into the
 *  atlas as SDF-flagged alpha and `icon-color` tints them per state at paint time,
 *  which is what lets the grey-proof render exist at all.
 *
 *  Every cell is drawn at the length it will be painted at, and `icon-size` is 1:
 *  the beam floor and the 17-px detail gate are rules about RENDERED pixels, so a
 *  single nominal-length image scaled down by MapLibre applied both of them at the
 *  wrong size — a 14-px tanker came out 2.0 px in the beam instead of 2.6 and still
 *  carried a deck hint. One image per (class, state, step) is what makes §5's
 *  14-px test pass.
 *
 *  ponytail: runtime canvas generation, not the 1024x768 PNG atlas the design
 *  calls for — same curves, one build step less. And a solid alpha channel flagged
 *  sdf:true is not a real distance field: fine at these sizes, but if hull edges
 *  read soft at rung 2, the upgrade is a proper SDF build script feeding addImage.
 */

export const STEP = [10, 14, 18, 23, 28] as const;
export const LOZ = [7, 9, 11] as const;

/** `steps` is the class's [first, last] length step — the browser half of
 *  CLASS_STEPS in pipeline/ais_pipeline/refinery/symbology.py, which is the
 *  authority. Every range there is contiguous, so a pair clamps as well as a set,
 *  and the empty matrix cells stay empty: no 280 m fishing boat is ever drawn. */
export const CMETA: Record<string, { lb: number; steps: readonly [number, number] }> = {
  tanker: { lb: 7, steps: [2, 5] },
  cargo: { lb: 6, steps: [2, 5] },
  ferry: { lb: 5, steps: [1, 4] },
  fishing: { lb: 3, steps: [1, 2] },
  tug: { lb: 2.5, steps: [1, 2] },
  hsc: { lb: 8, steps: [1, 3] },
  pleasure: { lb: 4.5, steps: [1, 1] },
  unknown: { lb: 4, steps: [1, 5] },
};
export const CLASSES = Object.keys(CMETA);
export const STATES = ['anchored', 'moored', 'silent', 'selected'] as const;

/** Rung 1 has one lozenge for the whole fleet and `icon-size` still scales it:
 *  class is deliberately unreadable there, so the erosion buys nothing back. */
export const NOMINAL_L = 28;
const PIXEL_RATIO = 2;

/** Wake length comes from speed alone; three buckets, drawn at their midpoints. */
const SOG_MIDPOINT = [2, 7, 14];
export const sogBucket = (sog: number): number => (sog < 4 ? 0 : sog <= 10 ? 1 : 2);
const wakeLen = (L: number, sog: number): number => Math.min(L * 3, sog * 2.2);

/** `"tanker4"` + state -> the icon this ship draws with, the class it belongs to
 *  (F7 filters match on it) and the class+step key the selected-ship expression
 *  rebuilds its own icon name from. A token we cannot read falls back to the
 *  unknown capsule; a step outside the class's own range is clamped into it, the
 *  same way symbology.py clamps, so the key always exists in the atlas. */
export function iconOf(
  sym: string,
  state: string,
  sog: number,
): { icon: string; cls: string; sym: string } {
  const match = /^([a-z]+)([1-5])$/.exec(sym);
  const cls = match && CMETA[match[1]!] ? match[1]! : 'unknown';
  const [lo, hi] = CMETA[cls]!.steps;
  const step = match && CMETA[match[1]!] ? Math.min(Math.max(Number(match[2]), lo), hi) : 2;
  const known = (STATES as readonly string[]).includes(state);
  const key = `${cls}${step}`;
  return {
    icon: known ? `${key}-${state}` : `${key}-u${sogBucket(sog)}`,
    cls,
    sym: key,
  };
}

export function hullD(cls: string, L: number, B: number): string {
  const l = L / 2, b = B / 2;
  if (cls === 'tanker') { // bluff bow, slab sides, square stern
    return 'M0,' + (-l) + ' C' + (b * .85) + ',' + (-l) + ' ' + b + ',' + (-l + L * .07) + ' ' + b + ',' + (-l + L * .13) +
      ' L' + b + ',' + (l - b * .7) + ' Q' + b + ',' + l + ' ' + (b * .6) + ',' + l +
      ' L' + (-b * .6) + ',' + l + ' Q' + (-b) + ',' + l + ' ' + (-b) + ',' + (l - b * .7) +
      ' L' + (-b) + ',' + (-l + L * .13) + ' C' + (-b) + ',' + (-l + L * .07) + ' ' + (-b * .85) + ',' + (-l) + ' 0,' + (-l) + ' Z';
  }
  if (cls === 'cargo') { // flared bow, slight taper to transom
    return 'M0,' + (-l) + ' C' + (b * .5) + ',' + (-l + L * .05) + ' ' + (b * .95) + ',' + (-l + L * .12) + ' ' + b + ',' + (-l + L * .2) +
      ' L' + (b * .84) + ',' + (l - 1.2) + ' L' + (b * .8) + ',' + l +
      ' L' + (-b * .8) + ',' + l + ' L' + (-b * .84) + ',' + (l - 1.2) +
      ' L' + (-b) + ',' + (-l + L * .2) + ' C' + (-b * .95) + ',' + (-l + L * .12) + ' ' + (-b * .5) + ',' + (-l + L * .05) + ' 0,' + (-l) + ' Z';
  }
  if (cls === 'ferry') { // fuller, rounded stern
    return 'M0,' + (-l) + ' C' + (b * .6) + ',' + (-l + L * .08) + ' ' + b + ',' + (-l + L * .16) + ' ' + b + ',' + (-l + L * .24) +
      ' L' + b + ',' + (l - b) + ' Q' + b + ',' + l + ' ' + (b * .4) + ',' + l +
      ' L' + (-b * .4) + ',' + l + ' Q' + (-b) + ',' + l + ' ' + (-b) + ',' + (l - b) +
      ' L' + (-b) + ',' + (-l + L * .24) + ' C' + (-b) + ',' + (-l + L * .16) + ' ' + (-b * .6) + ',' + (-l + L * .08) + ' 0,' + (-l) + ' Z';
  }
  if (cls === 'fishing') { // squat, rounded bow, full transom
    return 'M0,' + (-l) + ' C' + (b * .92) + ',' + (-l + L * .2) + ' ' + b + ',' + (-l + L * .34) + ' ' + b + ',' + (-l + L * .42) +
      ' L' + b + ',' + l + ' L' + (-b) + ',' + l +
      ' L' + (-b) + ',' + (-l + L * .42) + ' C' + (-b) + ',' + (-l + L * .34) + ' ' + (-b * .92) + ',' + (-l + L * .2) + ' 0,' + (-l) + ' Z';
  }
  if (cls === 'tug') { // wedge: blunt fendered bow, taper aft
    return 'M0,' + (-l) + ' C' + (b * .9) + ',' + (-l + L * .05) + ' ' + b + ',' + (-l + L * .12) + ' ' + b + ',' + (-l + L * .2) +
      ' L' + (b * .58) + ',' + (l - 1.2) + ' Q' + (b * .52) + ',' + l + ' ' + (b * .38) + ',' + l +
      ' L' + (-b * .38) + ',' + l + ' Q' + (-b * .52) + ',' + l + ' ' + (-b * .58) + ',' + (l - 1.2) +
      ' L' + (-b) + ',' + (-l + L * .2) + ' C' + (-b) + ',' + (-l + L * .12) + ' ' + (-b * .9) + ',' + (-l + L * .05) + ' 0,' + (-l) + ' Z';
  }
  if (cls === 'hsc') { // needle, fine wedge entry
    return 'M0,' + (-l) + ' L' + b + ',' + (-l + L * .5) + ' L' + b + ',' + l + ' L' + (-b) + ',' + l + ' L' + (-b) + ',' + (-l + L * .5) + ' Z';
  }
  if (cls === 'pleasure') {
    return 'M0,' + (-l) + ' C' + b + ',' + (-l + L * .3) + ' ' + b + ',' + (l - L * .26) + ' 0,' + l +
      ' C' + (-b) + ',' + (l - L * .26) + ' ' + (-b) + ',' + (-l + L * .3) + ' 0,' + (-l) + ' Z';
  }
  return 'M' + (-b) + ',' + (-l + b) + ' A' + b + ',' + b + ' 0 0 1 ' + b + ',' + (-l + b) + ' L' + b + ',' + (l - b) + ' A' + b + ',' + b + ' 0 0 1 ' + (-b) + ',' + (l - b) + ' Z';
}

export function lozD(L: number, B: number): string {
  const l = L / 2, b = B / 2;
  return 'M0,' + (-l) + ' L' + b + ',' + (-l + L * .34) + ' L' + b + ',' + (l - b) + ' A' + b + ',' + b + ' 0 0 1 ' + (-b) + ',' + (l - b) + ' L' + (-b) + ',' + (-l + L * .34) + ' Z';
}

/** The single deck hint of rung 2 (`detail()` at lvl 2). Drawn as an alpha cutout,
 *  so the hint reads as a hole in the tinted hull rather than a second colour —
 *  one channel is all the SDF atlas has. Below 17 rendered px the outline does the
 *  work alone (the frame's §3a rule), which now fires because `L` is the step. */
function detail(ctx: CanvasRenderingContext2D, cls: string, L: number, B: number): void {
  const l = L / 2, b = B / 2;
  const R = (x: number, y: number, w: number, h: number, rx = 0) => {
    ctx.beginPath();
    ctx.roundRect(x, y, w, h, rx);
    ctx.fill();
  };
  const Ln = (x1: number, y1: number, x2: number, y2: number, w = Math.max(.6, B * .06)) => {
    ctx.lineWidth = w;
    ctx.beginPath();
    ctx.moveTo(x1, y1);
    ctx.lineTo(x2, y2);
    ctx.stroke();
  };
  if (L < 17) return;
  if (cls === 'tanker') R(-b * .6, l - L * .17, b * 1.2, L * .11); // house far aft
  else if (cls === 'cargo') { // divisions + house aft
    R(-b * .58, l - L * .16, b * 1.16, L * .1);
    Ln(-b * .62, -l + L * .36, b * .62, -l + L * .36);
    Ln(-b * .62, -l + L * .58, b * .62, -l + L * .58);
  } else if (cls === 'ferry') R(-b * .56, -l + L * .18, b * 1.12, L * .6, b * .3);
  else if (cls === 'fishing') R(-b * .48, -l + L * .28, b * .96, L * .22);
  else if (cls === 'tug') R(-b * .56, -l + L * .14, b * 1.12, L * .3, b * .18);
  else if (cls === 'hsc') Ln(0, -l + L * .12, 0, l - L * .12);
  // pleasure carries nothing on deck at lvl 2
}

/** One cell. `state` is the five of SYMBOLOGY.md §2; each keeps its own shape cue,
 *  because colour is the first thing a deuteranope loses. */
function draw(
  ctx: CanvasRenderingContext2D,
  cls: string,
  L: number,
  state: string,
  sog: number,
): void {
  // the frame's own floor, at the size it draws: max(2.6, px / lb). A 14-px tanker
  // is 2.6 px in the beam, not 14/7 = 2.0 — thin classes are exactly the ones that
  // stop being nameable when the floor is applied to some other length.
  const B = Math.max(2.6, L / (cls === 'loz' ? 2.8 : CMETA[cls]!.lb));
  const l = L / 2;
  const hull = new Path2D(cls === 'loz' ? lozD(L, B) : hullD(cls, L, B));

  ctx.fillStyle = '#fff';
  ctx.strokeStyle = '#fff';
  ctx.lineCap = 'butt';

  if (state === 'underway') { // the wake: a ribbon astern, length from speed alone
    const wl = wakeLen(L, sog);
    ctx.beginPath();
    ctx.moveTo(-B * .38, l);
    ctx.lineTo(B * .38, l);
    ctx.lineTo(B * .13, l + wl);
    ctx.lineTo(-B * .13, l + wl);
    ctx.closePath();
    ctx.fill();
  }
  if (state === 'anchored') { // the swing ring, and the chain to it
    const r = l + Math.max(4, L * .3);
    ctx.setLineDash([1.6, 3.2]);
    ctx.lineWidth = .8;
    ctx.beginPath();
    ctx.arc(0, 0, r, 0, Math.PI * 2);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.lineWidth = .7;
    ctx.beginPath();
    ctx.moveTo(0, -l);
    ctx.lineTo(0, -r);
    ctx.stroke();
  }
  if (state === 'moored') { // the berth line alongside
    ctx.lineWidth = 1.1;
    ctx.beginPath();
    ctx.moveTo(-B * .9, -l * .9);
    ctx.lineTo(-B * .9, l * .9);
    ctx.stroke();
  }
  if (state === 'silent') { // the double-ring pulse
    for (const [r, w] of [[l + 7, 1.2], [l + 14, .7]] as const) {
      ctx.lineWidth = w;
      ctx.beginPath();
      ctx.arc(0, 0, r, 0, Math.PI * 2);
      ctx.stroke();
    }
  }
  if (state === 'selected') { // the double halo
    for (const [r, w] of [[l + 12, 1.5], [l + 21, .8]] as const) {
      ctx.lineWidth = w;
      ctx.beginPath();
      ctx.arc(0, 0, r, 0, Math.PI * 2);
      ctx.stroke();
    }
  }

  if (state === 'silent') {
    // ghost: outline only, dashed. The empty hull IS the cue that survives grey.
    ctx.setLineDash([2.6, 2]);
    ctx.lineWidth = Math.max(.7, L * .035);
    ctx.stroke(hull);
    ctx.setLineDash([]);
    return;
  }
  ctx.fill(hull);
  ctx.lineWidth = Math.max(.7, L * .035);
  ctx.stroke(hull);
  if (cls === 'loz') return;
  ctx.globalCompositeOperation = 'destination-out';
  detail(ctx, cls, L, B);
  ctx.globalCompositeOperation = 'source-over';
}

/** Half a cell, in CSS px. Sized to what THIS cell draws, not to the worst case in
 *  the atlas: the selected halo reaches l + 21, the silent pulse l + 14, the swing
 *  ring l + max(4, .3L), a wake runs its own length astern, and a moored or bare
 *  hull needs nothing past the outline. + 2 covers the widest stroke. Sizing every
 *  cell to the largest of those costs ~2.5x the bytes, all of them transparent. */
function halfOf(L: number, state: string, sog: number): number {
  const reach =
    state === 'underway' ? wakeLen(L, sog)
    : state === 'selected' ? 21
    : state === 'silent' ? 14
    : state === 'anchored' ? Math.max(4, L * 0.3)
    : 0;
  return Math.ceil(L / 2 + reach + 2);
}

/** Every sprite the map needs: one per (class, step, variant) over the 25 class ×
 *  step cells the matrix allows (of 40 — the empty ones are honest), variants being
 *  the 3 wake buckets + 4 states, plus the bare fill §5's acceptance stand prints
 *  and the rung-1 lozenge. 201 images, 7.7 MB of ImageData, ~7 ms to build.
 *  Keys are the `icon` property MapCanvas puts on each feature. */
export function sprites(): Record<string, ImageData> {
  const canvas = document.createElement('canvas');
  const ctx = canvas.getContext('2d', { willReadFrequently: true })!;
  const out: Record<string, ImageData> = {};
  const cell = (key: string, cls: string, L: number, state: string, sog = 0) => {
    const half = halfOf(L, state, sog);
    const size = half * 2 * PIXEL_RATIO;
    canvas.width = canvas.height = size; // resizing clears; no clearRect needed
    ctx.setTransform(PIXEL_RATIO, 0, 0, PIXEL_RATIO, half * PIXEL_RATIO, half * PIXEL_RATIO);
    draw(ctx, cls, L, state, sog);
    out[key] = ctx.getImageData(0, 0, size, size);
  };
  for (const cls of CLASSES) {
    const [lo, hi] = CMETA[cls]!.steps;
    for (let step = lo; step <= hi; step++) {
      const L = STEP[step - 1]!;
      SOG_MIDPOINT.forEach((sog, i) => cell(`${cls}${step}-u${i}`, cls, L, 'underway', sog));
      for (const state of STATES) cell(`${cls}${step}-${state}`, cls, L, state);
      // no state cue, no wake — the flat fill the 14-px test is judged on. Nothing
      // in the running map asks for it; the acceptance spec does.
      cell(`${cls}${step}-plain`, cls, L, 'plain');
    }
  }
  cell('loz', 'loz', NOMINAL_L, 'plain');
  return out;
}

export const SPRITE_PIXEL_RATIO = PIXEL_RATIO;
