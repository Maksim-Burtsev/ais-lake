"""Offline seed: replay Danish Maritime Authority daily dumps into the lake.

A cold lake makes a dead map. The DMA publishes one zipped CSV per day of all
AIS traffic it heard — free, no key, no rate limit:

    http://web.ais.dk/aisdata/aisdk-YYYY-MM-DD.zip

Each zip holds a single CSV (aisdk-YYYY-MM-DD.csv, tens of GB uncompressed),
so nothing is ever extracted to disk: the zip is streamed, decoded and parsed
row by row. Rows become the same PositionRow / StaticRow the live refinery
produces (src="dma") and go through Refinery.handle_parsed, so validation,
dedup, vessel_latest and the sinks behave exactly as they do live — the bbox
check alone is what confines a Danish dump to the North Sea launch region.

Entry point: `python -m ais_pipeline.seed` (or `make seed`).
"""
