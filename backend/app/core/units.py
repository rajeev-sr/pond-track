"""Explicit unit conversions.

This module exists for one reason: HLD CH-10 identifies mixed units as the most
common source of results that are wrong by exactly 10, 100 or 1000. Every
conversion in the system goes through a named function here rather than a bare
literal, so the intent is visible at the call site and testable in isolation.

Naming convention: ``<from>_to_<to>``, and every public function name states the
units of both its input and its output.
"""

from __future__ import annotations

M2_PER_HECTARE = 10_000.0
M2_PER_KM2 = 1_000_000.0
MM_PER_M = 1_000.0


def mm_to_m(mm: float) -> float:
    return mm / MM_PER_M


def m_to_mm(m: float) -> float:
    return m * MM_PER_M


def ha_to_m2(ha: float) -> float:
    return ha * M2_PER_HECTARE


def m2_to_ha(m2: float) -> float:
    return m2 / M2_PER_HECTARE


def km2_to_m2(km2: float) -> float:
    return km2 * M2_PER_KM2


def m2_to_km2(m2: float) -> float:
    return m2 / M2_PER_KM2


def ha_to_km2(ha: float) -> float:
    return ha * M2_PER_HECTARE / M2_PER_KM2


def runoff_depth_mm_to_volume_m3(depth_mm: float, area_m2: float) -> float:
    """Convert an SCS-CN runoff *depth* to a *volume*.

    ``Q`` from the SCS-CN equation is a depth in millimetres, not a volume. The
    conversion needs both a mm -> m step and an area in square metres:

        V [m3] = (Q [mm] / 1000) * A [m2]

    Getting this wrong by leaving out the /1000, or by passing hectares as
    square metres, is precisely the failure mode HLD CH-10 warns about. Worked
    reference (HLD 6.9): Q = 361.8 mm over 148.6 ha -> 537_635 m3.
    """
    if depth_mm < 0:
        raise ValueError(f"runoff depth must be non-negative, got {depth_mm}")
    if area_m2 <= 0:
        raise ValueError(f"area must be positive, got {area_m2}")
    return mm_to_m(depth_mm) * area_m2


def cells_to_area_m2(n_cells: int, cell_size_m: float) -> float:
    """Raster cell count -> area. Only valid when the raster is in a metric CRS."""
    if n_cells < 0:
        raise ValueError(f"cell count must be non-negative, got {n_cells}")
    if cell_size_m <= 0:
        raise ValueError(f"cell size must be positive, got {cell_size_m}")
    return n_cells * cell_size_m**2
