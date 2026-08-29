"""The limits the pipeline enforces, read from the one file that holds them (F27).

The same limits.json the api reads and the web copies. A second copy here would
let the detector open a gap at a threshold the map never draws, and the two
numbers would drift the first time one of them was tuned. LIMITS_PATH points at
the mount inside a container, where the repo root is not around.
"""

import json
import os
from pathlib import Path
from typing import Any

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "limits.json"

LIMITS: dict[str, Any] = json.loads(
    Path(os.environ.get("LIMITS_PATH") or DEFAULT_PATH).read_text()
)
# Past this with no fix and we no longer believe she is transmitting: the map
# draws her silent, F7 counts her, and the detector opens a gap. One number.
SILENT_AFTER_S: int = int(LIMITS["map_vessel_age_s"]["silent_after"])
