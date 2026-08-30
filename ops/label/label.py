"""Hand-label 20 gaps, then score the coverage classifier against them.

The classifier in pipeline/ais_pipeline/detectors/coverage.py answers a question
nobody can answer from the row alone: a ship stopped transmitting — was that HER
(went dark, or did something worth a sentence) or US (she sailed out of our
receivers' hearing)? Its thresholds were read off aggregate statistics of the
lake. Aggregates cannot tell you whether the verdict on ONE gap was right, and
until a human has looked at twenty of them the numbers in that docstring are a
plausible story rather than a measurement.

So: `sample` pulls 20 gaps and prints, for each, everything needed to judge it
by eye — who she is, where she went quiet, what her track looked like on both
sides of the silence, and how many other ships were being heard in that patch of
sea at that minute. A gap that ends with the ship resuming 200 km further on at
14 kn, in a cell where nobody else was heard either, is reception. A gap in the
Maasvlakte approaches with a dozen neighbours still chattering is hers.

`report` reads the labels back and prints precision/recall for the "unusual"
call. Two deliberate choices:

  * abstentions are not errors. `coverage-unknown` is the classifier declining
    to answer, and folding it into either class would score honesty as a
    mistake. It is reported as a share, with the labels it drew, and excluded
    from precision and recall.
  * y = "this was really her". The positive class is `unusual`, because that is
    the one that costs us: a false positive puts a sentence on a public page
    about a ship that did nothing.

Deterministic: the same --seed samples the same twenty, so a rerun after a
threshold change is comparable to the last one.

Run:  python3 ops/label/label.py sample [--seed 7] [--days 14]
      python3 ops/label/label.py report
      CLICKHOUSE_URL=... to point somewhere other than the dev box.
"""

from __future__ import annotations

import csv
import json
import os
import sys
import urllib.request
from datetime import UTC, datetime
from functools import cache
from math import cos, dist, radians
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pipeline"))
from ais_pipeline.detectors.coverage import (  # noqa: E402
    CLASS_COVERAGE,
    CLASS_UNKNOWN,
    CLASS_UNUSUAL,
    COVERAGE_QUERY,
    MAX_CENTRE_DEG,
    CoverageModel,
    Verdict,
    build,
)
from ais_pipeline.refinery.symbology import class_of  # noqa: E402

CLICKHOUSE_URL = os.environ.get(
    "CLICKHOUSE_URL", "http://localhost:8123/?user=ais&password=ais-dev&database=ais"
)
HERE = Path(__file__).parent
N_CASES = 20
TRACK_FIXES = 5  # per side; enough to read a heading and a speed, short enough to skim
NEIGHBOUR_BOX_DEG = 0.1  # ~11 km of latitude — the scale the classifier itself works at
NEIGHBOUR_WINDOW_MIN = 30


def query(sql: str) -> list[list[str]]:
    with urllib.request.urlopen(CLICKHOUSE_URL, data=sql.encode(), timeout=60) as r:  # noqa: S310
        return [line.split("\t") for line in r.read().decode().strip().splitlines() if line]


def _points(coords: object) -> list[list[float]]:
    """Flatten Polygon or MultiPolygon nesting down to [lon, lat] pairs."""
    if isinstance(coords, list) and coords and isinstance(coords[0], (int, float)):
        return [coords]  # type: ignore[list-item]
    return [p for c in coords for p in _points(c)]  # type: ignore[union-attr]


@cache
def ports() -> list[tuple[str, float, float]]:
    """(name, lat, lon) centroids of the hand-drawn polygons. Only ever used to say
    "38 km from Rotterdam" — a bearing on a chart, not a containment test."""
    out = []
    for f in json.loads((HERE.parent / "geo" / "ports.geojson").read_text())["features"]:
        pts = _points(f["geometry"]["coordinates"])
        out.append((f["properties"]["name"],
                    sum(p[1] for p in pts) / len(pts), sum(p[0] for p in pts) / len(pts)))
    return out


def nearest_port(lat: float, lon: float, ps: list[tuple[str, float, float]]) -> str:
    def km(p: tuple[str, float, float]) -> float:
        return dist((p[1] * 111.32, p[2] * 111.32 * cos(radians(lat))),
                    (lat * 111.32, lon * 111.32 * cos(radians(lat))))
    name, plat, plon = min(ps, key=km)
    return f"{km((name, plat, plon)):.0f} km from {name}"


