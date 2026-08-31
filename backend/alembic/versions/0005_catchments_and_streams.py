"""Catchment and stream-network tables

Revision ID: 0005
Revises: 0004
Create Date: M3-11

Storage for delineated catchments and extracted drainage networks, so a stored
analysis can be replayed and re-examined rather than recomputed (HLD NFR-13), and
so spatial questions can be asked of it: which catchments overlap a village
boundary, which channels a proposed pond would sit across.

**Nothing writes to these yet, and that is deliberate.** The contour endpoints
work with no database at all -- a design decision, not an omission -- and wiring
persistence into them would either make PostGIS a requirement for analysing a
KML file or add a conditional path through the one place that most needs to stay
simple. The tables land here because M6 orchestrates persisted analyses, and a
schema is cheaper to have waiting than to add under a running feature.

Two things are recorded that a naive schema would omit, both because they change
how a stored result should be read:

* `conditioning_method` and `cells_filled` -- a catchment delineated over a
  heavily filled surface deserves less confidence than one over terrain that
  drained on its own, and six months later nobody will remember which it was.
* `touches_survey_edge` -- part of the contributing area lay outside the map, so
  the stored area is a lower bound rather than a measurement.
"""

from __future__ import annotations

import geoalchemy2
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "catchments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        # Nullable: a catchment delineated from an uploaded contour map belongs to
        # no stored analysis, and forcing one would mean inventing a row.
        sa.Column("analysis_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("village_id", postgresql.UUID(as_uuid=True), nullable=True),
        # Where the water leaves, and where the caller actually asked -- the two
        # differ by the snap, and only storing the outlet loses the question.
        sa.Column(
            "outlet",
            geoalchemy2.types.Geometry(geometry_type="POINT", srid=4326),
            nullable=False,
        ),
        sa.Column(
            "requested_point", geoalchemy2.types.Geometry(geometry_type="POINT", srid=4326)
        ),
        sa.Column("snap_moved_m", sa.Float()),
        sa.Column(
            "geom",
            geoalchemy2.types.Geometry(geometry_type="MULTIPOLYGON", srid=4326),
            nullable=False,
        ),
        sa.Column("area_ha", sa.Float(), nullable=False),
        sa.Column("perimeter_m", sa.Float()),
        sa.Column("relief_m", sa.Float()),
        sa.Column("mean_slope_pct", sa.Float()),
        sa.Column("longest_flow_path_m", sa.Float()),
        sa.Column("time_of_concentration_min", sa.Float()),
        sa.Column("form_factor", sa.Float()),
        sa.Column("compactness_coefficient", sa.Float()),
        # Provenance that changes how the numbers should be read.
        sa.Column("dem_source", sa.String(40)),
        sa.Column("resolution_m", sa.Float()),
        sa.Column("conditioning_method", sa.String(24)),
        sa.Column("cells_filled", sa.Integer()),
        sa.Column("touches_survey_edge", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("confidence", sa.String(16)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.ForeignKeyConstraint(["analysis_id"], ["analyses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["village_id"], ["villages.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_catchments_analysis_id", "catchments", ["analysis_id"])
    op.create_index("ix_catchments_village_id", "catchments", ["village_id"])

    op.create_table(
        "streams",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("catchment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("analysis_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "geom",
            geoalchemy2.types.Geometry(geometry_type="LINESTRING", srid=4326),
            nullable=False,
        ),
        # One row is one Strahler stream: it runs from where it attains its order
        # to where it loses it, not from junction to junction. Storing segments
        # instead would make the row counts violate Horton's law of stream
        # numbers, which is how the distinction was found in the first place.
        sa.Column("strahler_order", sa.Integer(), nullable=False),
        sa.Column("length_m", sa.Float(), nullable=False),
        sa.Column("upstream_area_ha", sa.Float()),
        # The threshold is the one free parameter of stream extraction and it
        # decides everything downstream, so it is stored with the result.
        sa.Column("threshold_ha", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.ForeignKeyConstraint(["catchment_id"], ["catchments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["analysis_id"], ["analyses.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_streams_catchment_id", "streams", ["catchment_id"])
    op.create_index("ix_streams_analysis_id", "streams", ["analysis_id"])
    op.create_index("ix_streams_strahler_order", "streams", ["strahler_order"])


def downgrade() -> None:
    op.drop_index("ix_streams_strahler_order", table_name="streams")
    op.drop_index("ix_streams_analysis_id", table_name="streams")
    op.drop_index("ix_streams_catchment_id", table_name="streams")
    op.drop_table("streams")
    op.drop_index("ix_catchments_village_id", table_name="catchments")
    op.drop_index("ix_catchments_analysis_id", table_name="catchments")
    op.drop_table("catchments")
