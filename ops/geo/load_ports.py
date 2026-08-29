"""Load ops/geo/ports.geojson into the `ports` table. Idempotent: run it as often
as you like, the table ends up the same.

Two kinds of feature share one row. `kind=port` features own the row and carry
`geom`; `kind=anchorage` features carry the same locode and only fill in
`anchorages`. Ports are upserted first so an anchorage always has a row to land on.

Validation is PostGIS's job, not ours — ST_IsValid on every geometry, and a
pairwise ST_Overlaps across the anchorages (two anchorages sharing water means a
ship at anchor would be counted twice). Both fail loudly rather than loading half
a file. No shapely: the database already owns the geometry code.

Run:  make geo        (or POSTGRES_URL=... uv run python ops/geo/load_ports.py)
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import asyncpg

DSN = os.environ.get("POSTGRES_URL", "postgresql://ais:ais-dev@localhost:5432/ais")
GEOJSON = Path(__file__).with_name("ports.geojson")


def rows(geojson: dict[str, Any]) -> tuple[list[tuple[str, str, str, str]], list[tuple[str, str]]]:
    """Split a FeatureCollection into (port rows, anchorage rows).

    Port row: (locode, name, kind, geometry as a GeoJSON string).
    Anchorage row: (locode, geometry as a GeoJSON string).
    """
    ports: list[tuple[str, str, str, str]] = []
    anchorages: list[tuple[str, str]] = []
    for feature in geojson["features"]:
        props = feature["properties"]
        locode, kind = props["locode"], props["kind"]
        geom = json.dumps(feature["geometry"])
        if kind == "anchorage":
            anchorages.append((locode, geom))
        else:
            ports.append((locode, props["name"], kind, geom))
    if not ports:
        raise ValueError("no port features — nothing to key the anchorages on")
    known = {locode for locode, *_ in ports}
    orphans = sorted({locode for locode, _ in anchorages} - known)
    if orphans:
        raise ValueError(f"anchorages for unknown locodes: {', '.join(orphans)}")
    seen = [locode for locode, _ in anchorages]
    dupes = sorted({locode for locode in seen if seen.count(locode) > 1})
    if dupes:
        raise ValueError(f"two anchorage features for one locode: {', '.join(dupes)}")
    return ports, anchorages


async def check_valid(conn: asyncpg.Connection, label: str, geoms: list[tuple[str, str]]) -> None:
    for locode, geom in geoms:
        reason = await conn.fetchval(
            "SELECT CASE WHEN ST_IsValid(g) THEN NULL ELSE ST_IsValidReason(g) END "
            "FROM (SELECT ST_GeomFromGeoJSON($1) AS g) t",
            geom,
        )
        if reason:
            raise ValueError(f"invalid {label} geometry for {locode}: {reason}")


async def check_no_overlap(conn: asyncpg.Connection, anchorages: list[tuple[str, str]]) -> None:
    for i, (a_code, a_geom) in enumerate(anchorages):
        for b_code, b_geom in anchorages[i + 1 :]:
            # Interiors sharing any water — ST_Overlaps alone misses the case
            # where one anchorage sits wholly inside another.
            if await conn.fetchval(
                "SELECT ST_Intersects(a, b) AND NOT ST_Touches(a, b) FROM "
                "(SELECT ST_GeomFromGeoJSON($1) AS a, ST_GeomFromGeoJSON($2) AS b) t",
                a_geom,
                b_geom,
            ):
                raise ValueError(f"anchorages overlap: {a_code} and {b_code}")


async def main() -> None:
    ports, anchorages = rows(json.loads(GEOJSON.read_text()))
    conn = await asyncpg.connect(DSN)
    try:
        await check_valid(conn, "port", [(c, g) for c, _, _, g in ports])
        await check_valid(conn, "anchorage", anchorages)
        await check_no_overlap(conn, anchorages)
        async with conn.transaction():
            # Idempotent for removals too: a port dropped from the geojson leaves
            # the table, an anchorage dropped from it clears its column.
            await conn.execute(
                "DELETE FROM ports WHERE locode != ALL($1)",
                [locode for locode, *_ in ports],
            )
            await conn.execute(
                "UPDATE ports SET anchorages = NULL WHERE locode != ALL($1)",
                [locode for locode, _ in anchorages],
            )
            await conn.executemany(
                """
                INSERT INTO ports (locode, name, kind, geom)
                VALUES ($1, $2, $3, ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON($4), 4326)))
                ON CONFLICT (locode) DO UPDATE
                    SET name = EXCLUDED.name, kind = EXCLUDED.kind, geom = EXCLUDED.geom
                """,
                ports,
            )
            attached = 0
            for locode, geom in anchorages:
                await conn.execute(
                    "UPDATE ports SET anchorages = "
                    "ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON($2), 4326)) WHERE locode = $1",
                    locode,
                    geom,
                )
                attached += 1
    finally:
        await conn.close()
    print(f"ports upserted: {len(ports)} · anchorages attached: {attached}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except ValueError as exc:
        sys.exit(f"load_ports: {exc}")
