"""ORM models. Mirrors the data model in HLD 8.

Only the three tables M0-7 needs are here. Later phases add their own tables in
their own migrations (M3-11 catchments/streams, M4-15 rainfall_cache,
M5-14 candidate_sites/pond_designs/runoffs).

Convention: geometry is stored in EPSG:4326 (HLD ADR-5); every metric value is
stored in a column whose name states its unit.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from geoalchemy2 import Geometry
from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class AdminArea(Base):
    """A state, district or sub-district polygon.

    Held separately from `Village` because the two arrive from different sources
    at different granularities, and because a sub-district outline should exist
    once rather than being copied onto each of the hundreds of villages inside
    it. Self-referencing so the hierarchy is walkable in either direction.
    """

    __tablename__ = "admin_areas"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    #: state | district | subdistrict
    level: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    name_normalised: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    #: The source's own id, so re-seeding updates rather than duplicates.
    source_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_areas.id", ondelete="SET NULL"), index=True
    )
    geom: Mapped[Any] = mapped_column(
        Geometry("MULTIPOLYGON", srid=4326, spatial_index=True), nullable=False
    )
    centroid: Mapped[Any | None] = mapped_column(Geometry("POINT", srid=4326))
    area_ha: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("source", "source_id", name="uq_admin_areas_source_id"),)


class Village(Base):
    """A Census-2011 village or town.

    The name index and the geometry come from different sources -- see migration
    0002 -- so a row must be able to say which boundary it is actually offering.
    `geom` is therefore nullable and `boundary_level` qualifies it:

    * ``village``     -- `geom` is this village's own boundary;
    * ``subdistrict`` -- only the containing sub-district outline is known;
    * ``None``        -- no geometry, name and hierarchy only.

    A row with no polygon is still worth having: fuzzy name search is what the
    index exists for (HLD CH-24), and the sub-district is enough to frame a map.
    Serving a sub-district outline as if it were a village boundary is the one
    thing that must not happen, which is what this column prevents.
    """

    __tablename__ = "villages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    #: HLD CH-24: the canonical key is the code, never the name.
    #:
    #: Still null everywhere. The open data has no *village* LGD code -- the only
    #: LGD code available identifies the Gram Panchayat, which is a cluster of
    #: villages (see `GramPanchayat`). `census_2011_id` and `shrid` serve as the
    #: canonical keys until a village-level source appears.
    lgd_code: Mapped[str | None] = mapped_column(String(20), unique=True, index=True)
    census_2011_id: Mapped[str | None] = mapped_column(String(20), index=True)
    #: The pre-2011 code, for joining against older records (HLD E3).
    census_2001_id: Mapped[str | None] = mapped_column(String(20), index=True)
    #: SHRUG composite id: version-state-district-subdistrict-village.
    shrid: Mapped[str | None] = mapped_column(String(40), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    name_normalised: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    #: CD Block -- a rural-development unit, not the Census sub-district below.
    block: Mapped[str | None] = mapped_column(String(200))
    #: Census sub-district (tehsil), the level between district and village.
    subdistrict: Mapped[str | None] = mapped_column(String(200))
    district: Mapped[str | None] = mapped_column(String(200), index=True)
    state: Mapped[str | None] = mapped_column(String(200), index=True)
    geom: Mapped[Any | None] = mapped_column(
        Geometry("MULTIPOLYGON", srid=4326, spatial_index=True), nullable=True
    )
    #: What `geom` represents. See the class docstring.
    boundary_level: Mapped[str | None] = mapped_column(String(16))
    admin_area_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_areas.id", ondelete="SET NULL"), index=True
    )
    centroid: Mapped[Any | None] = mapped_column(Geometry("POINT", srid=4326))
    area_ha: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(50), default="shrug")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_villages_state_district", "state", "district"),
        Index("ix_villages_state_district_subdistrict", "state", "district", "subdistrict"),
    )


class GramPanchayat(Base):
    """An elected local body: the unit that plans and builds MGNREGA water works.

    Not a village, and deliberately not conflated with one. HLD E2 wants a
    village LGD code; the open data supplies a *Panchayat* LGD code, which
    covers a cluster of villages. Keeping them separate is why
    `Village.lgd_code` is still null rather than quietly holding something
    coarser than it claims.
    """

    __tablename__ = "gram_panchayats"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    #: The Panchayat's canonical key, unique nationally.
    lgd_code: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    name_normalised: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    state: Mapped[str | None] = mapped_column(String(200))
    district: Mapped[str | None] = mapped_column(String(200))
    subdistrict: Mapped[str | None] = mapped_column(String(200))
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_gram_panchayats_state_district", "state", "district"),)


class VillageGramPanchayat(Base):
    """Which Panchayats a village belongs to.

    A link table rather than a column, because the relationship is genuinely
    many-to-many: **12,045 of India's villages sit in two or more Panchayats**.
    Bambooflat is in Bambooflat-I and Bambooflat-II; Kottampalem is in
    Bheemudupakalu and Doramamidi. A single foreign key would be wrong for 2 % of
    the country and look right everywhere else.
    """

    __tablename__ = "village_gram_panchayats"

    village_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("villages.id", ondelete="CASCADE"), primary_key=True
    )
    gram_panchayat_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("gram_panchayats.id", ondelete="CASCADE"),
        primary_key=True,
        # The composite primary key leads with `village_id`, so it cannot serve
        # "which villages are in this Panchayat" -- that needs its own index.
        index=True,
    )


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


class Catchment(Base):
    """A delineated contributing area, with the provenance to read it by.

    `analysis_id` and `village_id` are both nullable: a catchment delineated from
    an uploaded contour map belongs to neither, and forcing one would mean
    inventing a row.

    Two columns exist purely so a stored result stays interpretable. A catchment
    delineated over a heavily filled surface deserves less confidence than one
    over terrain that drained on its own, and six months later nobody will
    remember which it was -- so `conditioning_method` and `cells_filled` are
    recorded. `touches_survey_edge` means part of the contributing area lay
    outside the map, making `area_ha` a lower bound rather than a measurement.
    """

    __tablename__ = "catchments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    analysis_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("analyses.id", ondelete="CASCADE"), index=True
    )
    village_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("villages.id", ondelete="SET NULL"), index=True
    )

    #: Where the water leaves, and where the caller actually asked. The two differ
    #: by the snap, and storing only the outlet loses the question that was asked.
    outlet: Mapped[Any] = mapped_column(
        Geometry("POINT", srid=4326, spatial_index=True), nullable=False
    )
    requested_point: Mapped[Any | None] = mapped_column(
        Geometry("POINT", srid=4326, spatial_index=True)
    )
    snap_moved_m: Mapped[float | None] = mapped_column(Float)

    geom: Mapped[Any] = mapped_column(
        Geometry("MULTIPOLYGON", srid=4326, spatial_index=True), nullable=False
    )
    area_ha: Mapped[float] = mapped_column(Float, nullable=False)
    perimeter_m: Mapped[float | None] = mapped_column(Float)
    relief_m: Mapped[float | None] = mapped_column(Float)
    mean_slope_pct: Mapped[float | None] = mapped_column(Float)
    longest_flow_path_m: Mapped[float | None] = mapped_column(Float)
    time_of_concentration_min: Mapped[float | None] = mapped_column(Float)
    form_factor: Mapped[float | None] = mapped_column(Float)
    compactness_coefficient: Mapped[float | None] = mapped_column(Float)

    dem_source: Mapped[str | None] = mapped_column(String(40))
    resolution_m: Mapped[float | None] = mapped_column(Float)
    #: fill | breach_then_fill
    conditioning_method: Mapped[str | None] = mapped_column(String(24))
    cells_filled: Mapped[int | None] = mapped_column(Integer)
    touches_survey_edge: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=false()
    )
    confidence: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Stream(Base):
    """One Strahler stream of a drainage network.

    A row runs from where the channel attains its order to where it loses it --
    not from junction to junction. Storing segments instead would make the row
    counts violate Horton's law of stream numbers, which is how the distinction
    was found in the first place.

    `threshold_ha` is stored with the result because it is the one free parameter
    of stream extraction and it decides everything downstream: the same terrain at
    0.5 ha and at 25 ha gives 720 reaches and 18.
    """

    __tablename__ = "streams"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    catchment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("catchments.id", ondelete="CASCADE"), index=True
    )
    analysis_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("analyses.id", ondelete="CASCADE"), index=True
    )
    geom: Mapped[Any] = mapped_column(
        Geometry("LINESTRING", srid=4326, spatial_index=True), nullable=False
    )
    strahler_order: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    length_m: Mapped[float] = mapped_column(Float, nullable=False)
    upstream_area_ha: Mapped[float | None] = mapped_column(Float)
    threshold_ha: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class RainfallCache(Base):
    """One cached day of rainfall for one source's grid cell.

    Keyed on the *source's grid cell*, never the coordinate that was asked for.
    ERA5-Land is a 0.1-degree reanalysis, so every point inside one cell receives
    the same series -- keying on exact lon/lat would store a fresh copy per query
    and never hit, so two clicks 200 m apart would each persist 11,000 rows of
    identical data.

    Row per day rather than one blob per series, so a cache can be *extended*
    when a later year becomes available instead of being invalidated whole.
    """

    __tablename__ = "rainfall_cache"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    #: The source's grid cell, e.g. "21.3000,81.3000" for a 0.1-degree product.
    cell_key: Mapped[str] = mapped_column(String(32), nullable=False)
    #: The cell's own centre, so a cached series can be placed on a map honestly
    #: and its distance from the query point reported.
    cell_lat: Mapped[float] = mapped_column(Float, nullable=False)
    cell_lon: Mapped[float] = mapped_column(Float, nullable=False)
    observed_on: Mapped[date] = mapped_column(Date, nullable=False)
    precipitation_mm: Mapped[float] = mapped_column(Float, nullable=False)
    #: Nullable because which extras arrive depends on the source: POWER carries
    #: temperature and no ET0, Open-Meteo the reverse.
    temperature_c: Mapped[float | None] = mapped_column(Float)
    et0_mm: Mapped[float | None] = mapped_column(Float)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        # The constraint that makes this a cache rather than an append log:
        # without it a re-fetch adds a second copy of every day and reads start
        # double-counting rainfall, inflating every runoff figure derived from it.
        UniqueConstraint("source", "cell_key", "observed_on", name="uq_rainfall_cache_cell_day"),
        Index("ix_rainfall_cache_lookup", "source", "cell_key", "observed_on"),
        # For finding stale entries when a source revises its reanalysis, which
        # both of these do -- ERA5-Land and MERRA-2 are reprocessed periodically.
        Index("ix_rainfall_cache_fetched_at", "fetched_at"),
    )
