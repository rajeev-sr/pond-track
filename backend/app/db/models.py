"""ORM models. Mirrors the data model in HLD 8.

Only the three tables M0-7 needs are here. Later phases add their own tables in
their own migrations (M3-11 catchments/streams, M4-15 rainfall_cache,
M5-14 candidate_sites/pond_designs/runoffs).

Convention: geometry is stored in EPSG:4326 (HLD ADR-5); every metric value is
stored in a column whose name states its unit.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from geoalchemy2 import Geometry
from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class Village(Base):
    """Census-2011 village polygon, seeded from SHRUG (HLD 4.2 E1)."""

    __tablename__ = "villages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    lgd_code: Mapped[str | None] = mapped_column(String(20), unique=True, index=True)
    census_2011_id: Mapped[str | None] = mapped_column(String(20), index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    name_normalised: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    block: Mapped[str | None] = mapped_column(String(200))
    district: Mapped[str | None] = mapped_column(String(200), index=True)
    state: Mapped[str | None] = mapped_column(String(200), index=True)
    geom: Mapped[Any] = mapped_column(Geometry("MULTIPOLYGON", srid=4326, spatial_index=True))
    centroid: Mapped[Any | None] = mapped_column(Geometry("POINT", srid=4326))
    area_ha: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(50), default="shrug")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_villages_state_district", "state", "district"),)


class DemAsset(Base):
    """A cached DEM (or derivative) COG on disk, keyed by bbox + source + params.

    Content-addressed: the same request never downloads twice, and a stored
    analysis can be replayed byte-identically (HLD NFR-13, 2.5 L2 cache).
    """

    __tablename__ = "dem_assets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    cache_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    product: Mapped[str] = mapped_column(String(40), nullable=False)  # dem | slope | flowacc ...
    source: Mapped[str] = mapped_column(String(40), nullable=False)  # COP30 | CartoDEM ...
    bbox: Mapped[Any] = mapped_column(Geometry("POLYGON", srid=4326, spatial_index=True))
    epsg: Mapped[int] = mapped_column(Integer, nullable=False)  # projected working CRS
    resolution_m: Mapped[float] = mapped_column(Float, nullable=False)
    width_px: Mapped[int | None] = mapped_column(Integer)
    height_px: Mapped[int | None] = mapped_column(Integer)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64))
    stats: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # min/max/mean elevation
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Analysis(Base):
    """One pipeline run. State machine per HLD 3.7."""

    __tablename__ = "analyses"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    village_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("villages.id", ondelete="SET NULL")
    )
    job_id: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    # queued | running | retrying | partial | done | failed | cancelled
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="queued", index=True)
    progress_pct: Mapped[int] = mapped_column(Integer, default=0)
    current_step: Mapped[str | None] = mapped_column(String(60))
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # Provenance: which source/version produced each layer, for reproducibility.
    sources: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    warnings: Mapped[list[str] | None] = mapped_column(JSONB)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    village: Mapped[Village | None] = relationship(lazy="joined")

    __table_args__ = (Index("ix_analyses_state_created", "state", "created_at"),)
