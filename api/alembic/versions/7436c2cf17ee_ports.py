"""ports

The 12 hand-drawn port polygons (M3-H1) and their anchorages, one row per locode.
Raw SQL: the geometry types are PostGIS's, and geoalchemy2 would be a dependency
bought for four column definitions.

Revision ID: 7436c2cf17ee
Revises:
"""

from collections.abc import Sequence

from alembic import op

revision: str = "7436c2cf17ee"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.execute("""
        CREATE TABLE ports (
            locode      text PRIMARY KEY,
            name        text NOT NULL,
            kind        text NOT NULL,
            geom        geometry(MultiPolygon, 4326) NOT NULL,
            anchorages  geometry(MultiPolygon, 4326),
            verified    boolean NOT NULL DEFAULT false,
            notes       text
        )
    """)
    op.execute("CREATE INDEX ports_geom_gist ON ports USING GIST (geom)")
    op.execute("CREATE INDEX ports_anchorages_gist ON ports USING GIST (anchorages)")


def downgrade() -> None:
    # The extension stays: dropping it would take every other PostGIS object with it.
    op.execute("DROP TABLE ports")
