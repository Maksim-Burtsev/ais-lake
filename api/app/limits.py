"""The limits table, read from the one file that holds it (F27).

limits.json lives at the repo root because the frontend copies it too — the UI
must quote the same numbers the server enforces, never its own. LIMITS_PATH
overrides the location for containers, where the repo root is not around.
"""

import json
import os
from pathlib import Path
from typing import Any

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "limits.json"

LIMITS: dict[str, Any] = json.loads(
    Path(os.environ.get("LIMITS_PATH") or DEFAULT_PATH).read_text()
)
REFRESH: dict[str, Any] = LIMITS["map_refresh_s"]
# Older than this and a ship is not on the map and not in a region count (map.py).
MAX_VESSEL_AGE_S: int = int(LIMITS["map_vessel_age_s"]["max"])
# Older than this and we no longer believe she is transmitting. Deliberately
# shorter than MAX_VESSEL_AGE_S so a silent ship is still on the water to see —
# limits.json argues the gap between the two.
SILENT_AFTER_S: int = int(LIMITS["map_vessel_age_s"]["silent_after"])
# The window "typical wait" is measured over (ports.py).
PORT_WAIT_WINDOW_D: int = int(LIMITS["port_wait_window_d"]["days"])


def clamp_interval(requested: Any, tier: str = "anon") -> int:
    """The asked-for cadence if this tier may have it, else the default one.

    Not a nearest-match: the selector only ever offers the options below, so
    anything else is a hand-written URL and gets the honest default back.
    """
    allowed = [o for o in REFRESH["options"] if o >= REFRESH["floor"][tier]]
    try:
        want = int(requested)
    except (TypeError, ValueError):
        return int(REFRESH["default"])
    return want if want in allowed else int(REFRESH["default"])
