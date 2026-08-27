"""Persistent cache for daily rainfall series (M4-5).

Thirty years of daily rainfall for a point does not change, and Open-Meteo
enforces a daily request limit that gets hit -- during this project's development
to the point where the analysis could not reach a rainfall tier at all. Storing
what has already been fetched is the fix.

**The cache key is the source's own grid cell, never the coordinate asked for.**
That is the design, and getting it wrong makes the cache useless rather than
merely imperfect: ERA5-Land is a 0.1-degree reanalysis, so every point inside one
cell receives the *same* series. Keying on exact lon/lat would store a fresh copy
per query and never hit -- two clicks 200 m apart would each pull and persist
11,000 rows of identical data. Quantising to the source's resolution turns a 0 %
hit rate into a 100 % one for any later query in the same cell.

**Best-effort by design.** The contour endpoints work with no database at all --
a documented decision, not an omission -- so every operation here degrades to a
no-op if PostGIS is unreachable. A cache that could fail an analysis would be a
worse feature than no cache.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

import numpy as np
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

log = logging.getLogger(__name__)

#: Each source's grid resolution in degrees, as (latitude, longitude).
#:
#: Asymmetric for POWER because MERRA-2 is: 0.5 degrees north-south and 0.625
#: east-west. Quantising both to the same figure would either split cells that
#: share a series or merge cells that do not.
GRID_RESOLUTION: dict[str, tuple[float, float]] = {
    "open_meteo_era5_land": (0.1, 0.1),
    "nasa_power": (0.5, 0.625),
}

#: Fall back to the coarsest grid for an unregistered source. Over-coarse is the
#: safe direction to be wrong: it merges cells that may not share a series, so a
#: reader gets a slightly displaced series rather than a cache that never hits --
#: and `cell_offset_km` reports how far it was displaced.
DEFAULT_RESOLUTION = (0.5, 0.625)

#: A cached range must cover at least this share of the requested days to be used.
#:
#: Not 100 %: a source can legitimately lack days -- POWER's fill values, a
#: reanalysis gap -- and demanding every date would mean never hitting the cache
#: for a series that will never be complete. Not much lower either: the annual
#: totals and the coefficient of variation are computed from these days, so a
#: sparse cache would quietly change the design figures.
MIN_COVERAGE = 0.95

_INSERT = text(
    """
    INSERT INTO rainfall_cache
        (id, source, cell_key, cell_lat, cell_lon, observed_on,
         precipitation_mm, temperature_c, et0_mm)
    VALUES (gen_random_uuid(), :source, :cell_key, :cell_lat, :cell_lon,
            :observed_on, :precipitation_mm, :temperature_c, :et0_mm)
    ON CONFLICT (source, cell_key, observed_on) DO UPDATE SET
        precipitation_mm = EXCLUDED.precipitation_mm,
        -- COALESCE so a source that carries only one of the two extras cannot
        -- erase the other's contribution for the same cell and day.
        temperature_c    = COALESCE(EXCLUDED.temperature_c, rainfall_cache.temperature_c),
        et0_mm           = COALESCE(EXCLUDED.et0_mm, rainfall_cache.et0_mm),
        fetched_at       = now()
    """
)

_SELECT = text(
    """
    SELECT observed_on, precipitation_mm, temperature_c, et0_mm
      FROM rainfall_cache
     WHERE source = :source
       AND cell_key = :cell_key
       AND observed_on BETWEEN :start AND :end
     ORDER BY observed_on
    """
)


@dataclass(frozen=True)
class Cell:
    """A source's grid cell, and how far its centre sits from the query point."""

    key: str
    lat: float
    lon: float
    offset_km: float


def cell_for(source: str, lon: float, lat: float) -> Cell:
    """Quantise a coordinate to the source's grid cell.

    The returned centre is the cell's, not the query's, so a cached series can be
    placed on a map honestly -- and `offset_km` says how far the series actually
    describes from where it was asked about. At POWER's resolution that can be
    30 km, which a reader should be able to see.
    """
    lat_step, lon_step = GRID_RESOLUTION.get(source, DEFAULT_RESOLUTION)
    cell_lat = round(round(lat / lat_step) * lat_step, 6)
    cell_lon = round(round(lon / lon_step) * lon_step, 6)

    # Equirectangular is ample here: the offset is at most a cell, so the
    # convergence of meridians over that distance is irrelevant.
    import math

    mean_lat = math.radians((lat + cell_lat) / 2.0)
    dy_km = (cell_lat - lat) * 110.574
    dx_km = (cell_lon - lon) * 111.320 * math.cos(mean_lat)
    return Cell(
        key=f"{cell_lat:.4f},{cell_lon:.4f}",
        lat=cell_lat,
        lon=cell_lon,
        offset_km=round(math.hypot(dx_km, dy_km), 2),
    )


@dataclass(frozen=True)
class CachedSeries:
    dates: list[dt.date]
    precipitation_mm: np.ndarray
    temperature_c: np.ndarray | None
    et0_mm: np.ndarray | None
    cell: Cell
    coverage: float


