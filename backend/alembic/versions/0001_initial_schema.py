"""Initial schema: PostGIS, pg_trgm, unaccent, villages, dem_assets, analyses

Revision ID: 0001
Revises:
Create Date: M0-7
"""
from __future__ import annotations

import geoalchemy2
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # postgis: geometry types.  pg_trgm + unaccent: fuzzy village-name search,
    # which HLD CH-24 needs for Indian transliteration variance.
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")

    op.create_table(
        "villages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("lgd_code", sa.String(20), unique=True),
        sa.Column("census_2011_id", sa.String(20)),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("name_normalised", sa.String(200), nullable=False),
        sa.Column("block", sa.String(200)),
        sa.Column("district", sa.String(200)),
        sa.Column("state", sa.String(200)),
        sa.Column("geom", geoalchemy2.Geometry("MULTIPOLYGON", srid=4326), nullable=False),
        sa.Column("centroid", geoalchemy2.Geometry("POINT", srid=4326)),
        sa.Column("area_ha", sa.Float()),
        sa.Column("source", sa.String(50), server_default="shrug"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_villages_lgd_code", "villages", ["lgd_code"])
    op.create_index("ix_villages_census_2011_id", "villages", ["census_2011_id"])
    op.create_index("ix_villages_name_normalised", "villages", ["name_normalised"])
    op.create_index("ix_villages_district", "villages", ["district"])
    op.create_index("ix_villages_state", "villages", ["state"])
    op.create_index("ix_villages_state_district", "villages", ["state", "district"])
    # Trigram index: makes ILIKE '%rampur%' and similarity() fast on ~600k rows.
    op.execute(
        "CREATE INDEX ix_villages_name_trgm ON villages "
        "USING gin (name_normalised gin_trgm_ops)"
    )

    op.create_table(
        "dem_assets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("cache_key", sa.String(64), nullable=False, unique=True),
        sa.Column("product", sa.String(40), nullable=False),
        sa.Column("source", sa.String(40), nullable=False),
        sa.Column("bbox", geoalchemy2.Geometry("POLYGON", srid=4326), nullable=False),
        sa.Column("epsg", sa.Integer(), nullable=False),
        sa.Column("resolution_m", sa.Float(), nullable=False),
        sa.Column("width_px", sa.Integer()),
        sa.Column("height_px", sa.Integer()),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.Integer()),
        sa.Column("checksum_sha256", sa.String(64)),
        sa.Column("stats", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_dem_assets_cache_key", "dem_assets", ["cache_key"])

    op.create_table(
        "analyses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("village_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("villages.id", ondelete="SET NULL")),
        sa.Column("job_id", sa.String(64), unique=True),
        sa.Column("state", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("progress_pct", sa.Integer(), server_default="0"),
        sa.Column("current_step", sa.String(60)),
        sa.Column("params", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("result", postgresql.JSONB()),
        sa.Column("sources", postgresql.JSONB()),
        sa.Column("warnings", postgresql.JSONB()),
        sa.Column("error", postgresql.JSONB()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_analyses_job_id", "analyses", ["job_id"])
    op.create_index("ix_analyses_state", "analyses", ["state"])
    op.create_index("ix_analyses_state_created", "analyses", ["state", "created_at"])


def downgrade() -> None:
    op.drop_table("analyses")
    op.drop_table("dem_assets")
    op.execute("DROP INDEX IF EXISTS ix_villages_name_trgm")
    op.drop_table("villages")
    # Extensions are deliberately not dropped: other databases may share them.
