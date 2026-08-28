/**
 * gen-tokens.mjs — turns the design tokens into CSS custom properties.
 *
 * SOURCE OF TRUTH: docs/design/tokens.json (repo root).
 * web/src/theme/tokens.json is a verbatim copy of it, kept in web/ so the
 * frontend build never reaches outside its own package. Never hand-edit either
 * copy to "fix" a colour: change the approved frames first, then tokens.json,
 * then re-copy. Regenerate with `npm run tokens` (dev/build/check do it for you).
 *
 * Also copies the repo-root limits.json to web/src/limits.json, same reason and
 * same rule: the limits table (F27) has ONE source of truth and the UI reads a
 * copy of it, never its own numbers.
 *
 * Output: web/src/theme/tokens.css — GENERATED, do not edit.
 *   :root[data-theme="night"] { --panel: ...; }
 *   :root[data-theme="day"]   { --panel: ...; }
 *   :root                     { shared type / spacing vars }
 *
 * FRAME_EXTRAS below carries values that appear in the approved frames but are
 * NOT in tokens.json (top-bar hairline, search-field fill, LIVE-pill chrome,
 * day accents). They are namespaced --chrome-* so they never masquerade as
 * tokens. Each one is annotated with the frame it was measured from. When
 * tokens.json grows these values, delete them here.
 */

import { copyFileSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const SRC = resolve(here, '../src/theme/tokens.json');
const OUT = resolve(here, '../src/theme/tokens.css');
const LIMITS_SRC = resolve(here, '../../limits.json');
const LIMITS_OUT = resolve(here, '../src/limits.json');

/**
 * Frame-only chrome values — measured from
 * docs/design/"ais-lake MVP Batch 1 Map Shell.dc.html"
 *   :: "First visit" (night) and :: "First visit · Blue Marble day" (day).
 * Not present in docs/design/tokens.json.
 */
const FRAME_EXTRAS = {
  night: {
    'chrome-hairline': '#1C3040', // top-bar bottom border, search border, chip border, sweep track
    'chrome-search-fill': 'rgba(242,234,219,.045)',
    'chrome-search-ink': '#7B94A4', // magnifier stroke
    'chrome-search-placeholder': '#7B94A4',
    'chrome-live-fill': 'rgba(95,220,201,.07)',
    'chrome-live-ink': '#5FDCC9', // == color.night.wake
    'chrome-live-dot': '#5FDCC9',
    'chrome-caret': '#8FB4C6', // the ▾ next to LIVE
    'chrome-sweep-fill': '#5FDCC9', // == color.night.wake
    'chrome-chip-fill': 'rgba(10,22,32,.9)',
    'chrome-chip-ink': '#DCE7ED', // == color.night.text.soft
    'chrome-chip-border': '#1C3040',
    'chrome-chip-active-fill': '#E8B25C', // == color.night.gold
    'chrome-chip-active-ink': '#08151E',
    'chrome-chip-silent-fill': '#FF6A52', // F7 active "Recently silent" chip
    'chrome-chip-silent-ink': '#08151E',
    'chrome-bar-fill': 'rgba(10,22,32,.93)', // == color.night.panel
    'chrome-signin-ink': '#9DB4C2', // == color.night.text.mute
    'chrome-logo-dot': '#E8B25C',
    'chrome-logo-ink': '#F2EADB',
    'chrome-region-ink': '#F2EADB',
  },
  day: {
    'chrome-hairline': '#D8CDB4', // == color.day.border (top bar + search)
    'chrome-hairline-strong': '#C9BC9E', // LIVE pill + chip borders
    'chrome-search-fill': '#FFFDF7',
    'chrome-search-ink': '#6C7B84', // magnifier stroke
    'chrome-search-placeholder': '#77858E', // == color.day.text.faint
    'chrome-live-fill': 'rgba(216,54,31,.06)',
    'chrome-live-ink': '#A82A17',
    'chrome-live-dot': '#1B7F5E',
    'chrome-caret': '#6C7B84',
    'chrome-chip-fill': 'rgba(251,246,234,.94)', // == color.day.panel
    'chrome-chip-ink': '#0E2231',
    'chrome-chip-border': '#C9BC9E',
    'chrome-chip-active-fill': '#0E2231',
    'chrome-chip-active-ink': '#FBF6EA',
    'chrome-chip-silent-fill': '#FF6A52', // F7 — same coral in both themes
    'chrome-chip-silent-ink': '#08151E',
    'chrome-bar-fill': 'rgba(251,246,234,.96)', // frame is .96, tokens day.panel is .94
    'chrome-sweep-track': '#DFD5BE',
    'chrome-sweep-fill': '#D8361F', // == color.day.signal
    'chrome-signin-ink': '#5C6B74',
    'chrome-logo-dot': '#D8361F',
    'chrome-logo-ink': '#0E2231',
    'chrome-region-ink': '#0E2231',
  },
};

// night has no dedicated "strong" hairline / sweep track in the frame — reuse.
FRAME_EXTRAS.night['chrome-hairline-strong'] = '#2A4457'; // == color.night.border
FRAME_EXTRAS.night['chrome-sweep-track'] = '#1C3040';

const tokens = JSON.parse(readFileSync(SRC, 'utf8'));

const varName = (path) => `--${path.join('-').replace(/\./g, '-')}`;

/** Flatten a token subtree into [cssVarName, value] pairs. */
function flatten(node, path, out) {
  for (const [key, value] of Object.entries(node)) {
    if (key.startsWith('$')) continue;
    const next = [...path, key];
    if (Array.isArray(value)) {
      value.forEach((v, i) => out.push([`${varName(next)}-${i}`, String(v)]));
    } else if (value && typeof value === 'object') {
      flatten(value, next, out);
    } else {
      out.push([varName(next), String(value)]);
    }
  }
  return out;
}

function block(selector, pairs) {
  const body = pairs.map(([k, v]) => `  ${k}: ${v};`).join('\n');
  return `${selector} {\n${body}\n}\n`;
}

function themeBlock(theme) {
  const pairs = flatten(tokens.color[theme], [], []);
  // sea.bands[0] is the deepest water — the map placeholder ground.
  pairs.push(['--sea-deep', tokens.color[theme]['sea.bands'][0]]);
  pairs.push(['--sea-shallow', tokens.color[theme]['sea.bands'].at(-1)]);
  for (const [k, v] of Object.entries(FRAME_EXTRAS[theme])) pairs.push([`--${k}`, v]);
  return block(`:root[data-theme="${theme}"]`, pairs);
}

const shared = [
  ['--face-display', tokens.type.display],
  ['--face-ui', tokens.type.ui],
  ['--face-mono', tokens.type.mono],
  ...Object.entries(tokens.type.scale_px).map(([k, v]) => [
    `--text-${k.replace(/[._]/g, '-')}`,
    `${v}px`,
  ]),
  ...Object.entries(tokens.space_px).map(([k, v]) => [`--space-${k.replace(/[._]/g, '-')}`, `${v}px`]),
  // Top bar geometry, measured from the frames (not in tokens.json).
  ['--bar-h', '56px'],
  ['--bar-pad-x', '20px'],
  ['--bar-gap', '20px'],
  ['--chip-h', '30px'],
  ['--chip-gap', '7px'],
  ['--chips-top', '74px'],
];

const css = [
  '/* GENERATED by scripts/gen-tokens.mjs from src/theme/tokens.json.',
  ' * Source of truth: docs/design/tokens.json. Do not edit by hand. */',
  '',
  block(':root', shared),
  themeBlock('night'),
  themeBlock('day'),
].join('\n');

writeFileSync(OUT, css);
console.log(`tokens -> ${OUT}`);

copyFileSync(LIMITS_SRC, LIMITS_OUT);
console.log(`limits -> ${LIMITS_OUT}`);
