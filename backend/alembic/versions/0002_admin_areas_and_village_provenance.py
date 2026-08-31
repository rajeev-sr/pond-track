"""Admin-area polygons, and villages that admit which boundary they carry

Revision ID: 0002
Revises: 0001
Create Date: M0-11

The initial schema assumed one source: SHRUG village polygons, so `villages.geom`
was NOT NULL and every row was a Census-2011 village boundary. Seeding revealed
that assumption does not survive contact with the available data.

* SHRUG's *names* are open and downloadable without credentials -- the SHRID to
  LGD crosswalk on Harvard Dataverse is CC0 and carries all 596,390 villages and
  towns with their full state/district/sub-district hierarchy.
* SHRUG's *polygons* are not: they go through a form on devdatalab.org rather
  than a fetchable URL, their Dataverse mirror holds only the socioeconomic
  tables, and the GitHub releases carry no assets.
* The obvious substitutes do not reach village level. DataMeet covers nine
  states and Chhattisgarh is not among them; geoBoundaries stops at CD Block,
  7,152 units against India's ~600,000 villages.

So the name index and the geometry now come from different places and at
different granularities, and the schema has to be able to say so. Two changes:

1. `admin_areas` holds state, district and sub-district polygons once each,
   rather than repeating a sub-district outline across the hundreds of villages
   inside it.
2. `villages.geom` becomes nullable and gains `boundary_level`, so a row can
   state whether the polygon it offers is the village's own or the containing
   sub-district's. A village with no polygon is still fully searchable, which is
   what the name index is for; what must never happen is a sub-district outline
   being served as though it were a village boundary.
"""

from __future__ import annotations

import geoalchemy2
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "admin_areas",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        # state | district | subdistrict. Not an enum: the levels a source
        # offers vary by country and by source, and a check constraint here
        # would have to be migrated every time one is added.
        sa.Column("level", sa.String(16), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("name_normalised", sa.String(200), nullable=False),
        # The source's own identifier, so a re-seed can recognise a row it
        # already wrote instead of duplicating it.
        sa.Column("source_id", sa.String(64), nullable=False),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "geom",
            geoalchemy2.types.Geometry(geometry_type="MULTIPOLYGON", srid=4326),
            nullable=False,
        ),
        sa.Column(
            "centroid", geoalchemy2.types.Geometry(geometry_type="POINT", srid=4326)
        ),
        sa.Column("area_ha", sa.Float()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["parent_id"], ["admin_areas.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("source", "source_id", name="uq_admin_areas_source_id"),
    )
    op.create_index("ix_admin_areas_level", "admin_areas", ["level"])
    op.create_index("ix_admin_areas_name_normalised", "admin_areas", ["name_normalised"])
    # Named as SQLAlchemy would name it. A migration that invents its own name
    # for a column declared `index=True` puts the schema permanently out of step
    # with the models, and `alembic check` reports drift on every run.
    op.create_index("ix_admin_areas_parent_id", "admin_areas", ["parent_id"])
    op.execute(
        "CREATE INDEX ix_admin_areas_name_trgm ON admin_areas "
        "USING gin (name_normalised gin_trgm_ops)"
    )

    # A village row is worth having with a name alone -- search is the point of
    # it -- so the polygon becomes optional.
    op.alter_column("villages", "geom", existing_type=geoalchemy2.types.Geometry(
        geometry_type="MULTIPOLYGON", srid=4326), nullable=True)

    # SHRUG's composite id: shrug_version-pc11_state-district-subdistrict-village.
    # Longer than the 20 chars `census_2011_id` allows, and it is the key the
    # open name index is distributed under, so it earns its own column.
    op.add_column("villages", sa.Column("shrid", sa.String(40), nullable=True))
    op.create_index("ix_villages_shrid", "villages", ["shrid"], unique=True)

    # The Census hierarchy has a sub-district (tehsil) between district and
    # village. `block` already exists but means the CD Block, a different unit
    # from a different administrative system; conflating them would corrupt the
    # hierarchy the search results are disambiguated by.
    op.add_column("villages", sa.Column("subdistrict", sa.String(200), nullable=True))
    op.create_index(
        "ix_villages_state_district_subdistrict",
        "villages",
        ["state", "district", "subdistrict"],
    )

    # What `geom` actually is: 'village' for a true boundary, 'subdistrict' when
    # only the containing area is known, NULL when there is no geometry at all.
    op.add_column("villages", sa.Column("boundary_level", sa.String(16), nullable=True))
    op.add_column(
        "villages",
        sa.Column("admin_area_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_villages_admin_area",
        "villages",
        "admin_areas",
        ["admin_area_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_villages_admin_area_id", "villages", ["admin_area_id"])


def downgrade() -> None:
    op.drop_index("ix_villages_admin_area_id", table_name="villages")
    op.drop_constraint("fk_villages_admin_area", "villages", type_="foreignkey")
    op.drop_column("villages", "admin_area_id")
    op.drop_column("villages", "boundary_level")
    op.drop_index("ix_villages_state_district_subdistrict", table_name="villages")
    op.drop_column("villages", "subdistrict")
    op.drop_index("ix_villages_shrid", table_name="villages")
    op.drop_column("villages", "shrid")
    # Rows seeded without a polygon cannot satisfy NOT NULL, so they go first.
    op.execute("DELETE FROM villages WHERE geom IS NULL")
    op.alter_column("villages", "geom", existing_type=geoalchemy2.types.Geometry(
        geometry_type="MULTIPOLYGON", srid=4326), nullable=False)
    op.drop_index("ix_admin_areas_name_trgm", table_name="admin_areas")
    op.drop_index("ix_admin_areas_parent_id", table_name="admin_areas")
    op.drop_index("ix_admin_areas_name_normalised", table_name="admin_areas")
    op.drop_index("ix_admin_areas_level", table_name="admin_areas")
    op.drop_table("admin_areas")
