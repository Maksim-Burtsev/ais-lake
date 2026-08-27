/** S1 filter row — same frames as TopBar. Visual stubs in M2-T1: the active
 *  chip is fixed to "All ships"; wiring lands with F7. */

export const FILTERS = [
  'All ships',
  'Tankers',
  'Cargo',
  'Waiting at anchor',
  'Recently silent',
] as const;

export type Filter = (typeof FILTERS)[number];

export function FilterChips({ active = 'All ships' }: { active?: Filter }) {
  return (
    <div className="pointer-events-auto flex gap-[var(--chip-gap)] whitespace-nowrap">
      {FILTERS.map((label) => {
        const isActive = label === active;
        return (
          <button
            key={label}
            type="button"
            aria-pressed={isActive}
            className={
              isActive
                ? 'flex h-[var(--chip-h)] cursor-pointer items-center border-0 px-[13px] text-[12.5px] text-[var(--chrome-chip-active-ink)] bg-[var(--chrome-chip-active-fill)]'
                : 'flex h-[var(--chip-h)] cursor-pointer items-center border border-[var(--chrome-chip-border)] px-[13px] text-[12.5px] text-[var(--chrome-chip-ink)] bg-[var(--chrome-chip-fill)]'
            }
          >
            {label}
          </button>
        );
      })}
    </div>
  );
}