# Prefer gaps the classifier actually ruled on; cityHash64 over the id and the seed
# is a stable shuffle, so the same seed draws the same twenty forever.
CASES_SQL = """
SELECT event_id, mmsi, t_start, t_end, toString(meta.classification),
       toString(meta.confidence), toString(meta.cell_occupancy),
       toString(meta.cell_interval_s), toString(meta.neighbors_online),
       toUInt32(toString(meta.duration_s))
FROM events
WHERE kind = 'gap' AND t_start > now() - INTERVAL {days} DAY
  AND toString(meta.classification) {filter}
ORDER BY cityHash64(toString(event_id), '{seed}') LIMIT {n} FORMAT TSV
"""


def cases(days: int, seed: str) -> tuple[list[list[str]], bool]:
    """Twenty gaps, decided ones first. False = we had to fall back to abstentions."""
    decided = query(CASES_SQL.format(
        days=days, seed=seed, n=N_CASES,
        filter=f"IN ('{CLASS_UNUSUAL}', '{CLASS_COVERAGE}')"))
    if len(decided) >= N_CASES:
        return decided, True
    return query(CASES_SQL.format(days=days, seed=seed, n=N_CASES, filter="!= ''")), False


def context(mmsi: str, t_start: str, t_end: str) -> str:
    """One screen: who she is, where she stopped, her track either side, her company."""
    ident = query(f"""SELECT name, ship_type FROM vessels_static WHERE mmsi = {mmsi}
                      ORDER BY ts DESC LIMIT 1 FORMAT TSV""")
    name, stype = ident[0] if ident else ("(no static report)", "0")
    before = query(f"""SELECT ts, lat, lon, sog FROM positions WHERE mmsi = {mmsi}
                       AND ts <= '{t_start}' ORDER BY ts DESC LIMIT {TRACK_FIXES} FORMAT TSV""")
    after = query(f"""SELECT ts, lat, lon, sog FROM positions WHERE mmsi = {mmsi}
                      AND ts >= '{t_end}' ORDER BY ts LIMIT {TRACK_FIXES} FORMAT TSV""")
    lines = [f"**{name or '(unnamed)'}** · {class_of(int(stype))} · mmsi {mmsi}"]
    if not before:
        return "\n".join([*lines, "", "_No fix before the gap — nothing to judge on._"])
    _, lat, lon, _ = before[0]
    ps = ports()
    lines += ["", (f"Last seen {before[0][0]} at {float(lat):.4f}, {float(lon):.4f} "
                   f"— {nearest_port(float(lat), float(lon), ps)}"), ""]
    neigh = query(f"""SELECT uniqExact(mmsi) FROM positions
        WHERE ts BETWEEN '{t_start}' - INTERVAL {NEIGHBOUR_WINDOW_MIN} MINUTE
                     AND '{t_start}' + INTERVAL {NEIGHBOUR_WINDOW_MIN} MINUTE
          AND lat BETWEEN {float(lat) - NEIGHBOUR_BOX_DEG} AND {float(lat) + NEIGHBOUR_BOX_DEG}
          AND lon BETWEEN {float(lon) - NEIGHBOUR_BOX_DEG} AND {float(lon) + NEIGHBOUR_BOX_DEG}
          AND mmsi != {mmsi} FORMAT TSV""")
    lines.append(f"Other ships heard in that ±{NEIGHBOUR_BOX_DEG}° box, ±"
                 f"{NEIGHBOUR_WINDOW_MIN} min: **{neigh[0][0] if neigh else 0}**")
    for title, rows in (("Before", list(reversed(before))), ("After", after)):
        lines += ["", f"{title}:"]
        lines += [f"  {ts}  {float(la):.4f}, {float(lo):.4f}  {float(s):.1f} kn"
                  for ts, la, lo, s in rows] or ["  (nothing)"]
    return "\n".join(lines)


@cache
def coverage_model() -> CoverageModel:
    """The same model the live classifier builds, from the same query. Built once."""
    return build(query(COVERAGE_QUERY + " FORMAT TSV"))


