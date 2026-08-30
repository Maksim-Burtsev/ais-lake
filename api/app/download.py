"""F17 — the visible window as a file: /v1/ships/{key}/track.geojson|.csv.

No new queries: the bytes come from track_payload, the name from card_for. The
only thing this module owns is the window it asks for (DOWNLOAD_WINDOW_D, shorter
than the story's — limits.json argues why) and the filename F17 dictates,
`{name}-{imo}-{from}-{to}`. F17 does not say how the dates are written; we write
them YYYYMMDD, UTC, because a sorted download folder then reads chronologically.

IMO is what F17 asks for and what we print — when the ship has never told us hers,
the MMSI stands in it, since a file called `gas-khios-None-…` names nothing.
"""

import csv
import io
import json
from datetime import UTC, datetime
from typing import Any

from .limits import DOWNLOAD_WINDOW_D
from .map import RedisClient
from .ships import ClickHouseClient, card_for
from .ssr import slugify
from .track import track_payload

CSV_HEADER = ("ts", "lat", "lon")  # what the track carries — no sog in it, so none here.


def _day(ts: int) -> str:
    return datetime.fromtimestamp(ts, UTC).strftime("%Y%m%d")


def _csv(feature: dict[str, Any]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(CSV_HEADER)
    for (lon, lat), ts in zip(
        feature["geometry"]["coordinates"], feature["properties"]["times"], strict=True
    ):
        writer.writerow([datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), lat, lon])
    return out.getvalue()


async def download(
    ch: ClickHouseClient | None,
    redis_client: RedisClient | None,
    key: str,
    fmt: str,
    from_: Any = None,
    to: Any = None,
) -> tuple[bytes, str, str]:
    """(body, media type, filename) for one ship's slice. Raises what its two
    sources raise: ShipNotFound, CardUnavailable, StoryUnavailable."""
    card = await card_for(ch, redis_client, key)
    feature = await track_payload(ch, key, from_, to, window_d=DOWNLOAD_WINDOW_D)
    props = feature["properties"]
    stem = "-".join(
        (
            slugify(card["identity"]["name"]),
            str(card["identity"]["imo"] or card["mmsi"]),
            _day(props["from"]),
            _day(props["to"]),
        )
    )
    if fmt == "csv":
        return _csv(feature).encode(), "text/csv; charset=utf-8", f"{stem}.csv"
    return json.dumps(feature).encode(), "application/geo+json", f"{stem}.geojson"


__all__ = ["download"]
