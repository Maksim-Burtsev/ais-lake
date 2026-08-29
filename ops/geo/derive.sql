-- Trace the twelve launch ports round the water where ships actually stop.
--
-- Input: a TEMP TABLE `stationary(lat, lon, ships, ship_hours)`, loaded by
-- derive.sh from ops/geo/stationary.sql. Output: one GeoJSON FeatureCollection on
-- stdout, the shape prompts/M3-detectors.md M3-H1 asks for.
--
-- All geometry happens in EPSG:3035 (ETRS89 / LAEA Europe), where a metre is a
-- metre — 4326 degrees are not, and every threshold below is a distance.
--
-- The honest caveat, repeated in candidates.md: this draws where ships STOP, not
-- the legal boundary of a port authority. For "did she arrive at Rotterdam" that
-- is the better definition. For a page claiming to be about the Port of Rotterdam
-- it is a near miss, and M5 should know it.

\set ON_ERROR_STOP on

-- ---------------------------------------------------------------- thresholds --
-- :reach   how far from a port centre a mooring can be and still be that port's
-- :eps     DBSCAN neighbourhood — how close two cells must be to be one place
-- :minpts  cells needed to make a cluster; below this it is one ship's habit
-- :pad     buffer round the traced hull: a line of quay cells has no width
-- :tol     simplification, well under the pad so corners survive
-- :anchor  share of a cluster's stationary hours reporting nav_status 1 above
--          which it is an anchorage rather than berths
-- :inner   how far berths may be from the centre. Anchorages may be much further,
--          which is why the two are not one number.
-- :dwell   ship-hours an outlying anchorage needs before it is more than a lay-by

-- ------------------------------------------------------------------- the 12 --
-- From ops/geo/candidates.md, ranked out of the lake by rank.py. Centres are the
-- busiest stationary cell of each complex.
CREATE TEMP TABLE port (locode text, name text, lat float8, lon float8);
INSERT INTO port VALUES
    ('NLRTM', 'Rotterdam',   51.8822,  4.2768),
    ('BEANR', 'Antwerp',     51.2646,  4.3382),
    ('NLAMS', 'Amsterdam',   52.3787,  4.8942),
    ('DEHAM', 'Hamburg',     53.5443,  9.9681),
    ('NLTNZ', 'Terneuzen',   51.3331,  3.8213),
    ('NLIJM', 'IJmuiden',    52.4602,  4.5893),
    ('NLDZL', 'Delfzijl',    53.3180,  6.9350),
    ('BEGNE', 'Ghent',       51.1509,  3.7864),
    ('DEBRV', 'Bremerhaven', 53.5168,  8.5785),
    ('NLHAR', 'Harlingen',   53.1760,  5.4141),
    ('NLDHR', 'Den Helder',  52.9585,  4.7810),
    ('NLVLI', 'Vlissingen',  51.4451,  3.5937);

CREATE TEMP TABLE port_m AS
SELECT locode, name,
       ST_Transform(ST_SetSRID(ST_MakePoint(lon, lat), 4326), 3035) AS g
FROM port;

-- --------------------------------------------- cells, tagged with their port --
-- Nearest of the twelve, and only if it is within reach: the lake is full of
-- moorings on the Rhine and in the Baltic that belong to none of them.
CREATE TEMP TABLE cell AS
SELECT p.locode, s.ships, s.ship_hours, s.anchor_hours, s.g
FROM (
    SELECT ships, ship_hours, anchor_hours,
           ST_Transform(ST_SetSRID(ST_MakePoint(lon, lat), 4326), 3035) AS g
    FROM stationary
) AS s
CROSS JOIN LATERAL (
    SELECT locode, port_m.g AS centre FROM port_m ORDER BY port_m.g <-> s.g LIMIT 1
) AS p
WHERE ST_Distance(s.g, p.centre) <= :reach;