def replay(mmsi: str, t_start: str) -> Verdict | None:
    """What the classifier WOULD have said about this gap, computed now.

    Every gap older than the classifier's deploy carries `coverage-unknown`,
    because the verdict is stamped when a gap opens. Waiting a week for live
    verdicts to accumulate is a week not spent calibrating, and the classifier
    is pure — the same inputs are still in the lake, so we can just ask it.
    """
    last = query(f"""SELECT lat, lon, sog FROM positions WHERE mmsi = {mmsi}
                     AND ts <= '{t_start}' ORDER BY ts DESC LIMIT 1 FORMAT TSV""")
    if not last:
        return None
    model = coverage_model()
    lat, lon, sog = float(last[0][0]), float(last[0][1]), float(last[0][2])
    centre = model.cell_of(lat, lon)
    if centre is None:
        return model.classify(lat, lon, 0)  # no cell: an abstention either way
    # Neighbours = other ships heard in the SAME cell in the half hour before she
    # went quiet. The box is a superset of the cell (a point is never further than
    # MAX_CENTRE_DEG from its centre); cell_of does the real filtering.
    # ponytail: one arbitrary fix per ship decides her cell. A ship crossing the
    # boundary in those 30 min is a coin flip — fine at 3 neighbours saturation.
    span = MAX_CENTRE_DEG / max(0.1, cos(radians(lat)))
    rows = query(f"""SELECT mmsi, any(lat), any(lon) FROM positions
        WHERE ts BETWEEN '{t_start}' - INTERVAL {NEIGHBOUR_WINDOW_MIN} MINUTE AND '{t_start}'
          AND lat BETWEEN {lat - MAX_CENTRE_DEG} AND {lat + MAX_CENTRE_DEG}
          AND lon BETWEEN {lon - span} AND {lon + span}
          AND mmsi != {mmsi} GROUP BY mmsi FORMAT TSV""")
    online = sum(1 for _, la, lo in rows if model.cell_of(float(la), float(lo)) == centre)
    verdict = model.classify(lat, lon, online)
    # Mirror of the machine's underway demotion (machine.py::_verdict): a ship
    # making way carries her silence out of the cell. The machine reads motion;
    # here the last fix's sog stands in for it (3 kn: below every labelled true
    # positive's speed, above every false one's — ops/label/report.md).
    if verdict.classification == CLASS_UNUSUAL and 3.0 <= sog < 102.3:  # 102.3 = sog n/a
        return Verdict(CLASS_UNKNOWN, 0.0, {**verdict.stats, "demoted": "underway"})
    return verdict


def sample(days: int, seed: str) -> None:
    rows, decided = cases(days, seed)
    if not rows:
        sys.exit("No gap events in the window. Let the detector run first.")
    head = [f"# {len(rows)} gaps to label — seed {seed}, last {days} days\n",
            ("For each case: was the silence **hers** (y — she went quiet where we could "
             "hear her) or **ours** (n — she sailed out of reception)? Put y or n in the "
             "`label` column of labels.csv, then run `report`.\n")]
    if not decided:
        head.append("> Not enough classified gaps to fill the sample, so this draw "
                    "includes abstentions (`coverage-unknown`). They still deserve a "
                    "label — the report scores them separately, as abstentions.\n")
    body = []
    with (HERE / "labels.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["event_id", "mmsi", "t_start", "duration_h", "classification",
                    "confidence", "replayed_classification", "replayed_confidence", "label"])
        for i, (eid, mmsi, t0, t1, cls, conf, occ, interval, online, dur) in enumerate(rows, 1):
            v = replay(mmsi, t0) if cls == CLASS_UNKNOWN else None
            w.writerow([eid, mmsi, t0, f"{int(dur) / 3600:.1f}", cls, conf,
                        v.classification if v else "", f"{v.confidence}" if v else "", ""])
            said = (f"**{v.classification}** (replayed) (confidence {v.confidence}; "
                    f"cell occupancy {v.stats.get('cell_occupancy', '—')}, interval "
                    f"{v.stats.get('cell_interval_s', '—')} s, "
                    f"{v.stats['neighbors_online']} neighbours online at the start)"
                    if v else
                    f"**{cls}** (confidence {conf or '—'}; cell occupancy {occ or '—'}, "
                    f"interval {interval or '—'} s, {online or '—'} neighbours online "
                    "at the start)")
            body += [f"\n## {i}. {eid}\n",
                     f"Silent {t0} → {t1} ({int(dur) / 3600:.1f} h)",
                     f"Detector says: {said}\n",
                     context(mmsi, t0, t1), ""]
    (HERE / "cases.md").write_text("\n".join(head + body) + "\n")
    print(f"Wrote {len(rows)} cases to ops/label/cases.md "
          f"and a blank ops/label/labels.csv ({'decided' if decided else 'fallback'} draw).")


