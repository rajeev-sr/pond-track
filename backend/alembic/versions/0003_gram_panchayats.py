"""Gram Panchayats, and the many-to-many link from villages

Revision ID: 0003
Revises: 0002
Create Date: M2-2c

HLD E2 names the LGD code as the canonical village identifier. The open data does
not supply one. What the CC0 SHRID→LGD dataset actually carries is the **Gram
Panchayat** LGD code -- the elected local body, which is a cluster of villages,
not the village. Writing it into `villages.lgd_code` would make the column that
the whole design calls "the canonical village key" identify something coarser
than a village, silently, in a field nobody would re-check. So `lgd_code` stays
null until a village-level LGD source appears, and the Panchayat gets modelled as
what it is.

The relationship is genuinely many-to-many. Measured over all 638,847 rows:
**12,045 villages belong to two or more Gram Panchayats** -- Bambooflat is in
both Bambooflat-I and Bambooflat-II, Kottampalem in both Bheemudupakalu and
Doramamidi. A single `gram_panchayat_id` column on `villages` would be wrong for
2 % of the country and right-looking everywhere.

Worth having despite not being the village key:

* the **Gram Panchayat is the body that plans and builds water-harvesting works**
  under MGNREGA, so it is the operationally relevant unit for a pond proposal;
* it carries the only LGD code available anywhere in the open data;
* its names disambiguate villages the Census hierarchy cannot. Durg sub-district
  holds two villages called Khapri; their Panchayats are `Khapri` and `Khapri K`.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "gram_panchayats",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        # The LGD code is the Panchayat's own canonical key and is unique
        # nationally, which is what makes re-seeding idempotent.
        sa.Column("lgd_code", sa.String(20), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("name_normalised", sa.String(200), nullable=False),
        sa.Column("state", sa.String(200)),
        sa.Column("district", sa.String(200)),
        sa.Column("subdistrict", sa.String(200)),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_gram_panchayats_name_normalised", "gram_panchayats", ["name_normalised"])
    op.create_index("ix_gram_panchayats_state_district", "gram_panchayats", ["state", "district"])
    # Panchayat names vary in transliteration exactly as village names do, so the
    # same trigram search applies to them.
    op.execute(
        "CREATE INDEX ix_gram_panchayats_name_trgm ON gram_panchayats "
        "USING gin (name_normalised gin_trgm_ops)"
    )

    op.create_table(
        "village_gram_panchayats",
        sa.Column("village_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("gram_panchayat_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["village_id"], ["villages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["gram_panchayat_id"], ["gram_panchayats.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("village_id", "gram_panchayat_id"),
    )
    # The composite primary key leads with `village_id`, so it cannot serve
    # "which villages are in this Panchayat" -- that needs its own index. Named
    # as SQLAlchemy names an `index=True` column, so the models stay in step and
    # `alembic check` stays quiet.
    op.create_index(
        "ix_village_gram_panchayats_gram_panchayat_id",
        "village_gram_panchayats",
        ["gram_panchayat_id"],
    )

    # The Census 2001 code, for joining against pre-2011 records. It travels in
    # the same file, so collecting it here costs nothing and saves re-reading
    # 56 MB later (HLD E3 mentions the 2001↔2011 mapping).
    op.add_column("villages", sa.Column("census_2001_id", sa.String(20), nullable=True))
    op.create_index("ix_villages_census_2001_id", "villages", ["census_2001_id"])


def downgrade() -> None:
    op.drop_index("ix_villages_census_2001_id", table_name="villages")
    op.drop_column("villages", "census_2001_id")
    op.drop_index(
        "ix_village_gram_panchayats_gram_panchayat_id",
        table_name="village_gram_panchayats",
    )
    op.drop_table("village_gram_panchayats")
    op.drop_index("ix_gram_panchayats_name_trgm", table_name="gram_panchayats")
    op.drop_index("ix_gram_panchayats_state_district", table_name="gram_panchayats")
    op.drop_index("ix_gram_panchayats_name_normalised", table_name="gram_panchayats")
    op.drop_table("gram_panchayats")
