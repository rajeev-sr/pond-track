"""Hillshade and COG writing (M2-3, M2-4).

The hillshade cases are analytic: a plane of known aspect has a hillshade value
that can be computed by hand, so these are correctness tests rather than
snapshots. That matters because the failure mode is not a crash -- an
illumination rotated by 90 degrees renders a perfectly plausible shaded relief of
the wrong terrain, and the first implementation here did exactly that.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from app.services.raster import (
    COG_BLOCK_SIZE,
    DEFAULT_ALTITUDE_DEG,
    NODATA_FLOAT,
    NODATA_HILLSHADE,
    RasterWriteError,
    cache_key,
    hillshade,
    write_cog,
)

CELL = 10.0
SIZE = 41
MID = SIZE // 2

# UTM 44N over Durg, 5 m cells -- the working CRS the pipeline actually derives.
TRANSFORM = from_origin(530000.0, 2352000.0, 5.0, 5.0)
EPSG = 32644


def plane_facing(compass_deg: float, drop_per_m: float = 0.3) -> np.ndarray:
    """A plane whose downhill direction is `compass_deg`.

    Row 0 is the northern edge, so the northward coordinate decreases with the
    row index -- getting that backwards is one of the two sign errors this file
    exists to catch.
    """
    rows, cols = np.mgrid[0:SIZE, 0:SIZE].astype(float)
    angle = math.radians(compass_deg)
    east = cols * CELL
    north = (SIZE - 1 - rows) * CELL
    return 100.0 - drop_per_m * (math.sin(angle) * east + math.cos(angle) * north)


class TestHillshadeGeometry:
    def test_the_brightest_facet_faces_the_light(self) -> None:
        """With light from the north-west, the north-west-facing slope is lit."""
        values = {
            facing: int(hillshade(plane_facing(facing), CELL, azimuth_deg=315)[MID, MID])
            for facing in range(0, 360, 45)
        }
        assert max(values, key=lambda f: values[f]) == 315, values

    def test_the_darkest_facet_faces_away(self) -> None:
        values = {
            facing: int(hillshade(plane_facing(facing), CELL, azimuth_deg=315)[MID, MID])
            for facing in range(0, 360, 45)
        }
        assert min(values, key=lambda f: values[f]) == 135, values

    @pytest.mark.parametrize("light", [0, 45, 90, 135, 180, 225, 270, 315])
    def test_it_holds_for_every_light_direction(self, light: int) -> None:
        """Not just the default. A conversion error can be right at one azimuth."""
        values = {
            facing: int(hillshade(plane_facing(facing), CELL, azimuth_deg=light)[MID, MID])
            for facing in range(0, 360, 45)
        }
        assert max(values, key=lambda f: values[f]) == light, (light, values)

    def test_flat_ground_is_the_sine_of_the_light_altitude(self) -> None:
        """A horizontal surface's normal is straight up, so N.L = sin(altitude)."""
        flat = np.full((SIZE, SIZE), 50.0)
        expected = round(254.0 * math.sin(math.radians(DEFAULT_ALTITUDE_DEG)))
        assert int(hillshade(flat, CELL)[MID, MID]) == expected

    def test_a_hand_computed_value_matches(self) -> None:
        """The full arithmetic, so the scaling is pinned and not just the ordering.

        A plane falling 0.3 m/m toward 315 degrees, lit from 315 at 45 degrees:
        dz/dx = +0.2121, dz/dy = -0.2121, so
        N.L / |N| = (0.1061 + 0.1061 + 0.7071) / sqrt(1.0900) = 0.8814,
        and 0.8814 * 254 = 224.
        """
        assert int(hillshade(plane_facing(315), CELL, azimuth_deg=315)[MID, MID]) == 224

    def test_a_steeper_light_flattens_the_contrast(self) -> None:
        """At 90 degrees the light is overhead and every facet reads alike."""
        overhead = [
            int(hillshade(plane_facing(f), CELL, altitude_deg=90)[MID, MID])
            for f in range(0, 360, 45)
        ]
        assert max(overhead) - min(overhead) <= 1, overhead

    def test_exaggeration_increases_the_range(self) -> None:
        gentle = plane_facing(315, drop_per_m=0.02)
        plain = int(hillshade(gentle, CELL, z_factor=1.0)[MID, MID])
        raised = int(hillshade(gentle, CELL, z_factor=10.0)[MID, MID])
        assert raised > plain


