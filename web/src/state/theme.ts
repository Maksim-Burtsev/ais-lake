/** F3 — theme resolution. Mirrors the inline bootstrap script in index.html;
 *  change both together or the shell will flash on first paint. */

export type Theme = 'night' | 'day';

export const THEME_STORAGE_KEY = 'theme';

/** Daylight window in local time: [07:00, 19:00) is day, the rest is night. */
export const DAY_START_HOUR = 7;
export const DAY_END_HOUR = 19;

export function isTheme(value: unknown): value is Theme {
  return value === 'night' || value === 'day';
}

export function themeByClock(now: Date = new Date()): Theme {
  const hour = now.getHours();
  return hour >= DAY_START_HOUR && hour < DAY_END_HOUR ? 'day' : 'night';
}

function readStoredTheme(): Theme | null {
  try {
    const stored = localStorage.getItem(THEME_STORAGE_KEY);
    return isTheme(stored) ? stored : null;
  } catch {
    return null;
  }
}

export function persistTheme(theme: Theme): void {
  try {
    localStorage.setItem(THEME_STORAGE_KEY, theme);
  } catch {
    /* private mode — the URL param still carries the choice */
  }
}

/** ?theme= > localStorage > local clock. */
export function resolveTheme(search: string, now: Date = new Date()): Theme {
  const fromUrl = new URLSearchParams(search).get(THEME_STORAGE_KEY);
  if (isTheme(fromUrl)) return fromUrl;
  return readStoredTheme() ?? themeByClock(now);
}

export function applyTheme(theme: Theme): void {
  document.documentElement.setAttribute('data-theme', theme);
}
