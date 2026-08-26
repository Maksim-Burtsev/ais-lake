-- Downsample + density: positions_5m (replay beyond 90d) and density_h3 (rung-0 map).
-- Source of truth: docs/02-architecture.html §03 — change the doc first.

-- migrator:up
-- @stmt
CREATE TABLE IF NOT EXISTS positions_5m (
    mmsi UInt32,
    ts5 DateTime,
    ts DateTime64(0),
    lat Float64,
    lon Float64,
    sog Float32,
    cog Float32,
    heading UInt16,
    nav_status UInt8
) ENGINE = ReplacingMergeTree(ts)  -- newest point wins per bucket
  PARTITION BY toYYYYMM(ts5)
  ORDER BY (mmsi, ts5)

-- @stmt
CREATE MATERIALIZED VIEW IF NOT EXISTS positions_5m_mv TO positions_5m AS
SELECT
    mmsi,
    toStartOfFiveMinutes(ts) AS ts5,
    ts,
    lat,
    lon,
    sog,
    cog,
    heading,
    nav_status
FROM positions

-- @stmt
CREATE TABLE IF NOT EXISTS density_h3 (
    bucket DateTime,
    h3 UInt64,
    cnt SimpleAggregateFunction(sum, UInt64),
    ships AggregateFunction(uniqCombined(12), UInt32)
) ENGINE = AggregatingMergeTree
  PARTITION BY toYYYYMM(bucket)
  ORDER BY (bucket, h3)

-- @stmt
CREATE MATERIALIZED VIEW IF NOT EXISTS density_h3_mv TO density_h3 AS
SELECT
    toStartOfFifteenMinutes(ts) AS bucket,
    geoToH3(lon, lat, 7) AS h3,
    count() AS cnt,
    uniqCombinedState(12)(mmsi) AS ships
FROM positions
GROUP BY bucket, h3

-- migrator:down
-- @stmt
DROP TABLE IF EXISTS density_h3_mv

-- @stmt
DROP TABLE IF EXISTS density_h3

-- @stmt
DROP TABLE IF EXISTS positions_5m_mv

-- @stmt
DROP TABLE IF EXISTS positions_5m