class TestHillshadeNodata:
    def test_a_nodata_cell_stays_nodata(self) -> None:
        surface = plane_facing(315)
        surface[10, 10] = np.nan
        assert int(hillshade(surface, CELL)[10, 10]) == NODATA_HILLSHADE

    def test_its_neighbours_are_still_shaded(self) -> None:
        """One missing cell must not punch a hole three cells wide."""
        surface = plane_facing(315)
        surface[10, 10] = np.nan
        assert int(hillshade(surface, CELL)[10, 13]) != NODATA_HILLSHADE

    def test_zero_is_a_real_value_not_a_sentinel(self) -> None:
        """Full shadow is 0; nodata is 255. Conflating them loses the shadows."""
        cliff = plane_facing(135, drop_per_m=50.0)
        shaded = hillshade(cliff, CELL, azimuth_deg=315, altitude_deg=5)
        assert int(shaded[MID, MID]) < 10
        assert int(shaded[MID, MID]) != NODATA_HILLSHADE


class TestHillshadeValidation:
    def test_a_non_positive_cell_size_is_refused(self) -> None:
        with pytest.raises(ValueError, match="cell size"):
            hillshade(plane_facing(0), 0.0)

    @pytest.mark.parametrize("altitude", [0.0, -10.0, 91.0])
    def test_an_impossible_light_altitude_is_refused(self, altitude: float) -> None:
        with pytest.raises(ValueError, match="altitude"):
            hillshade(plane_facing(0), CELL, altitude_deg=altitude)


class TestWritingACog:
    @pytest.fixture
    def dem(self) -> np.ndarray:
        rows, cols = np.mgrid[0:1100, 0:1100].astype(np.float32)
        surface = (280.0 + 0.01 * cols - 0.008 * rows).astype(np.float32)
        surface[:40, :40] = NODATA_FLOAT
        return surface

    def test_it_is_tiled_with_overviews(self, dem: np.ndarray, tmp_path: Path) -> None:
        """What makes a COG a COG. Without overviews, rendering a zoomed-out tile
        reads the full-resolution grid."""
        asset = write_cog(
            tmp_path / "dem.tif",
            dem,
            transform=TRANSFORM,
            epsg=EPSG,
            nodata=NODATA_FLOAT,
            product="dem",
        )
        with rasterio.open(asset.path) as handle:
            # The block shape is the tiling; `is_tiled` is deprecated and says
            # no more than this does.
            assert handle.block_shapes[0] == (COG_BLOCK_SIZE, COG_BLOCK_SIZE)
            assert handle.overviews(1), "no overviews were built"
            assert handle.compression is not None

    def test_the_georeferencing_round_trips(self, dem: np.ndarray, tmp_path: Path) -> None:
        asset = write_cog(
            tmp_path / "dem.tif",
            dem,
            transform=TRANSFORM,
            epsg=EPSG,
            nodata=NODATA_FLOAT,
            product="dem",
        )
        with rasterio.open(asset.path) as handle:
            assert handle.crs.to_epsg() == EPSG
            assert handle.nodata == NODATA_FLOAT
            assert handle.transform.a == pytest.approx(5.0)
        # And the reported lon/lat extent lands in central India.
        west, south, east, north = asset.bounds_4326
        assert 80 < west < east < 83
        assert 20 < south < north < 23

    def test_the_statistics_exclude_nodata(self, dem: np.ndarray, tmp_path: Path) -> None:
        """Including it would report a minimum elevation of -9999 m, and a tiler
        told to rescale on that renders the whole raster one flat colour."""
        asset = write_cog(
            tmp_path / "dem.tif",
            dem,
            transform=TRANSFORM,
            epsg=EPSG,
            nodata=NODATA_FLOAT,
            product="dem",
        )
        assert asset.stats["min"] > 200
        assert asset.stats["nodata_cells"] == 40 * 40
        assert asset.stats["valid_cells"] == dem.size - 40 * 40

    def test_no_partial_file_is_left_behind(self, dem: np.ndarray, tmp_path: Path) -> None:
        """TiTiler reads this directory; it must never see a half-written path."""
        write_cog(
            tmp_path / "dem.tif",
            dem,
            transform=TRANSFORM,
            epsg=EPSG,
            nodata=NODATA_FLOAT,
            product="dem",
        )
        assert list(tmp_path.glob("*.part")) == []

    def test_the_checksum_matches_the_bytes_on_disk(self, dem: np.ndarray, tmp_path: Path) -> None:
        import hashlib

        asset = write_cog(
            tmp_path / "dem.tif",
            dem,
            transform=TRANSFORM,
            epsg=EPSG,
            nodata=NODATA_FLOAT,
            product="dem",
        )
        assert asset.checksum_sha256 == hashlib.sha256(asset.path.read_bytes()).hexdigest()

    def test_a_small_raster_needs_no_overviews(self, tmp_path: Path) -> None:
        """Below one block there is nothing to build overviews from, and
        rejecting it would fail on a legitimately tiny survey."""
        tiny = np.full((16, 16), 280.0, dtype=np.float32)
        asset = write_cog(
            tmp_path / "tiny.tif",
            tiny,
            transform=TRANSFORM,
            epsg=EPSG,
            nodata=NODATA_FLOAT,
            product="dem",
        )
        assert asset.width == 16

    @pytest.mark.parametrize("shape", [(5,), (4, 4, 4)])
    def test_a_non_2d_array_is_refused(self, shape: tuple[int, ...], tmp_path: Path) -> None:
        with pytest.raises(RasterWriteError, match="2-D"):
            write_cog(
                tmp_path / "bad.tif",
                np.zeros(shape, dtype=np.float32),
                transform=TRANSFORM,
                epsg=EPSG,
                nodata=NODATA_FLOAT,
                product="dem",
            )

    def test_a_degenerate_raster_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(RasterWriteError, match="too small"):
            write_cog(
                tmp_path / "bad.tif",
                np.zeros((1, 1), dtype=np.float32),
                transform=TRANSFORM,
                epsg=EPSG,
                nodata=NODATA_FLOAT,
                product="dem",
            )

    def test_uint8_hillshade_survives_the_round_trip(self, tmp_path: Path) -> None:
        shaded = hillshade(plane_facing(315), CELL)
        asset = write_cog(
            tmp_path / "hs.tif",
            shaded,
            transform=TRANSFORM,
            epsg=EPSG,
            nodata=NODATA_HILLSHADE,
            product="hillshade",
        )
        assert asset.dtype == "uint8"
        with rasterio.open(asset.path) as handle:
            assert np.array_equal(handle.read(1), shaded)


