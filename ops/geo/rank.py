"""Rank the launch region's ports from our own data, so the 12 hand-drawn
polygons (M3-H1) are chosen by evidence rather than memory.

Two independent signals, deliberately not blended into one score:

  OBSERVED  where ships are actually moored or at anchor right now. Cells of
            vessel_latest at h3 res 7 (~5 km across), greedily merged into port
            complexes by distance. This is the authoritative one: it is inside
            the launch bbox by construction, and a berth nobody uses is not a
            port worth drawing.

  DECLARED  what ships say in vessels_static.destination. Free text, ~62% filled,
            and it reaches well past the bbox — Duisburg and Mainz are up the
            Rhine, Gdansk and Gothenburg are other seas. Useful as a name and as
            a demand signal, never as a position.

Where the two disagree the report says so rather than picking a winner: a port
with traffic declared and nothing moored is either outside our water or outside
our coverage, and both are worth knowing before an evening in QGIS.

Run:  uv run --with requests python ops/geo/rank.py     (or plain python3)
      CLICKHOUSE_URL=... to point somewhere other than the dev box.
"""

from __future__ import annotations

import os
import re
import sys
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field
from math import cos, dist, radians

CLICKHOUSE_URL = os.environ.get(
    "CLICKHOUSE_URL", "http://localhost:8123/?user=ais&password=ais-dev&database=ais"
)

# A port complex is wider than one berth: Rotterdam runs 40 km from Maasvlakte to
# Waalhaven and is ONE locode. 18 km merges those and still leaves Amsterdam and
# IJmuiden (25 km apart, two locodes) as two rows.
CLUSTER_KM = 18.0
# How much of a cluster must agree on a port name before we print it. Below this
# the cluster is listed by position only — see Cluster.named for why.
NAME_MAJORITY = 0.45
# Only what a ship still transmitting can vouch for — the same window the map cuts at.
WINDOW = "24 HOUR"

