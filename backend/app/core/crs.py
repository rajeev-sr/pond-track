"""Coordinate-reference-system helpers and the metric-operation guard.

HLD ADR-5: geometry is stored in EPSG:4326 and every metric computation runs in
a projected (metric) CRS. ``shapely.area`` on EPSG:4326 silently returns square
degrees, which is the single most dangerous bug class in this project because it
produces plausible-looking numbers with no error (HLD CH-10).

``CRSGuard`` makes that failure loud instead of silent.
"""

from __future__ import annotations

import math
from typing import Any

UTM_NORTH_EPSG_BASE = 32600
UTM_SOUTH_EPSG_BASE = 32700

# India's mainland + islands envelope, used for a sanity check on inputs.
INDIA_LON_MIN, INDIA_LON_MAX = 68.0, 97.5
INDIA_LAT_MIN, INDIA_LAT_MAX = 6.0, 37.6

# EPSG:7755 - WGS 84 / India NSF LCC. Emitted alongside UTM for outputs shared
# with government GIS (HLD 6.2).
INDIA_NSF_LCC_EPSG = 7755

# UTM zones spanned by India, for reporting and tests (HLD 6.2).
INDIA_UTM_ZONES: dict[int, str] = {
    42: "66-72E  Gujarat, west Rajasthan",
    43: "72-78E  Maharashtra, MP, Delhi, Haryana",
    44: "78-84E  UP, Telangana, Chhattisgarh, Tamil Nadu",
    45: "84-90E  Bihar, Jharkhand, West Bengal, Odisha",
    46: "90-96E  Assam, Meghalaya",
    47: "96-102E Arunachal Pradesh",
}


class CRSError(ValueError):
    """Raised when a CRS is missing, unusable, or wrong for the operation."""


def utm_zone_for(lon: float) -> int:
    """UTM zone number (1-60) for a longitude in degrees."""
    if not math.isfinite(lon):
        raise CRSError(f"longitude must be finite, got {lon!r}")
    if not -180.0 <= lon <= 180.0:
        raise CRSError(f"longitude {lon} outside [-180, 180]")
    # The modulo makes lon == 180 wrap to zone 1 instead of producing zone 61.
    return int((lon + 180.0) // 6.0) % 60 + 1


def utm_epsg_for(lon: float, lat: float = 0.0) -> int:
    """EPSG code of the WGS 84 / UTM zone containing (lon, lat).

    >>> utm_epsg_for(77.4126, 23.2599)   # Bhopal
    32643
    """
    if not math.isfinite(lat):
        raise CRSError(f"latitude must be finite, got {lat!r}")
    if not -90.0 <= lat <= 90.0:
        raise CRSError(f"latitude {lat} outside [-90, 90]")
    base = UTM_NORTH_EPSG_BASE if lat >= 0 else UTM_SOUTH_EPSG_BASE
    return base + utm_zone_for(lon)


def is_within_india(lon: float, lat: float) -> bool:
    """Envelope check, not a boundary test. Used for warnings, never to reject."""
    return INDIA_LON_MIN <= lon <= INDIA_LON_MAX and INDIA_LAT_MIN <= lat <= INDIA_LAT_MAX


def _resolve_is_projected(crs: Any) -> bool:
    """Determine whether ``crs`` is projected, accepting several CRS flavours.

    Deliberately duck-typed so the guard works with pyproj.CRS, rasterio.crs.CRS,
    a bare EPSG int, an "EPSG:xxxx" string, or a test stub exposing
    ``is_projected`` -- and so the guard itself is unit-testable without GDAL.
    """
    flag = getattr(crs, "is_projected", None)
    if isinstance(flag, bool):
        return flag

    try:  # pragma: no cover - exercised only when pyproj is installed
        import pyproj
    except ImportError as exc:  # pragma: no cover
        raise CRSError(
            f"cannot determine whether {crs!r} is projected: it exposes no "
            "'is_projected' attribute and pyproj is not installed"
        ) from exc

    try:
        return bool(pyproj.CRS.from_user_input(crs).is_projected)
    except Exception as exc:
        raise CRSError(f"unrecognised CRS: {crs!r}") from exc


class CRSGuard:
    """Assertions that stop metric maths from running on degrees."""

    @staticmethod
    def require_projected(crs: Any, operation: str) -> None:
        """Raise unless ``crs`` is projected (i.e. has metric axes).

        Call this at the top of anything that computes an area, length, volume
        or slope.
        """
        if crs is None:
            raise CRSError(
                f"{operation} requires a projected CRS but the dataset has none. "
                "Reproject to the local UTM zone first - see utm_epsg_for()."
            )
        if not _resolve_is_projected(crs):
            raise CRSError(
                f"{operation} attempted in a geographic CRS ({crs}). Distances "
                "and areas would come out in degrees, not metres. Reproject to "
                "the local UTM zone first - see utm_epsg_for()."
            )

    @staticmethod
    def require_geographic(crs: Any, operation: str) -> None:
        """Raise unless ``crs`` is geographic. For API-boundary checks."""
        if crs is None:
            raise CRSError(f"{operation} requires a geographic CRS but got none")
        if _resolve_is_projected(crs):
            raise CRSError(
                f"{operation} expects lon/lat (EPSG:4326) but got a projected "
                f"CRS ({crs}). GeoJSON leaving the API must be in EPSG:4326."
            )
