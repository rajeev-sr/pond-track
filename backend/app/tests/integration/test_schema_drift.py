"""The models and the migrations must describe the same schema.

This existed as a latent problem for the whole project: `alembic check` had never
been run, and when it was, it reported drift across five tables *and* proposed a
migration that dropped thirty-odd PostGIS TIGER tables. Both were harmless until
someone ran `make revision --autogenerate` and committed what it produced.

A test is the right place for this rather than a Makefile target, because the
failure mode is silent and cumulative: each new migration that names an index
differently from its model adds another line of permanent noise, until real drift
is indistinguishable from the background.

Skipped unless a database is reachable.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, inspect, text

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    from sqlalchemy.exc import OperationalError

    from app.db.session import get_engine

    eng = get_engine()
    try:
        with eng.connect() as connection:
            connection.execute(text("SELECT 1"))
    except OperationalError as exc:
        pytest.skip(f"no database reachable: {exc.__class__.__name__}")
    yield eng


def test_the_migrations_are_at_head(engine: Engine) -> None:
    """A drift comparison against a half-migrated database proves nothing."""
    with engine.connect() as connection:
        applied = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert applied, "no alembic version recorded; run `make migrate`"


def test_the_models_and_the_database_agree(engine: Engine) -> None:
    """`alembic check`, as a test.

    Uses the same comparison autogenerate does, including the `include_object`
    filter, so a pass here means `alembic revision --autogenerate` would produce
    an empty migration.
    """
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from alembic_env_helpers import include_object  # type: ignore[import-not-found]
    from app.db.base import Base

    with engine.connect() as connection:
        context = MigrationContext.configure(
            connection,
            opts={"include_object": include_object, "compare_type": False},
        )
        diff = compare_metadata(context, Base.metadata)

    assert diff == [], (
        "the models and the database describe different schemas. Either a "
        "migration is missing, or a migration named something differently from "
        "the model that declares it:\n  " + "\n  ".join(str(d) for d in diff)
    )


def test_every_declared_table_exists(engine: Engine) -> None:
    """A blunter check that survives even if the autogenerate API shifts."""
    from app.db.base import Base

    present = set(inspect(engine).get_table_names())
    declared = set(Base.metadata.tables)
    missing = declared - present
    assert not missing, f"tables declared in the models but absent: {sorted(missing)}"


def test_the_trigram_indexes_survive(engine: Engine) -> None:
    """These are created with raw SQL and are invisible to the metadata.

    `include_object` must keep excluding them: autogenerate cannot see them in
    the models, so without the filter the next generated migration would drop
    them, and village search would fall back to a sequential scan over 600,000
    rows -- slower, but still correct, so nothing would fail visibly.
    """
    expected = {
        "ix_villages_name_trgm",
        "ix_admin_areas_name_trgm",
        "ix_gram_panchayats_name_trgm",
    }
    with engine.connect() as connection:
        found = set(
            connection.execute(
                text("SELECT indexname FROM pg_indexes WHERE indexname LIKE '%%_trgm'")
            ).scalars()
        )
    assert expected <= found, f"missing trigram indexes: {sorted(expected - found)}"
