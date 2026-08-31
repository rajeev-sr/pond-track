"""Reconcile the initial schema with the models it was meant to create

Revision ID: 0004
Revises: 0003
Create Date: M2-2c

`alembic check` had never been run. Doing so revealed that migration 0001 and
`app/db/models.py` had disagreed since the beginning, in two ways that autogenerate
reports as pending changes on every invocation -- which means the one tool meant
to catch real drift was drowning in permanent noise.

**1. `created_at` was created nullable.** The models declare
`Mapped[datetime]`, which is NOT NULL. The column always has a value because
`server_default=now()` fills it, so the constraint was simply missing rather than
being deliberately relaxed.

**2. `unique=True, index=True` was rendered as two objects.** SQLAlchemy expresses
that pair as a *single unique index*; 0001 created a UNIQUE constraint plus a
separate non-unique index. Functionally similar, structurally different, and the
difference is exactly what autogenerate keeps proposing to fix.

Neither had any effect on behaviour, which is why they survived this long. The
cost was to `alembic check`: with permanent drift reported, the next real
mismatch would have been invisible in the noise -- and the migration autogenerate
proposed also dropped every PostGIS TIGER table, because `include_object` did not
filter them either (fixed in `alembic/env.py`).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels = None
depends_on = None

#: (table, column) pairs whose `created_at` the models declare NOT NULL.
_TIMESTAMPS = (
    ("villages", "created_at"),
    ("dem_assets", "created_at"),
    ("analyses", "created_at"),
)

#: (table, column, index) triples declared `unique=True, index=True` in the
#: models, which SQLAlchemy renders as one unique index.
_UNIQUE_INDEXES = (
    ("villages", "lgd_code", "ix_villages_lgd_code", "villages_lgd_code_key"),
    ("dem_assets", "cache_key", "ix_dem_assets_cache_key", "dem_assets_cache_key_key"),
    ("analyses", "job_id", "ix_analyses_job_id", "analyses_job_id_key"),
)


def upgrade() -> None:
    for table, column in _TIMESTAMPS:
        op.alter_column(
            table,
            column,
            existing_type=sa.DateTime(timezone=True),
            existing_server_default=sa.text("now()"),
            nullable=False,
        )

    # `progress_pct` has a server default too, and the model declares it required.
    op.alter_column(
        "analyses",
        "progress_pct",
        existing_type=sa.Integer(),
        existing_server_default=sa.text("0"),
        nullable=False,
    )
    op.alter_column(
        "villages",
        "source",
        existing_type=sa.String(50),
        existing_server_default=sa.text("'shrug'::character varying"),
        nullable=False,
    )

    for table, column, index_name, constraint_name in _UNIQUE_INDEXES:
        # Drop the constraint first: it owns an implicit index that would
        # conflict with the unique index replacing it.
        op.drop_constraint(constraint_name, table, type_="unique")
        op.drop_index(index_name, table_name=table)
        op.create_index(index_name, table, [column], unique=True)


def downgrade() -> None:
    for table, column, index_name, constraint_name in _UNIQUE_INDEXES:
        op.drop_index(index_name, table_name=table)
        op.create_unique_constraint(constraint_name, table, [column])
        op.create_index(index_name, table, [column])

    op.alter_column(
        "villages",
        "source",
        existing_type=sa.String(50),
        existing_server_default=sa.text("'shrug'::character varying"),
        nullable=True,
    )
    op.alter_column(
        "analyses",
        "progress_pct",
        existing_type=sa.Integer(),
        existing_server_default=sa.text("0"),
        nullable=True,
    )
    for table, column in _TIMESTAMPS:
        op.alter_column(
            table,
            column,
            existing_type=sa.DateTime(timezone=True),
            existing_server_default=sa.text("now()"),
            nullable=True,
        )