def report() -> None:
    rows = list(csv.DictReader((HERE / "labels.csv").open()))
    replayed = 0
    for r in rows:  # a replayed verdict stands in wherever the stored one abstained
        if r["classification"] == CLASS_UNKNOWN and r.get("replayed_classification"):
            r["classification"] = r["replayed_classification"]
            r["confidence"] = r["replayed_confidence"]
            replayed += 1
    labelled = [r for r in rows if r["label"].strip().lower() in ("y", "n")]
    if not labelled:
        sys.exit("No labels in ops/label/labels.csv yet — fill the `label` column with y/n.")
    scored = [r for r in labelled if r["classification"] != CLASS_UNKNOWN]
    abstained = [r for r in labelled if r["classification"] == CLASS_UNKNOWN]

    def n(pred: str, truth: str) -> int:
        return sum(1 for r in scored
                   if r["classification"] == pred and r["label"].strip().lower() == truth)

    tp, fp = n(CLASS_UNUSUAL, "y"), n(CLASS_UNUSUAL, "n")
    fn, tn = n(CLASS_COVERAGE, "y"), n(CLASS_COVERAGE, "n")
    prec = tp / (tp + fp) if tp + fp else float("nan")
    rec = tp / (tp + fn) if tp + fn else float("nan")
    out = [f"# Coverage classifier vs {len(labelled)} hand labels",
           (f"\n_{datetime.now(UTC):%Y-%m-%d %H:%M} UTC · positive class = `unusual` "
            "(the silence was hers)._\n"),
           (f"Labelled {len(labelled)} of {len(rows)} sampled. "
            f"{len(abstained)} ({len(abstained) / len(labelled):.0%}) were "
            f"`{CLASS_UNKNOWN}` — the classifier declined, so they score neither way.\n"),
           (f"{replayed} of {len(rows)} verdicts were replayed offline (the gap predates "
            "the classifier); they are scored like any other.\n" if replayed else ""),
           f"**Precision {prec:.2f}** · **Recall {rec:.2f}** on the {len(scored)} it ruled on.\n",
           "| | labelled y (hers) | labelled n (ours) |",
           "|---|---|---|",
           f"| said `{CLASS_UNUSUAL}` | {tp} | {fp} |",
           f"| said `{CLASS_COVERAGE}` | {fn} | {tn} |",
           (f"| abstained | {sum(1 for r in abstained if r['label'].strip().lower() == 'y')} "
            f"| {sum(1 for r in abstained if r['label'].strip().lower() == 'n')} |"),
           "\n## Per case\n",
           "| mmsi | t_start | h | said | conf | label | |",
           "|---|---|---|---|---|---|---|"]
    for r in labelled:
        lab = r["label"].strip().lower()
        agree = ("—" if r["classification"] == CLASS_UNKNOWN else
                 "ok" if (r["classification"] == CLASS_UNUSUAL) == (lab == "y") else "**miss**")
        out.append(f"| {r['mmsi']} | {r['t_start']} | {r['duration_h']} | "
                   f"{r['classification']} | {r['confidence'] or '—'} | {lab} | {agree} |")
    text = "\n".join(out) + "\n"
    (HERE / "report.md").write_text(text)
    print(text)


def main() -> None:
    argv = sys.argv[1:]
    mode = argv[0] if argv else ""

    def arg(flag: str, default: str) -> str:
        return argv[argv.index(flag) + 1] if flag in argv else default

    if mode == "sample":
        sample(int(arg("--days", "14")), arg("--seed", "ais-m3"))
    elif mode == "report":
        report()
    else:
        sys.exit(__doc__.split("Run:")[1].strip())


def selfcheck() -> None:
    """The one runnable check on the scoring: six hand-built rows, known answer.

    Run: python3 ops/label/label.py selfcheck
    """
    base = {"mmsi": "1", "t_start": "t", "duration_h": "1",
            "confidence": "1.0", "replayed_classification": "", "replayed_confidence": ""}
    rows = [
        {**base, "classification": CLASS_UNUSUAL, "label": "y"},   # tp
        {**base, "classification": CLASS_UNUSUAL, "label": "n"},   # fp
        {**base, "classification": CLASS_COVERAGE, "label": "y"},  # fn
        {**base, "classification": CLASS_COVERAGE, "label": "n"},  # tn
        {**base, "classification": CLASS_UNKNOWN, "label": "y"},   # abstains
        {**base, "classification": CLASS_UNKNOWN, "label": "y",    # replay decides
         "replayed_classification": CLASS_UNUSUAL, "replayed_confidence": "0.9"},
    ]
    replayed = 0
    for r in rows:
        if r["classification"] == CLASS_UNKNOWN and r.get("replayed_classification"):
            r["classification"] = r["replayed_classification"]
            replayed += 1
    scored = [r for r in rows if r["classification"] != CLASS_UNKNOWN]
    tp = sum(1 for r in scored if r["classification"] == CLASS_UNUSUAL and r["label"] == "y")
    fp = sum(1 for r in scored if r["classification"] == CLASS_UNUSUAL and r["label"] == "n")
    fn = sum(1 for r in scored if r["classification"] == CLASS_COVERAGE and r["label"] == "y")
    assert (tp, fp, fn, replayed) == (2, 1, 1, 1), (tp, fp, fn, replayed)
    assert abs(tp / (tp + fp) - 2 / 3) < 1e-9 and abs(tp / (tp + fn) - 2 / 3) < 1e-9
    print("selfcheck: ok")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "selfcheck":
        selfcheck()
    else:
        main()
