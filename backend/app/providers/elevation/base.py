"""The `ElevationSource` seam (HLD ADR-7).

Elevation is an *abstract source*, not a fixed dataset. A remote DEM tile and a
user-uploaded contour map are interchangeable implementations of one protocol,
each producing a metric DEM raster. Everything downstream -- sink filling, D8
flow routing, flow accumulation, catchment delineation, pond siting -- is written
once against `DemGrid` and never learns where the elevation came from.

This is the project's principal extensibility seam: adding a new terrain input
means adding one class here, not forking the pipeline.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

import numpy as np

#: How a contour file's elevations were located, in the parser's priority order.
ElevationStrategy = Literal[
    "coordinate_z",  # third ordinate of each coordinate tuple
    "extended_data",  # <ExtendedData><SimpleData name="ELEV">
    "placemark_name",  # <Placemark><name>277.0</name>
    "folder_name",  # enclosing <Folder><name>contours_1.0m</name>
]


@dataclass(frozen=True)
class Bounds:
    """Geographic envelope in EPSG:4326, degrees."""

    min_lon: float
    min_lat: float
    max_lon: float
    max_lat: float

    @property
    def centroid(self) -> tuple[float, float]:
        return ((self.min_lon + self.max_lon) / 2.0, (self.min_lat + self.max_lat) / 2.0)

    @property
    def width_deg(self) -> float:
        return self.max_lon - self.min_lon

    @property
    def height_deg(self) -> float:
        return self.max_lat - self.min_lat

    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.min_lon, self.min_lat, self.max_lon, self.max_lat)

    def buffered(self, degrees: float) -> Bounds:
        return Bounds(
            self.min_lon - degrees,
            self.min_lat - degrees,
            self.max_lon + degrees,
            self.max_lat + degrees,
        )


@dataclass(frozen=True)
class DemGrid:
    """A DEM in a projected, metric CRS -- the common currency of the pipeline.

    Whatever the source, downstream code sees only this.
    """

    elevation: np.ndarray  # 2-D float32, NaN = nodata
    transform: tuple[float, ...]  # affine, 6 coefficients (GDAL order)
    epsg: int  # projected CRS, always metric (ADR-5)
    cell_size_m: float
    provenance: dict[str, object] = field(default_factory=dict)

    @property
    def shape(self) -> tuple[int, int]:
        return (int(self.elevation.shape[0]), int(self.elevation.shape[1]))

    @property
    def valid_cells(self) -> int:
        return int(np.count_nonzero(~np.isnan(self.elevation)))

    @property
    def relief_m(self) -> float:
        finite = self.elevation[~np.isnan(self.elevation)]
        return float(finite.max() - finite.min()) if finite.size else 0.0

    @property
    def bounds_m(self) -> tuple[float, float, float, float]:
        """(min_x, min_y, max_x, max_y) of the grid in its projected CRS."""
        a, _b, c, _d, e, f = self.transform
        rows, cols = self.shape
        return (c, f + e * rows, c + a * cols, f)

    def rowcol(self, x: float, y: float) -> tuple[int, int]:
        """World (x, y) -> (row, col). Raises IndexError outside the grid."""
        a, _b, c, _d, e, f = self.transform
        col = int(math.floor((x - c) / a))
        row = int(math.floor((y - f) / e))
        rows, cols = self.shape
        if not (0 <= row < rows and 0 <= col < cols):
            raise IndexError(f"({x}, {y}) falls outside the grid ({rows}x{cols})")
        return row, col

    def xy(self, row: int, col: int) -> tuple[float, float]:
        """(row, col) -> world (x, y) at the cell centre."""
        a, _b, c, _d, e, f = self.transform
        return (c + a * (col + 0.5), f + e * (row + 0.5))

    def sample(self, x: float, y: float) -> float:
        """Elevation at a world coordinate; NaN where there is no data."""
        row, col = self.rowcol(x, y)
        return float(self.elevation[row, col])


@runtime_checkable
class ElevationSource(Protocol):
    """Anything that can yield a metric DEM for an area of interest."""

    #: Stable identifier echoed in API responses as `elevation_source`.
    name: str

    def to_dem(self, cell_size_m: float | None = None) -> DemGrid:
        """Produce the DEM. `cell_size_m=None` means "derive a sensible value"."""
        ...
