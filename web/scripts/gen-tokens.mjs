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
 * the region picker, the F8 ship card, day accents). They are namespaced --chrome-* so they never masquerade as
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
    // F6 region picker panel :: "Region picker" (5c)
    'chrome-picker-fill': 'rgba(10,22,32,.97)',
    'chrome-picker-border': '#2A4457',
    'chrome-picker-shadow': '0 26px 60px rgba(0,0,0,.6)',
    'chrome-picker-section': '#6E8798', // the SEAS eyebrow
    'chrome-picker-ink': '#DCE7ED',
    'chrome-picker-ink-active': '#F6F0E3',
    'chrome-picker-count': '#7E8B93',
    'chrome-picker-rule': '#16283A',
    'chrome-picker-gold': '#E8B25C', // "Straits" header + the active row's rail
    'chrome-picker-active-fill': 'rgba(232,178,92,.1)',
    // F5 search :: "Search open" (5b) + "Batch 4 Entry and States" :: "Search
    // with nothing to show" (8c). The panel itself reuses the picker's chrome —
    // same fill, border, shadow, eyebrow and highlight rail, measured identical.
    'chrome-search-focus': '#E8B25C', // focused field border, magnifier, caret
    'chrome-search-focus-fill': 'rgba(232,178,92,.06)',
    'chrome-search-text': '#F2EADB', // what you typed
    'chrome-search-sub': '#9DB4C2', // row subtitle + the empty state's paragraph
    'chrome-search-hull': '#EAF2F7', // row silhouette, under way
    'chrome-search-hull-still': '#C6D8E2', // row silhouette, stopped
    'chrome-search-sea-mark': '#5FDCC9', // the 8×8 square in a sea row
    // trailing metric, one colour per state (5b + "Batch 6" :: 10a)
    'chrome-search-underway': '#5FDCC9',
    'chrome-search-anchored': '#8FB8CC',
    'chrome-search-silent': '#FF6A52',
    'chrome-search-moored': '#FFB454',
    'chrome-search-empty-rule': '#14242F', // between TRY INSTEAD rows
    // F8 ship card :: "Night Passage · 1440 × 900" (2c) + "Mobile · ship tapped" (5g)
    'chrome-card-fill': 'rgba(10,22,32,.94)',
    'chrome-card-sheet-fill': '#0A1620', // the mobile sheet is opaque, not a panel
    'chrome-card-border': '#1C3040',
    'chrome-card-shadow': '0 24px 52px rgba(2,10,18,.6)',
    'chrome-card-sheet-shadow': '0 -18px 44px rgba(0,0,0,.5)',
    'chrome-card-handle': '#26404F', // the sheet's grab handle
    'chrome-card-eyebrow': '#E8B25C',
    'chrome-card-age': '#6E8798',
    'chrome-card-name': '#F6F0E3',
    'chrome-card-sub': '#9DB4C2',
    'chrome-card-rule': '#1C3040',
    'chrome-card-sentence': '#DCE7ED',
    'chrome-card-strong': '#F6F0E3',
    'chrome-card-figure': '#5FDCC9', // the mono numerals inside the sentence
    'chrome-card-particulars': '#8FB4C6',
    'chrome-card-action-fill': '#E8B25C',
    'chrome-card-action-ink': '#0A1620',
    'chrome-card-star-border': '#2A4457',
    'chrome-card-star-ink': '#9DB4C2',
    // F11 welcome toast + map key :: "First visit" (1b)
    'chrome-toast-fill': 'rgba(10,22,32,.95)',
    'chrome-toast-border': '#2A4457', // == color.night.border
    'chrome-toast-shadow': '0 16px 40px rgba(0,0,0,.5)',
    'chrome-toast-ink': '#F2EADB',
    'chrome-toast-dismiss': '#7E8B93', // the ✕
    'chrome-key-fill': 'rgba(10,22,32,.92)',
    'chrome-key-border': '#1C3040',
    'chrome-key-ink': '#DCE7ED', // the three row labels
    'chrome-key-hull': '#EAF2F7', // == color.night.vessel hull.lit
    'chrome-key-hull-port': '#6E93AB', // the shaded half; the day frame inverts it
    'chrome-key-anchor-ring': '#8FB8CC',
    'chrome-key-anchor-dot': '#C6D8E2',
    'chrome-key-silent': '#FF6A52',
    'chrome-key-rule': '#1C3040',
    'chrome-key-count': '#E8B25C', // "2,412 SHIPS"
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
    // F6 region picker — the same panel in the day palette (no day frame for it).
    'chrome-picker-fill': 'rgba(251,246,234,.97)',
    'chrome-picker-border': '#C9BC9E',
    'chrome-picker-shadow': '0 26px 60px rgba(40,30,10,.22)',
    'chrome-picker-section': '#77858E',
    'chrome-picker-ink': '#0E2231',
    'chrome-picker-ink-active': '#0E2231',
    'chrome-picker-count': '#6C7B84',
    'chrome-picker-rule': '#DFD5BE',
    'chrome-picker-gold': '#B87F3A', // == color.day.gold
    'chrome-picker-active-fill': 'rgba(184,127,58,.12)',
    // F5 search — the same panel in the day palette (no day frame for it either).
    'chrome-search-focus': '#B87F3A', // == color.day.gold
    'chrome-search-focus-fill': 'rgba(184,127,58,.1)',
    'chrome-search-text': '#0E2231',
    'chrome-search-sub': '#5C6B74',
    'chrome-search-hull': '#0C1D28',
    'chrome-search-hull-still': '#5C6B74',
    'chrome-search-sea-mark': '#0B5E7A',
    'chrome-search-underway': '#0B5E7A',
    'chrome-search-anchored': '#5C6B74',
    'chrome-search-silent': '#D8361F', // == color.day.signal
    'chrome-search-moored': '#B87F3A',
    'chrome-search-empty-rule': '#DFD5BE',
    // F8 ship card :: "Blue Marble day" hero card, re-measured at the 1440 geometry
    'chrome-card-fill': '#FBF6EA',
    'chrome-card-sheet-fill': '#FBF6EA',
    'chrome-card-border': '#C9BC9E',
    'chrome-card-shadow': '0 28px 64px rgba(8,40,64,.35)',
    'chrome-card-sheet-shadow': '0 -18px 44px rgba(40,30,10,.28)',
    'chrome-card-handle': '#D8CDB4',
    'chrome-card-eyebrow': '#B8145A',
    'chrome-card-age': '#77858E',
    'chrome-card-name': '#0C1D28',
    'chrome-card-sub': '#5C6B74',
    'chrome-card-rule': '#D8CDB4',
    'chrome-card-sentence': '#233642',
    'chrome-card-strong': '#0C1D28',
    'chrome-card-figure': '#0B5E7A',
    'chrome-card-particulars': '#6C7B84',
    'chrome-card-action-fill': '#D8361F',
    'chrome-card-action-ink': '#FFF6F3',
    'chrome-card-star-border': '#C9BC9E',
    'chrome-card-star-ink': '#5C6B74',
    // F11 :: "First visit · Blue Marble day" (1c) — the toast is opaque here.
    'chrome-toast-fill': '#FBF6EA',
    'chrome-toast-border': '#C9BC9E',
    'chrome-toast-shadow': '0 16px 40px rgba(8,40,64,.28)',
    'chrome-toast-ink': '#0E2231',
    'chrome-toast-dismiss': '#77858E',
    'chrome-key-fill': 'rgba(251,246,234,.95)',
    'chrome-key-border': '#C9BC9E',
    'chrome-key-ink': '#233642',
    'chrome-key-hull': '#0C2436', // day inverts the pair: dark hull, sunlit port
    'chrome-key-hull-port': '#FFE7B8',
    'chrome-key-anchor-ring': '#2E6E86',
    'chrome-key-anchor-dot': '#12384E',
    'chrome-key-silent': '#D8361F', // == color.day.signal
    'chrome-key-rule': '#D8CDB4', // == color.day.border
    'chrome-key-count': '#B8145A',
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
