-- The lake: raw positions, static data, latest-state, events.
-- Source of truth: docs/02-architecture.html §03 — change the doc first.

-- migrator:up
-- @stmt
CREATE TABLE IF NOT EXISTS positions (
    ts DateTime64(0),
    mmsi UInt32,
    lat Float64 CODEC (Delta, ZSTD),
    lon Float64 CODEC (Delta, ZSTD),
    sog Float32,               -- knots ×10 fits UInt16; keep Float for clarity
    cog Float32,
    heading UInt16,            -- 511 = n/a
    nav_status UInt8,
    msg_type UInt8,
    src LowCardinality(String)
) ENGINE = MergeTree
  PARTITION BY toYYYYMM(ts)
  ORDER BY (mmsi, ts)
  TTL toDateTime(ts) + INTERVAL 90 DAY  -- raw window; positions_5m keeps 1/5min forever

-- @stmt
CREATE TABLE IF NOT EXISTS vessels_static (  -- type 5/24, latest wins
    mmsi UInt32,
    imo UInt32,
    name String,
    callsign String,
    ship_type UInt16,
    dim_a UInt16,
    dim_b UInt16,
    dim_c UInt8,
    dim_d UInt8,
    draught Float32,
    destination String,
    eta String,
    ts DateTime
) ENGINE = ReplacingMergeTree(ts) ORDER BY mmsi

-- @stmt
CREATE TABLE IF NOT EXISTS vessel_latest (  -- one row per ship for map cold-start & cards
    mmsi UInt32,
    ts DateTime,
    lat Float64,
    lon Float64,
    sog Float32,
    cog Float32,
    heading UInt16,
    nav_status UInt8,
    state Enum8('underway' = 1, 'anchored' = 2, 'moored' = 3, 'silent' = 4),
    sentence String            -- server-rendered status sentence
) ENGINE = ReplacingMergeTree(ts) ORDER BY mmsi

-- @stmt
CREATE TABLE IF NOT EXISTS events (
    event_id UUID,
    mmsi UInt32,
    kind Enum8('port_call' = 1, 'anchorage' = 2, 'gap' = 3, 'load_delta' = 4, 'departure' = 5),
    t_start DateTime,
    t_end Nullable(DateTime),
    port LowCardinality(String),  -- UN/LOCODE or ''
    meta JSON                     -- duration, classification, confidence, context
) ENGINE = MergeTree ORDER BY (mmsi, t_start)

-- migrator:down
-- @stmt
DROP TABLE IF EXISTS events

-- @stmt
DROP TABLE IF EXISTS vessel_latest

-- @stmt
DROP TABLE IF EXISTS vessels_static

-- @stmt
DROP TABLE IF EXISTS positions
