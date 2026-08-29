#!/bin/sh
# Trace the twelve launch ports round the water where ships actually stop, and
# write ops/geo/ports.geojson — the file M3-H1 asks a human to draw in QGIS.
#
#   ClickHouse (stationary.sql)  ->  PostGIS (derive.sql)  ->  GeoJSON
#
# Both stores are already in compose and PostGIS already carries GEOS, so this
# adds no dependency of any kind. Everything in Postgres happens in TEMP tables
# inside one session: the app database keeps exactly the schema alembic gave it
# (CLAUDE.md — migrations only, never ad-hoc DDL).
#
# Re-run it after the lake grows, or after editing a threshold below, and diff
# the result. The thresholds ARE the design; each is argued in derive.sql.
#
#   ./ops/geo/derive.sh              # writes ops/geo/ports.geojson
#   ./ops/geo/derive.sh --stats      # prints the shape table instead, for tuning
set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH= cd -- "$HERE/../.." && pwd)
CLICKHOUSE_URL=${CLICKHOUSE_URL:-'http://localhost:8123/?user=ais&password=ais-dev&database=ais'}

# reach   45 km  how far from a centre a mooring can still belong to that port —
#                wide, because the Maas anchorages lie 35 km offshore
# eps    800 m   two berth cells closer than this are one quay
# minpts   3     fewer cells than this is one ship's habit, not a berth
# eps_anchor / minpts_anchor  the same for anchorages, which are sparse and wide:
#                a swinging ship smears over many cells at one ship each, so 800 m
#                finds nothing out there
# pad    200 m   a line of quay cells has no width until it is buffered
# tol     60 m   simplification, well under the pad so corners survive
# anchor  0.30   share of a cluster's hours reported nav_status 1 to call it an
#                anchorage. The measured gap is 73% against 0-1%, so anything
#                between .2 and .5 picks the same shapes
# inner   15 km  how far BERTHS may be from the centre; beyond it they are some
#                other port's quays, not this one's
# dwell  100 h   an outlying anchorage needs real waiting to be more than a lay-by
SET="-v reach=45000 -v eps=800 -v minpts=3 -v eps_anchor=2500 -v minpts_anchor=4 -v pad=200 -v tol=60 -v anchor=0.30 -v inner=15000 -v dwell=100"

TAIL='SELECT locode, name, kind, ships, ship_hours, anchor_hours, blobs,
             ST_NumGeometries(g) AS parts,
             round((ST_Area(ST_Transform(g, 3035)) / 1e6)::numeric, 1) AS km2,
             ST_IsValid(g) AS valid
      FROM final ORDER BY locode, kind;'
# One FeatureCollection. The counts ride along so a reviewer can see how much
# evidence sits under each shape, and so the next run can be diffed against this one.
GEOJSON="SELECT json_build_object(
    'type', 'FeatureCollection',
    'features', coalesce(json_agg(json_build_object(
        'type', 'Feature',
        'geometry', ST_AsGeoJSON(g, 6)::json,
        'properties', json_build_object(
            'locode', locode, 'name', name, 'kind', kind, 'ships', ships,
            'ship_hours', ship_hours, 'anchor_hours', anchor_hours, 'blobs', blobs)
    ) ORDER BY locode, kind), '[]'::json))::text
FROM final;"

OUT=""
case "${1:-}" in
    --stats) ;;
    "")      OUT="$ROOT/ops/geo/ports.geojson"; TAIL="$GEOJSON" ;;
    *)       echo "usage: $0 [--stats]" >&2; exit 2 ;;
esac

stationary=$(mktemp)
trap 'rm -f "$stationary"' EXIT
curl -sSf "$CLICKHOUSE_URL" --data-binary "@$HERE/stationary.sql" > "$stationary"
echo "stationary cells: $(wc -l < "$stationary" | tr -d ' ')" >&2

run() {
    {
        echo 'CREATE TEMP TABLE stationary (lat float8, lon float8, ships int, ship_hours int, anchor_hours int);'
        echo 'COPY stationary FROM STDIN;'
        cat "$stationary"
        printf '\\.\n'
        cat "$HERE/derive.sql"
        [ -n "$TAIL" ] && echo "$TAIL"
    } | (cd "$ROOT" && docker compose exec -T postgres psql -U ais -d ais -q $SET "$@")
}

if [ -n "$OUT" ]; then
    run -tA > "$OUT.tmp"
    # psql -tA still prints the notices-free result rows; the geojson is the last one
    tail -n 1 "$OUT.tmp" > "$OUT"
    rm -f "$OUT.tmp"
    echo "wrote $OUT ($(wc -c < "$OUT" | tr -d ' ') bytes)" >&2
else
    run
fi