class TestCacheKeys:
    def test_the_same_inputs_give_the_same_key(self) -> None:
        assert cache_key("dem", 5.0, 32644) == cache_key("dem", 5.0, 32644)

    def test_different_inputs_give_different_keys(self) -> None:
        assert cache_key("dem", 5.0) != cache_key("dem", 10.0)
        assert cache_key("dem", 5.0) != cache_key("slope", 5.0)

    def test_the_key_fits_the_column(self) -> None:
        """`dem_assets.cache_key` is varchar(64); a hash fits whatever goes in."""
        assert len(cache_key(b"x" * 10_000_000, "dem", 5.0)) == 64


class TestAnUnwritableStoreIsAnAnswerNotACrash:
    """`write_cog` created its parent directory with no guard at all.

    Observed, not hypothesised: `COG_STORE_PATH` defaulted to the container's
    `/data/cache`, so a host-run uvicorn tried to `mkdir /data`, and the
    resulting `PermissionError` -- an `OSError`, not a `RasterWriteError` --
    escaped the endpoint's handler and became a 500 with a 200-line traceback.
    The default is fixed; this keeps the failure mode from returning, because a
    store can be unwritable for reasons no default can prevent.
    """

    @pytest.fixture
    def small(self) -> np.ndarray:
        rows, cols = np.mgrid[0:600, 0:600].astype(np.float32)
        return (280.0 + 0.01 * cols - 0.008 * rows).astype(np.float32)

    def test_an_unwritable_parent_raises_rasterwriteerror(
        self, small: np.ndarray, tmp_path: Path
    ) -> None:
        locked = tmp_path / "locked"
        locked.mkdir()
        locked.chmod(0o500)  # readable, not writable
        try:
            with pytest.raises(RasterWriteError):
                write_cog(
                    locked / "sub" / "dem.tif",
                    small,
                    transform=TRANSFORM,
                    epsg=EPSG,
                    nodata=NODATA_FLOAT,
                    product="dem",
                )
        finally:
            locked.chmod(0o700)

    def test_the_message_names_the_setting_to_change(
        self, small: np.ndarray, tmp_path: Path
    ) -> None:
        """A traceback tells the reader nothing they can act on."""
        locked = tmp_path / "locked2"
        locked.mkdir()
        locked.chmod(0o500)
        try:
            with pytest.raises(RasterWriteError) as exc:
                write_cog(
                    locked / "sub" / "dem.tif",
                    small,
                    transform=TRANSFORM,
                    epsg=EPSG,
                    nodata=NODATA_FLOAT,
                    product="dem",
                )
        finally:
            locked.chmod(0o700)
        assert "COG_STORE_PATH" in str(exc.value)
