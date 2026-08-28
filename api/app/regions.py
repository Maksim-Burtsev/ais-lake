"""F6 — /v1/regions: the picker's menu, with a live count per box.

regions.json lives at the repo root for the same reason limits.json does: one
file, and nobody gets to invent a bbox locally. REGIONS_PATH overrides the
location for containers, where the repo root is not around.

Redis down is NOT a 503 here (unlike the snapshot): every count simply comes back
null and the panel draws "—". An empty sea would be a lie; a missing number is
not, and the picker has to keep working so the user can move somewhere else.
"""

import json
import os
from pathlib import Path
from typing import Any

from .map import RedisClient, SnapshotUnavailable, counts_for

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "regions.json"

REGIONS: dict[str, Any] = json.loads(
    Path(os.environ.get("REGIONS_PATH") or DEFAULT_PATH).read_text()
)


def _entries() -> list[dict[str, Any]]:
    return [*REGIONS["seas"], *REGIONS["straits"]]


async def regions_payload(client: RedisClient | None) -> dict[str, Any]:
    boxes = [tuple(e["bbox"]) if e["live"] else None for e in _entries()]
    try:
        counts = await counts_for(client, boxes)
    except SnapshotUnavailable:
        counts = [None] * len(boxes)

    rows = [
        {"slug": e["slug"], "name": e["name"], "bbox": e["bbox"], "count": count}
        for e, count in zip(_entries(), counts, strict=True)
    ]
    return {"regions": rows[: len(REGIONS["seas"])], "straits": rows[len(REGIONS["seas"]) :]}
