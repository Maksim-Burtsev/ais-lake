# ais-lake

An open-source living map of the sea. Real ships, live — rendered as ships, not dots:
eight nameable hull silhouettes, night and day themes, and every vessel's voyage told
as a plain-language story ("Waited at anchor — 14 hours") instead of jargon and scores.

Launch region: the Black Sea, the Bosphorus and the Sea of Marmara.

## What it does

- **Living map** — live AIS traffic with class silhouettes, wakes and true-scale
  harbour views; every view is a shareable URL.
- **Ship stories** — a vessel's computed events (port calls, anchorage waits, AIS
  gaps, load/discharge) as a prose timeline with replay.
- **Ports & straits** — who is waiting now, typical wait, and who is passing through.
- **Follows & alerts** — star a ship, get an email when she arrives, goes silent, or
  enters an area you drew.
- **Open API** — the same REST/WS endpoints the frontend uses, with a free demo tier.

## Stack

Python 3.14 · FastAPI · Redpanda · ClickHouse · PostgreSQL + PostGIS · Redis ·
React + TypeScript + MapLibre GL. One docker-compose, one box.

## Status

Early — being built in public, milestone by milestone. Nothing to run yet; the steel
thread (live feed → lake → browser) is the first milestone.

## Development

```
cp .env.example .env   # add your aisstream.io key
make dev               # (arrives with the first milestone)
```

## License

TBD before first public release.