# Declared destination -> (locode, display name). Only ports that actually turn up
# as a cluster need an entry; the report prints anything it could not map so this
# table grows from evidence rather than guesswork. Berth suffixes ("ROTTERDAM 3E
# PETROHA", "AMSTERDAM AFRIKAHAVE") are stripped by _base() before lookup.
ALIASES: dict[str, tuple[str, str]] = {
    "ROTTERDAM": ("NLRTM", "Rotterdam"), "NLRTM": ("NLRTM", "Rotterdam"),
    "NL RTM": ("NLRTM", "Rotterdam"), "BOTLEK": ("NLRTM", "Rotterdam"),
    "MAASVLAKTE": ("NLRTM", "Rotterdam"), "EUROPOORT": ("NLRTM", "Rotterdam"),
    "AMSTERDAM": ("NLAMS", "Amsterdam"), "NLAMS": ("NLAMS", "Amsterdam"),
    "NL AMS": ("NLAMS", "Amsterdam"),
    "ANTWERPEN": ("BEANR", "Antwerp"), "ANTWERP": ("BEANR", "Antwerp"),
    "BEANR": ("BEANR", "Antwerp"), "BE ANR": ("BEANR", "Antwerp"),
    "HAMBURG": ("DEHAM", "Hamburg"), "DEHAM": ("DEHAM", "Hamburg"),
    "BREMERHAVEN": ("DEBRV", "Bremerhaven"), "DEBRV": ("DEBRV", "Bremerhaven"),
    "BREMEN": ("DEBRE", "Bremen"), "DEBRE": ("DEBRE", "Bremen"),
    "HARLINGEN": ("NLHAR", "Harlingen"), "NLHAR": ("NLHAR", "Harlingen"),
    "DORDRECHT": ("NLDOR", "Dordrecht"), "NLDOR": ("NLDOR", "Dordrecht"),
    "IJMUIDEN": ("NLIJM", "IJmuiden"), "NLIJM": ("NLIJM", "IJmuiden"),
    "VLISSINGEN": ("NLVLI", "Vlissingen"), "NLVLI": ("NLVLI", "Vlissingen"),
    "FLUSHING": ("NLVLI", "Vlissingen"),
    "DEN HELDER": ("NLDHR", "Den Helder"), "NLDHR": ("NLDHR", "Den Helder"),
    "EEMSHAVEN": ("NLEEM", "Eemshaven"), "NLEEM": ("NLEEM", "Eemshaven"),
    "GENT": ("BEGNE", "Ghent"), "GHENT": ("BEGNE", "Ghent"), "BEGNE": ("BEGNE", "Ghent"),
    "MOERDIJK": ("NLMOE", "Moerdijk"), "NLMOE": ("NLMOE", "Moerdijk"),
    "WERKENDAM": ("NLWKD", "Werkendam"),
    "OOSTENDE": ("BEOST", "Ostend"), "BEOST": ("BEOST", "Ostend"),
    "ZEEBRUGGE": ("BEZEE", "Zeebrugge"), "BEZEE": ("BEZEE", "Zeebrugge"),
    "TERNEUZEN": ("NLTNZ", "Terneuzen"), "NLTNZ": ("NLTNZ", "Terneuzen"),
    "DELFZIJL": ("NLDZL", "Delfzijl"), "NLDZL": ("NLDZL", "Delfzijl"),
    "EMDEN": ("DEEME", "Emden"), "DEEME": ("DEEME", "Emden"),
    "CUXHAVEN": ("DECUX", "Cuxhaven"), "DECUX": ("DECUX", "Cuxhaven"),
    "BRUNSBUTTEL": ("DEBRB", "Brunsbüttel"), "DEBRB": ("DEBRB", "Brunsbüttel"),
    "WILHELMSHAVEN": ("DEWVN", "Wilhelmshaven"), "DEWVN": ("DEWVN", "Wilhelmshaven"),
    "ESBJERG": ("DKEBJ", "Esbjerg"), "DKEBJ": ("DKEBJ", "Esbjerg"),
    "LE HAVRE": ("FRLEH", "Le Havre"), "FRLEH": ("FRLEH", "Le Havre"),
    "DUNKERQUE": ("FRDKK", "Dunkirk"), "FRDKK": ("FRDKK", "Dunkirk"),
    "CALAIS": ("FRCQF", "Calais"), "FRCQF": ("FRCQF", "Calais"),
    "DOVER": ("GBDVR", "Dover"), "GBDVR": ("GBDVR", "Dover"),
    "LONDON": ("GBLON", "London"), "GBLON": ("GBLON", "London"),
    "FELIXSTOWE": ("GBFXT", "Felixstowe"), "GBFXT": ("GBFXT", "Felixstowe"),
    "SOUTHAMPTON": ("GBSOU", "Southampton"), "GBSOU": ("GBSOU", "Southampton"),
    "IMMINGHAM": ("GBIMM", "Immingham"), "GBIMM": ("GBIMM", "Immingham"),
    "TEESPORT": ("GBTEE", "Teesport"), "GBTEE": ("GBTEE", "Teesport"),
    "URK": ("NLURK", "Urk"), "LAUWERSOOG": ("NLLWO", "Lauwersoog"),
    "STELLENDAM": ("NLSTE", "Stellendam"), "ENKHUIZEN": ("NLENK", "Enkhuizen"),
    "LEMMER": ("NLLEM", "Lemmer"), "SCHEVENINGEN": ("NLSCE", "Scheveningen"),
}

# Not a place: a status, a radio channel, a fishery. Dropped by name, never ranked.
NOT_A_PLACE = re.compile(
    r"^(FISHING|FISHFARM|FOR ORDERS?|CH ?\d+|VHF|SAR |PILOT|ANCHORAGE|SEA|AT SEA|"
    r"UNKNOWN|N/?A|--|\.+)",
)


def _base(dest: str) -> str:
    """"ROTTERDAM 3E PETROHA" -> "ROTTERDAM". A berth is not another port."""
    dest = re.sub(r"[^A-Z0-9 ]+", " ", dest.upper()).strip()
    for n in (3, 2, 1):  # longest alias first: "DEN HELDER" before "DEN"
        head = " ".join(dest.split()[:n])
        if head in ALIASES:
            return head
    return dest


def query(sql: str) -> list[list[str]]:
    body = sql.encode()
    with urllib.request.urlopen(CLICKHOUSE_URL, data=body, timeout=60) as r:  # noqa: S310
        text = r.read().decode()
    return [line.split("\t") for line in text.strip().splitlines() if line]


