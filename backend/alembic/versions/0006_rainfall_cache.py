"""Rainfall cache, keyed on the source's own grid cell

Revision ID: 0006
Revises: 0005
Create Date: M4-5, M4-15

Open-Meteo enforces a daily request limit and it gets hit -- repeatedly, during
development, to the point where the analysis could not reach a rainfall tier at
all until a second source was added. Thirty years of daily rainfall for a point
does not change; re-fetching it is pure waste that eventually costs the whole
feature.

**The key is the source's grid cell, not the requested coordinate.** This is the
whole design and getting it wrong makes the cache useless rather than merely
imperfect: ERA5-Land is a 0.1-degree reanalysis, so every point inside one cell
gets the *same* series back. Keying on the exact lon/lat asked for would store a
fresh copy per query and never hit -- two clicks 200 m apart would each fetch
11,000 rows of identical data. Quantising to the source's own resolution turns a
0 % hit rate into a 100 % one for any second query in the same cell.

Row per (source, cell, date) rather than one blob per series, so a cache can be
*extended* when a later year becomes available instead of being invalidated
whole, and so a partial fetch is still worth keeping.

Temperature and reference evapotranspiration ride along because they arrive in the
same responses -- POWER supplies temperature, Open-Meteo supplies ET0 -- and a
cache that dropped them would force a re-fetch for Khosla's cross-check.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rainfall_cache",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        # e.g. "open_meteo_era5_land" or "nasa_power".
        sa.Column("source", sa.String(40), nullable=False),
        # The source's grid cell, quantised to its own resolution: "21.3,81.3"
        # for a 0.1-degree product. Not the coordinate that was asked for.
        sa.Column("cell_key", sa.String(32), nullable=False),
        # The cell's representative point, so a cached series can be placed on a
        # map and its distance from the query point reported.
        sa.Column("cell_lat", sa.Float(), nullable=False),
        sa.Column("cell_lon", sa.Float(), nullable=False),
        sa.Column("observed_on", sa.Date(), nullable=False),
        sa.Column("precipitation_mm", sa.Float(), nullable=False),
        # Nullable because which extras arrive depends on the source: POWER
        # carries temperature and no ET0, Open-Meteo the reverse.
        sa.Column("temperature_c", sa.Float()),
        sa.Column("et0_mm", sa.Float()),
        sa.Column(
            "fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
            nullable=False,
        ),
        # The constraint that makes the cache a cache. Without it a re-fetch
        # appends a second copy of every day and the reads start double-counting
        # rainfall -- which would inflate every runoff figure derived from it.
        sa.UniqueConstraint(
            "source", "cell_key", "observed_on", name="uq_rainfall_cache_cell_day"
        ),
    )
    # The read path always asks for one source and cell over a date range, so the
    # index leads with those and ends on the date.
    op.create_index(
        "ix_rainfall_cache_lookup",
        "rainfall_cache",
        ["source", "cell_key", "observed_on"],
    )
    op.create_index("ix_rainfall_cache_fetched_at", "rainfall_cache", ["fetched_at"])


def downgrade() -> None:
    op.drop_index("ix_rainfall_cache_fetched_at", table_name="rainfall_cache")
    op.drop_index("ix_rainfall_cache_lookup", table_name="rainfall_cache")
    op.drop_table("rainfall_cache")
