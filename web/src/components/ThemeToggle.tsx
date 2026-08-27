import { useUrlStore } from '../state/url';

/** F3 toggle. Not drawn in the Batch 1 frames — built from the same chrome as
 *  the LIVE pill so it reads as part of the bar rather than a new element. */
export function ThemeToggle() {
  const theme = useUrlStore((s) => s.theme);
  const toggleTheme = useUrlStore((s) => s.toggleTheme);
  const isNight = theme === 'night';

  return (
    <button
      type="button"
      onClick={toggleTheme}
      aria-label={isNight ? 'Switch to daylight' : 'Switch to night'}
      title={isNight ? 'Switch to daylight' : 'Switch to night'}
      className="flex h-[26px] w-[26px] cursor-pointer items-center justify-center border border-[var(--chrome-hairline-strong)] bg-transparent p-0 text-[var(--text-mute)]"
    >
      {isNight ? (
        // sun — click for daylight
        <svg width="13" height="13" viewBox="0 0 14 14" aria-hidden="true">
          <circle cx="7" cy="7" r="3" fill="none" stroke="currentColor" strokeWidth="1.3" />
          {[0, 45, 90, 135, 180, 225, 270, 315].map((deg) => (
            <line
              key={deg}
              x1="7"
              y1="1.4"
              x2="7"
              y2="2.8"
              stroke="currentColor"
              strokeWidth="1.3"
              strokeLinecap="round"
              transform={`rotate(${deg} 7 7)`}
            />
          ))}
        </svg>
      ) : (
        // moon — click for night
        <svg width="13" height="13" viewBox="0 0 14 14" aria-hidden="true">
          <path
            d="M10.6 8.7A4.6 4.6 0 0 1 5.3 3.4a4.6 4.6 0 1 0 5.3 5.3Z"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.3"
            strokeLinejoin="round"
          />
        </svg>
      )}
    </button>
  );
}