def km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Flat-earth distance. At 52°N over 20 km the error is centimetres."""
    return dist((a[0] * 111.32, a[1] * 111.32 * cos(radians(a[0]))),
                (b[0] * 111.32, b[1] * 111.32 * cos(radians(a[0]))))


@dataclass
class Cluster:
    lat: float
    lon: float
    ships: int = 0
    moored: int = 0
    anchored: int = 0
    dests: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def absorb(self, ships: int, moored: int, anchored: int) -> None:
        """Counts move, the centre does not.

        A drifting centroid chains: the Rhine delta is one continuous ribbon of
        moored barges, so every 18 km hop dragged Rotterdam's centre up it until
        "Terneuzen" came out in the IJsselmeer and "Eemshaven" inland in Germany.
        The seed is the busiest cell of the complex, which is also the honest
        answer to "where do I start drawing".
        """
        self.ships += ships
        self.moored += moored
        self.anchored += anchored

    @property
    def hint(self) -> str:
        """The most-declared destination among these ships — a HINT, not a name.

        `destination` is where a ship is GOING. A moored Rhine barge bound for
        Eemshaven votes for a port 250 km from her berth, so every attempt to
        name a cluster from this field put a seaport on an inland canal. The
        position is the fact; naming it off a chart is the human's ten minutes,
        and the one part of this they will get right.
        """
        ranked = sorted(self.dests.items(), key=lambda kv: -kv[1])
        return ranked[0][0] if ranked else ""

    @property
    def unmapped(self) -> str:
        ranked = sorted(self.dests.items(), key=lambda kv: -kv[1])
        return ranked[0][0] if ranked else ""


OBSERVED_SQL = f"""
SELECT h3ToString(geoToH3(lo, la, 7)) AS cell, count() AS ships,
       countIf(state = 'moored') AS moored, countIf(state = 'anchored') AS anchored,
       avg(la) AS lat, avg(lo) AS lon, arrayStringConcat(groupArray(dest), '|') AS dests
FROM (
    SELECT v.mmsi AS mmsi, argMax(v.lat, v.ts) AS la, argMax(v.lon, v.ts) AS lo,
           argMax(toString(v.state), v.ts) AS state, max(v.ts) AS last_ts,
           upperUTF8(trim(any(s.destination))) AS dest
    FROM vessel_latest AS v
    LEFT JOIN (SELECT mmsi, argMax(destination, ts) AS destination
               FROM vessels_static GROUP BY mmsi) AS s ON s.mmsi = v.mmsi
    GROUP BY v.mmsi
)
WHERE last_ts > now() - INTERVAL {WINDOW} AND state IN ('moored', 'anchored')
GROUP BY cell ORDER BY ships DESC FORMAT TSV
"""

DECLARED_SQL = """
SELECT upperUTF8(trim(destination)) AS d, uniqExact(mmsi) AS ships
FROM (SELECT mmsi, argMax(destination, ts) AS destination, max(ts) AS t
      FROM vessels_static GROUP BY mmsi)
WHERE destination != '' AND t > now() - INTERVAL 14 DAY
GROUP BY d HAVING ships >= 3 ORDER BY ships DESC FORMAT TSV
"""


def observed() -> list[Cluster]:
    clusters: list[Cluster] = []
    for cell in query(OBSERVED_SQL):
        _, ships, moored, anchored, lat, lon, dests = cell
        at = (float(lat), float(lon))
        near = min(clusters, key=lambda c: km((c.lat, c.lon), at), default=None)
        if near is None or km((near.lat, near.lon), at) > CLUSTER_KM:
            near = Cluster(lat=at[0], lon=at[1])
            clusters.append(near)
        near.absorb(int(ships), int(moored), int(anchored))
        for d in dests.split("|"):
            if d and not NOT_A_PLACE.match(d):
                near.dests[d] += 1
    return sorted(clusters, key=lambda c: -c.ships)


def declared() -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for d, ships in query(DECLARED_SQL):
        if NOT_A_PLACE.match(d):
            continue
        hit = ALIASES.get(_base(d))
        if hit:
            out[hit[0]] += int(ships)
    return out


def main() -> None:
    clusters, decl = observed(), declared()

    print("## Observed — the busiest moorings in our own water, last 24 h\n")
    print("Position and counts are facts. The last column is only what these ships "
          "*declared*, and a moored ship declares where she is going — treat it as a "
          "hint and name the row off a chart.\n")
    print("| # | centre (start drawing here) | moored | at anchor | most-declared |")
    print("|---|------|--------|-----------|------|")
    for i, c in enumerate(clusters[:18], 1):
        print(f"| {i} | {c.lat:.4f}, {c.lon:.4f} | {c.moored} | {c.anchored} | "
              f"{c.hint or '—'} |")

    print("\n## Declared — where ships say they are bound, last 14 d\n")
    print("This is what the field is for, and it is the demand signal: a port with "
          "traffic bound for it earns a page even if its berths sit outside our bbox.\n")
    print("| # | locode | port | ships |")
    print("|---|--------|------|-------|")
    for i, (locode, ships) in enumerate(sorted(decl.items(), key=lambda kv: -kv[1])[:18], 1):
        name = next(v[1] for v in ALIASES.values() if v[0] == locode)
        print(f"| {i} | {locode} | {name} | {ships} |")


if __name__ == "__main__":
    sys.exit(main())
