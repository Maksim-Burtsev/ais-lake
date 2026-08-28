/** F6 region picker — docs/design/"ais-lake MVP Batch 1 Map Shell.dc.html" :: "Region picker".
 *
 *  Seas and straits with a live count each, straight from GET /v1/regions (the
 *  server owns the bboxes; the client never invents one). A null count is the
 *  coming-soon signal — that row is listed but not pickable.
 *
 *  Picking re-centres the map and, because the url patch carries the new bbox to
 *  the open socket, re-subscribes the live feed within the same second.
 */

import { useEffect, useRef, useState } from 'react';
import { mapView, type Bbox } from './MapCanvas';
import { useUrlStore } from '../state/url';

interface Region {
  slug: string;
  name: string;
  bbox: Bbox;
  count: number | null;
}
interface RegionsResponse {
  regions: Region[];
  straits: Region[];
}

const COUNT = new Intl.NumberFormat('en-US');

function Row({ region, active, onPick }: { region: Region; active: boolean; onPick: () => void }) {
  const soon = region.count === null;
  return (
    <button
      type="button"
      role="option"
      aria-selected={active}
      disabled={soon}
      onClick={onPick}
      className="flex h-[34px] w-full items-center justify-between border-0 border-l-2 bg-transparent px-[14px] text-left disabled:cursor-default"
      style={{
        borderLeftColor: active ? 'var(--chrome-picker-gold)' : 'transparent',
        background: active ? 'var(--chrome-picker-active-fill)' : 'transparent',
        cursor: soon ? undefined : 'pointer',
        opacity: soon ? 0.55 : 1,
      }}
    >
      <span
        className="text-[13.5px]"
        style={{
          color: active ? 'var(--chrome-picker-ink-active)' : 'var(--chrome-picker-ink)',
        }}
      >
        {region.name}
      </span>
      <span className="font-mono text-[11px] text-[var(--chrome-picker-count)]">
        {soon ? 'coming soon' : COUNT.format(region.count as number)}
      </span>
    </button>
  );
}

export function RegionPicker() {
  const region = useUrlStore((s) => s.region);
  const patch = useUrlStore((s) => s.patch);
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<RegionsResponse | null>(null);
  const box = useRef<HTMLDivElement>(null);

  // One fetch per session, on first open: the counts are a menu, not a feed.
  useEffect(() => {
    if (!open || data) return;
    fetch('/v1/regions')
      .then((r) => r.json() as Promise<RegionsResponse>)
      .then(setData)
      .catch((error: unknown) => console.warn('regions:', error));
  }, [open, data]);

  useEffect(() => {
    if (!open) return;
    const key = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    const away = (event: PointerEvent) => {
      if (!box.current?.contains(event.target as Node)) setOpen(false);
    };
    window.addEventListener('keydown', key);
    window.addEventListener('pointerdown', away);
    return () => {
      window.removeEventListener('keydown', key);
      window.removeEventListener('pointerdown', away);
    };
  }, [open]);

  const pick = (picked: Region) => {
    setOpen(false);
    patch({ region: picked.name });
    mapView.goto?.(picked.bbox);
  };

  return (
    <div ref={box} className="relative">
      <button
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
        className="flex cursor-pointer items-center gap-[8px] border-0 border-r border-[var(--chrome-hairline)] bg-transparent p-0 pr-[18px] text-[13px] text-[var(--chrome-region-ink)]"
      >
        {region}
        <span
          aria-hidden="true"
          className="text-[10px]"
          style={{ color: open ? 'var(--chrome-picker-gold)' : 'var(--chrome-caret)' }}
        >
          {open ? '▴' : '▾'}
        </span>
      </button>

      {open && (
        <div
          role="listbox"
          aria-label="Region"
          className="absolute top-[calc(100%+14px)] right-0 z-40 w-[300px] border"
          style={{
            background: 'var(--chrome-picker-fill)',
            borderColor: 'var(--chrome-picker-border)',
            boxShadow: 'var(--chrome-picker-shadow)',
            padding: '10px 0 12px',
          }}
        >
          <div className="px-[14px] pt-[6px] pb-[8px] font-mono text-[10px] tracking-[0.18em] text-[var(--chrome-picker-section)]">
            SEAS
          </div>
          {data?.regions.map((r) => (
            <Row key={r.slug} region={r} active={r.name === region} onPick={() => pick(r)} />
          ))}

          <div className="my-[10px] h-px bg-[var(--chrome-picker-rule)]" />
          <div className="flex items-baseline gap-[8px] px-[14px] pt-[2px] pb-[8px]">
            <span className="font-display text-[14px] text-[var(--chrome-picker-gold)]">
              Straits
            </span>
            <span className="text-[11.5px] text-[var(--chrome-picker-count)]">— live theatre</span>
          </div>
          {data?.straits.map((r) => (
            <Row key={r.slug} region={r} active={r.name === region} onPick={() => pick(r)} />
          ))}
        </div>
      )}
    </div>
  );
}
