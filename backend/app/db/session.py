"""SQLAlchemy engine and session management."""

from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings


@lru_cache
def get_engine() -> Engine:
    from app.core import telemetry

    s = get_settings()
    engine = create_engine(
        s.database_url,
        pool_pre_ping=True,  # survives PostGIS restarts without a stale-conn error
        # Fail in seconds, not the minutes a TCP connect to a host that drops
        # packets can take: the database is optional here, and a request that
        # needs it should say so quickly rather than hang.
        connect_args={"connect_timeout": 3},
        pool_size=5,
        max_overflow=10,
        echo=False,
    )
    # A span per statement. Here rather than at startup because this engine is
    # built lazily, on the first query, long after telemetry was set up.
    telemetry.instrument_engine(engine)
    return engine


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a scoped session."""
    with get_sessionmaker()() as session:
        yield session