def read(source: str, lon: float, lat: float, start: dt.date, end: dt.date) -> CachedSeries | None:
    """The cached series for this cell and range, or None.

    Returns None rather than a partial series when coverage is below
    `MIN_COVERAGE`: the annual totals and the coefficient of variation are derived
    from these days, so handing back a sparse series would quietly change the
    dependable rainfall a pond is sized on. A short read is a miss.
    """
    cell = cell_for(source, lon, lat)
    rows = _query(source, cell, start, end)
    if rows is None:
        return None

    expected = (end - start).days + 1
    if not rows or len(rows) / expected < MIN_COVERAGE:
        if rows:
            log.info(
                "rainfall_cache_partial",
                extra={
                    "source": source,
                    "cell": cell.key,
                    "have": len(rows),
                    "expected": expected,
                },
            )
        return None

    dates = [row[0] for row in rows]
    precip = np.array([float(row[1]) for row in rows])
    temps = [row[2] for row in rows]
    et0s = [row[3] for row in rows]
    return CachedSeries(
        dates=dates,
        precipitation_mm=precip,
        # None when the column is empty throughout, so a source without
        # temperature does not appear to have an all-NaN one -- which reads as a
        # broken series rather than an absent field.
        temperature_c=(
            None
            if all(value is None for value in temps)
            else np.array([np.nan if v is None else float(v) for v in temps])
        ),
        et0_mm=(
            None
            if all(value is None for value in et0s)
            else np.array([0.0 if v is None else float(v) for v in et0s])
        ),
        cell=cell,
        coverage=len(rows) / expected,
    )


def write(
    source: str,
    lon: float,
    lat: float,
    *,
    dates: list[dt.date],
    precipitation_mm: np.ndarray,
    temperature_c: np.ndarray | None = None,
    et0_mm: np.ndarray | None = None,
) -> int:
    """Store a fetched series. Returns the rows written, or 0 if unavailable."""
    if not dates:
        return 0
    cell = cell_for(source, lon, lat)
    payload = [
        {
            "source": source,
            "cell_key": cell.key,
            "cell_lat": cell.lat,
            "cell_lon": cell.lon,
            "observed_on": day,
            "precipitation_mm": float(precipitation_mm[index]),
            "temperature_c": _finite_or_none(temperature_c, index),
            "et0_mm": _finite_or_none(et0_mm, index),
        }
        for index, day in enumerate(dates)
    ]

    try:
        from app.db.session import get_sessionmaker

        with get_sessionmaker()() as session:
            # Batched: a 30-year series is ~11,000 rows and a single statement of
            # that size is slower than several and holds one lock far longer.
            for offset in range(0, len(payload), 2000):
                session.execute(_INSERT, payload[offset : offset + 2000])
            session.commit()
    except (SQLAlchemyError, OSError) as exc:
        # Best-effort by design: the contour endpoints work with no database, so
        # a cache write must never be able to fail an analysis.
        log.info(
            "rainfall_cache_write_skipped",
            extra={"source": source, "cell": cell.key, "error": type(exc).__name__},
        )
        return 0
    log.info(
        "rainfall_cache_written",
        extra={"source": source, "cell": cell.key, "rows": len(payload)},
    )
    return len(payload)


def _finite_or_none(values: np.ndarray | None, index: int) -> float | None:
    if values is None or index >= values.size:
        return None
    value = float(values[index])
    return None if not np.isfinite(value) else value


def _query(
    source: str, cell: Cell, start: dt.date, end: dt.date
) -> list[tuple[dt.date, float, float | None, float | None]] | None:
    """Run the lookup, or None if the database is not reachable."""
    try:
        from app.db.session import get_sessionmaker

        with get_sessionmaker()() as session:
            result = session.execute(
                _SELECT,
                {"source": source, "cell_key": cell.key, "start": start, "end": end},
            )
            return [tuple(row) for row in result]  # type: ignore[misc]
    except (SQLAlchemyError, OSError) as exc:
        log.info(
            "rainfall_cache_read_skipped",
            extra={"source": source, "error": type(exc).__name__},
        )
        return None


def stats_from_cache(
    source: str,
    lon: float,
    lat: float,
    start: dt.date,
    end: dt.date,
    *,
    provenance: object,
    data_caveat: str,
    model_used: str,
) -> object | None:
    """A full `RainfallStats` from the cache, or None on a miss.

    Imported lazily so `providers.rainfall.base` can stay free of any knowledge
    of the cache -- the statistics are pure derivation and should not know where
    the series came from.

    The returned statistics carry a warning naming the cache and the cell offset,
    because a series describing a point up to 30 km away at POWER's resolution is
    something a reader should be told, not something to discover from the numbers.
    """
    series = read(source, lon, lat, start, end)
    if series is None:
        return None

    from app.providers.base import ProviderUnavailableError
    from app.providers.rainfall.base import build_stats

    warnings = [
        f"served from the local cache for grid cell {series.cell.key} "
        f"({series.cell.offset_km:g} km from the requested point), "
        f"{series.coverage:.0%} of days present"
    ]
    try:
        return build_stats(
            source,
            daily_mm=series.precipitation_mm,
            dates=series.dates,
            lon=lon,
            lat=lat,
            model_used=f"{model_used} (cached)",
            provenance=provenance,  # type: ignore[arg-type]
            data_caveat=data_caveat,
            et0_daily_mm=series.et0_mm,
            temp_daily_c=series.temperature_c,
            warnings=warnings,
        )
    except ProviderUnavailableError as exc:
        # A cached series that cannot produce statistics -- too few complete
        # years, say -- is a miss, not a failure. Fetching is still an option.
        log.info(
            "rainfall_cache_unusable",
            extra={"source": source, "cell": series.cell.key, "reason": exc.detail},
        )
        return None
