"""`cached_fetch_dem`: a drawn area is fetched from Copernicus once, then read
from disk -- and a cache problem is never an analysis failure."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from app.providers.elevation import copernicus_aws
from app.providers.elevation.base import Bounds
from app.tests.area_fakes import DURG_BBOX, FakeFetch

BOX = Bounds(*DURG_BBOX)


@pytest.fixture
def fetch() -> FakeFetch:
    return FakeFetch()


class TestTheCache:
    def test_the_first_read_fetches_and_the_second_does_not(
        self, tmp_path: Path, fetch: FakeFetch
    ) -> None:
        first, first_cached = copernicus_aws.cached_fetch_dem(BOX, store=tmp_path, fetch=fetch)
        second, second_cached = copernicus_aws.cached_fetch_dem(BOX, store=tmp_path, fetch=fetch)
        assert (first_cached, second_cached) == (False, True)
        assert fetch.calls == 1

    def test_the_cached_grid_is_the_grid_that_was_fetched(
        self, tmp_path: Path, fetch: FakeFetch
    ) -> None:
        first, _ = copernicus_aws.cached_fetch_dem(BOX, store=tmp_path, fetch=fetch)
        second, _ = copernicus_aws.cached_fetch_dem(BOX, store=tmp_path, fetch=fetch)
        np.testing.assert_array_equal(first.elevation, second.elevation)
        assert second.elevation.dtype == np.float32
        assert second.transform == pytest.approx(first.transform)
        assert second.epsg == first.epsg
        assert second.cell_size_m == first.cell_size_m

    def test_a_hit_says_so_in_the_provenance(self, tmp_path: Path, fetch: FakeFetch) -> None:
        copernicus_aws.cached_fetch_dem(BOX, store=tmp_path, fetch=fetch)
        grid, _ = copernicus_aws.cached_fetch_dem(BOX, store=tmp_path, fetch=fetch)
        assert grid.provenance["cache"] == "hit"
        assert grid.provenance["tiles_used"] == ["fake_tile"]

    def test_a_box_redrawn_to_within_a_metre_shares_the_entry(
        self, tmp_path: Path, fetch: FakeFetch
    ) -> None:
        nudged = Bounds(*(v + 0.000001 for v in DURG_BBOX))
        copernicus_aws.cached_fetch_dem(BOX, store=tmp_path, fetch=fetch)
        _, cached = copernicus_aws.cached_fetch_dem(nudged, store=tmp_path, fetch=fetch)
        assert cached and fetch.calls == 1

    def test_a_different_box_or_buffer_is_a_different_entry(
        self, tmp_path: Path, fetch: FakeFetch
    ) -> None:
        moved = Bounds(DURG_BBOX[0] + 0.01, DURG_BBOX[1], DURG_BBOX[2] + 0.01, DURG_BBOX[3])
        copernicus_aws.cached_fetch_dem(BOX, store=tmp_path, fetch=fetch)
        copernicus_aws.cached_fetch_dem(moved, store=tmp_path, fetch=fetch)
        copernicus_aws.cached_fetch_dem(BOX, store=tmp_path, buffer_m=250.0, fetch=fetch)
        assert fetch.calls == 3

    def test_no_store_means_no_cache(self, fetch: FakeFetch) -> None:
        copernicus_aws.cached_fetch_dem(BOX, store=None, fetch=fetch)
        _, cached = copernicus_aws.cached_fetch_dem(BOX, store=None, fetch=fetch)
        assert not cached and fetch.calls == 2

    def test_the_default_fetcher_is_looked_up_when_called(
        self, tmp_path: Path, fetch: FakeFetch, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """So patching `copernicus_aws.fetch_dem` reaches the API routes too."""
        monkeypatch.setattr(copernicus_aws, "fetch_dem", fetch)
        copernicus_aws.cached_fetch_dem(BOX, store=tmp_path)
        assert fetch.calls == 1


class TestACacheProblemIsNeverAFailure:
    def test_a_corrupt_entry_is_refetched(self, tmp_path: Path, fetch: FakeFetch) -> None:
        copernicus_aws.cached_fetch_dem(BOX, store=tmp_path, fetch=fetch)
        (entry,) = (tmp_path / "dem").rglob("*.npz")
        entry.write_bytes(b"not a zip file")
        grid, cached = copernicus_aws.cached_fetch_dem(BOX, store=tmp_path, fetch=fetch)
        assert not cached and fetch.calls == 2
        assert grid.elevation.size > 0

    def test_an_unwritable_store_still_returns_the_grid(
        self, tmp_path: Path, fetch: FakeFetch
    ) -> None:
        blocked = tmp_path / "blocked"
        blocked.write_text("a file where the cache directory should be")
        grid, cached = copernicus_aws.cached_fetch_dem(BOX, store=blocked, fetch=fetch)
        assert not cached and grid.elevation.size > 0

    def test_no_temporary_file_is_left_behind(self, tmp_path: Path, fetch: FakeFetch) -> None:
        copernicus_aws.cached_fetch_dem(BOX, store=tmp_path, fetch=fetch)
        assert not list(tmp_path.rglob("*.tmp"))
        assert len(list(tmp_path.rglob("*.npz"))) == 1


class TestNoTerrainIsNotAnOutage:
    """Open sea has no Copernicus tile. That must read as "nothing to analyse
    here" (422), not "try again shortly" (503) -- retrying would never help."""

    @staticmethod
    def fail_with(monkeypatch: pytest.MonkeyPatch, message: str) -> None:
        from rasterio.errors import RasterioIOError

        def refuse(*_args: object, **_kwargs: object) -> None:
            raise RasterioIOError(message)

        monkeypatch.setattr(copernicus_aws.rasterio, "open", refuse)

    def test_every_tile_missing_is_no_terrain(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.fail_with(monkeypatch, "HTTP response code: 404")
        with pytest.raises(copernicus_aws.NoTerrainError, match="open water"):
            copernicus_aws.fetch_dem(Bounds(65.50, 10.50, 65.53, 10.53))

    def test_a_network_failure_is_still_an_outage(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.providers.base import ProviderUnavailableError

        self.fail_with(monkeypatch, "CURL error: Connection timed out")
        with pytest.raises(ProviderUnavailableError) as caught:
            copernicus_aws.fetch_dem(BOX)
        assert not isinstance(caught.value, copernicus_aws.NoTerrainError)

    def test_no_terrain_is_still_caught_by_outage_handlers(self) -> None:
        """Any older caller catching the provider error keeps working."""
        from app.providers.base import ProviderUnavailableError

        assert issubclass(copernicus_aws.NoTerrainError, ProviderUnavailableError)


class TestALocalTileNeedsNoNetwork:
    """The lab's internet drops in and out, so the tiles covering the demo
    region are kept on disk and read from there; anywhere else reads S3."""

    @staticmethod
    def write_tile(store: Path, name: str) -> None:
        import rasterio
        from rasterio.transform import from_origin

        target = store / "copernicus" / f"{name}.tif"
        target.parent.mkdir(parents=True)
        n = 120  # 30 arc-seconds: coarse, but a real 1-degree tile's footprint
        rows, cols = np.mgrid[0:n, 0:n]
        z = (300.0 - rows * 0.2 + cols * 0.1).astype(np.float32)
        with rasterio.open(
            target,
            "w",
            driver="GTiff",
            height=n,
            width=n,
            count=1,
            dtype="float32",
            crs="EPSG:4326",
            transform=from_origin(81.0, 22.0, 1.0 / n, 1.0 / n),
            nodata=np.nan,
        ) as dst:
            dst.write(z, 1)

    def test_a_kept_tile_is_read_from_disk(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.config import get_settings

        store = Path(get_settings().COG_STORE_PATH)
        name = copernicus_aws.tile_name(21.25, 81.29)
        self.write_tile(store, name)
        assert copernicus_aws.tile_source(name).startswith(str(store))

        def no_network(*_a: object, **_k: object) -> None:
            raise AssertionError("tried the network with the tile on disk")

        monkeypatch.setattr(copernicus_aws, "tile_url", no_network)
        dem = copernicus_aws.fetch_dem(BOX, cell_size_m=90.0)
        assert dem.provenance["tiles_used"] == [name]
        # Not all: this toy tile is 900 m a pixel, so bilinear resampling leaves a
        # thin rim at the window's edge. The real 30 m tile covers it fully.
        assert np.isfinite(dem.elevation).mean() > 0.8

    def test_without_a_kept_tile_it_reads_the_bucket(self) -> None:
        name = copernicus_aws.tile_name(10.5, 65.5)
        assert copernicus_aws.tile_source(name) == copernicus_aws.tile_url(name)