-- ------------------------------------------------------------- the clusters --
-- Two passes, because a quay and an anchorage are not the same shape of thing and
-- one neighbourhood cannot find both.
--
-- A moored ship sits still, so berths are dense: many ships stacked into few cells,
-- 800 m apart at most. A ship at anchor SWINGS around her anchor and drifts with
-- the tide, so an anchorage is sparse and wide — the Maas anchorages are 122 cells
-- smeared over 20 km at roughly one ship each, and at eps 800 m they form no
-- cluster at all. That is why the first cut of this file produced twelve ports and
-- not one anchorage.
--
-- Which cell is which comes from the crews: nav_status 1 is "at anchor", and while
-- one ship's field is unreliable, in bulk the signal is unambiguous. Measured over
-- the Rotterdam complex, the offshore anchorage reports 73% at-anchor and the
-- Botlek quays, the Maasvlakte terminals and the Dordrecht barge moorings all
-- report 0-1%.
CREATE TEMP TABLE kinded AS
SELECT locode, ships, ship_hours, anchor_hours, g,
       CASE WHEN anchor_hours::float8 / ship_hours >= :anchor
            THEN 'anchorage' ELSE 'port' END AS kind
FROM cell;

CREATE TEMP TABLE clustered AS
SELECT locode, kind, ships, ship_hours, anchor_hours, g,
       ST_ClusterDBSCAN(g, eps := :eps, minpoints := :minpts)
           OVER (PARTITION BY locode) AS cid
FROM kinded WHERE kind = 'port'
UNION ALL
SELECT locode, kind, ships, ship_hours, anchor_hours, g,
       ST_ClusterDBSCAN(g, eps := :eps_anchor, minpoints := :minpts_anchor)
           OVER (PARTITION BY locode) AS cid
FROM kinded WHERE kind = 'anchorage';

-- ------------------------------------------------------------- their shapes --
-- ConcaveHull at 0.85 hugs a quay line without swallowing the water between two
-- arms of a harbour; the buffer then gives that line a width to be a polygon at all.
CREATE TEMP TABLE shape AS
SELECT c.locode, c.kind, c.cid,
       sum(c.ships)        AS ships,
       sum(c.ship_hours)   AS ship_hours,
       sum(c.anchor_hours) AS anchor_hours,
       count(*)            AS cells,
       ST_SimplifyPreserveTopology(
           ST_Buffer(ST_ConcaveHull(ST_Collect(c.g), 0.85), :pad), :tol) AS g
FROM clustered AS c
WHERE c.cid IS NOT NULL
GROUP BY c.locode, c.kind, c.cid;

-- ------------------------------------------------------------ what to keep --
-- Distance now does the job it is actually good at, and asymmetrically. Berths
-- belong to the port they sit in, so a berth cluster far up-river is some other
-- port's — Dordrecht's barges are not Rotterdam's — and is dropped. An anchorage
-- may lie far offshore, so those are kept to the full reach provided ships really
-- wait in them.
CREATE TEMP TABLE labelled AS
SELECT s.locode, p.name, s.kind, s.cid, s.ships, s.ship_hours, s.anchor_hours,
       s.cells, s.g, ST_Distance(s.g, p.g) AS from_centre
FROM shape AS s JOIN port_m AS p USING (locode)
WHERE CASE WHEN s.kind = 'anchorage' THEN s.ship_hours >= :dwell
           ELSE ST_Distance(s.g, p.g) <= :inner END;

-- ------------------------------------------------------------------- one shape --
-- A port is not one blob: Rotterdam is the city quays, Europoort, the Botlek and
-- the Waalhaven, and a union of them is the honest geometry. Emitted as whatever
-- ST_Union returns — Polygon where the blobs touch, MultiPolygon where they do
-- not. docs/02 §03 declares ports.geom as Polygon; that is a contract to settle
-- when M3-T1 writes the migration, not something to distort the coastline for.
CREATE TEMP TABLE final AS
SELECT locode, name, kind,
       sum(ships)        AS ships,
       sum(ship_hours)   AS ship_hours,
       sum(anchor_hours) AS anchor_hours,
       count(*)          AS blobs,
       ST_Transform(ST_Union(g), 4326) AS g
FROM labelled
GROUP BY locode, name, kind;

-- `final` is the answer: one row per (port, kind). derive.sh decides what to do
-- with it — the GeoJSON for ports.geojson, or the shape table for tuning.
