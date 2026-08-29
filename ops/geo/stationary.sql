-- Where ships stop. The input to derive.sql, which traces the polygons round it.
--
-- A berth and an anchorage look the same from here: a ship that reported four or
-- more times in an hour and never exceeded half a knot was not going anywhere.
-- Collapsing to (mmsi, hour) first means a ship moored for three days counts as
-- 72 ship-hours rather than 40 000 near-identical fixes, so one big vessel cannot
-- outvote a busy quay.
--
-- Out: one row per ~100 m cell — lat, lon, distinct ships, ship-hours, and the
-- ship-hours whose crew reported nav_status 1, "at anchor". That last column is
-- what tells a berth from an anchorage, and it is not close: measured over the
-- Rotterdam complex, the offshore Maas anchorage runs 73% at-anchor while the
-- Botlek quays, the Maasvlakte terminals and the Dordrecht barge moorings all run
-- 0-1%. Individually the field is unreliable; over a whole cluster it is the
-- crews telling us what they are doing.
-- Cell size: round(lat,3) is 111 m, round(lon,3) is 69 m at 52°N. Finer than the
-- quays we are drawing and coarse enough to keep the point set near 25k.
--
-- The bbox is LAUNCH_BBOX from pipeline/ais_pipeline/config.py, repeated here
-- because ClickHouse cannot read it. If that box moves, this line moves with it.

SELECT round(la, 3)   AS lat,
       round(lo, 3)   AS lon,
       uniqExact(mmsi)  AS ships,
       count()          AS ship_hours,
       countIf(nav = 1) AS anchor_hours
FROM (
    SELECT mmsi,
           toStartOfHour(ts) AS hour,
           avg(lat)          AS la,
           avg(lon)          AS lo,
           max(sog)          AS fastest,
           count()           AS reports,
           any(nav_status)   AS nav
    FROM positions
    WHERE lon BETWEEN -6.5 AND 13.0
      AND lat BETWEEN 49.0 AND 61.5
    GROUP BY mmsi, hour
)
-- 0.5 kn is the same threshold refinery/state.py calls "under way"; four reports
-- is enough that a single stale fix cannot claim an hour.
WHERE fastest < 0.5 AND reports >= 4
GROUP BY lat, lon
-- No floor on ships-per-cell, and that is deliberate. A moored ship sits in one
-- cell, so a busy quay stacks many ships into few cells — but a ship at ANCHOR
-- swings around her anchor and smears across a dozen cells at one ship each. A
-- ships >= 2 floor therefore deletes exactly the anchorages it looks harmless to:
-- measured, single-ship cells carry 84% of all at-anchor hours in the region.
-- Rejecting noise is DBSCAN's job over in derive.sql, where minpoints can see
-- that three lonely cells sitting together are one place.
HAVING ship_hours >= 2
FORMAT TSV
