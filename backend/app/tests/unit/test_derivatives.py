"""Composing the map layers, and not writing the same raster twice (M2-3).

No database and no tile server: `build` is given `None` for the session because
the rasters are useful without one, and the contour endpoints deliberately work
with no database at all.
"""

from __future__ import annotations

import urllib.parse
from pathlib import Path

import numpy as np
import pytest
from rasterio.transform import from_origin

from app.services import derivatives

TRANSFORM = tuple(from_origin(530000.0, 2352000.0, 5.0, 5.0))[:6]
EPSG = 32644
CELL = 5.0


@pytest.fixture
def dome() -> np.ndarray:
    """A hill, so slope and hillshade have something to describe."""
    size = 600
    rows, cols = np.mgrid[0:size, 0:size].astype(np.float32)
    centre = size / 2
    surface = 280.0 + 14.0 * np.exp(
        -(((rows - centre) ** 2 + (cols - centre) ** 2) / (2 * (size / 4) ** 2))
    )
    surface[:20, :20] = np.nan
    return surface.astype(np.float32)


def build(dome: np.ndarray, store: Path, **kwargs: object) -> list[derivatives.Layer]:
    return derivatives.build(
        None,
        elevation=dome,
        transform=TRANSFORM,
        epsg=EPSG,
        cell_size_m=CELL,
        store=store,
        **kwargs,  # type: ignore[arg-type]
    )


class TestWhatGetsBuilt:
    def test_all_three_products_by_default(self, dome: np.ndarray, tmp_path: Path) -> None:
        layers = build(dome, tmp_path)
        assert [layer.product for layer in layers] == ["dem", "slope", "hillshade"]

    def test_a_subset_can_be_asked_for(self, dome: np.ndarray, tmp_path: Path) -> None:
        layers = build(dome, tmp_path, products=("hillshade",))
        assert [layer.product for layer in layers] == ["hillshade"]

    def test_the_dem_keeps_its_elevations(self, dome: np.ndarray, tmp_path: Path) -> None:
        (layer,) = build(dome, tmp_path, products=("dem",))
        assert 279 < float(layer.asset.stats["min"]) < 281
        assert 293 < float(layer.asset.stats["max"]) < 295

    def test_the_slope_is_a_percentage_not_a_ratio(self, dome: np.ndarray, tmp_path: Path) -> None:
        """A 14 m rise over ~750 m reads a few percent, not a few hundredths."""
        (layer,) = build(dome, tmp_path, products=("slope",))
        assert float(layer.asset.stats["min"]) >= 0.0
        assert 0.1 < float(layer.asset.stats["max"]) < 100.0

    def test_the_hillshade_is_a_byte_band(self, dome: np.ndarray, tmp_path: Path) -> None:
        (layer,) = build(dome, tmp_path, products=("hillshade",))
        assert layer.asset.dtype == "uint8"
        assert 0 <= float(layer.asset.stats["min"]) <= 254
        assert float(layer.asset.stats["max"]) <= 254

    def test_nodata_survives_into_every_product(self, dome: np.ndarray, tmp_path: Path) -> None:
        """A hole in the DEM must be a hole in the derivatives, not zero slope."""
        for layer in build(dome, tmp_path):
            assert int(layer.asset.stats["nodata_cells"]) > 0, layer.product


class TestContentAddressing:
    def test_the_second_call_reuses_the_rasters(self, dome: np.ndarray, tmp_path: Path) -> None:
        first = build(dome, tmp_path)
        second = build(dome, tmp_path)
        assert all(not layer.reused for layer in first)
        assert all(layer.reused for layer in second)
        assert [a.asset.path for a in first] == [b.asset.path for b in second]

    def test_a_different_dem_gets_different_paths(self, dome: np.ndarray, tmp_path: Path) -> None:
        first = build(dome, tmp_path)
        second = build(dome + 5.0, tmp_path)
        assert {a.asset.path for a in first}.isdisjoint({b.asset.path for b in second})

    def test_hillshade_parameters_are_part_of_the_key(
        self, dome: np.ndarray, tmp_path: Path
    ) -> None:
        """Two illuminations are two rasters; sharing a path would serve one for
        the other and the difference is invisible in a tile."""
        (a,) = build(dome, tmp_path, products=("hillshade",), hillshade_azimuth_deg=315.0)
        (b,) = build(dome, tmp_path, products=("hillshade",), hillshade_azimuth_deg=135.0)
        assert a.asset.path != b.asset.path
        assert not b.reused

    def test_elevation_alone_does_not_key_the_hillshade(
        self, dome: np.ndarray, tmp_path: Path
    ) -> None:
        """The z-factor changes the pixels, so it has to change the key."""
        (a,) = build(dome, tmp_path, products=("hillshade",), hillshade_z_factor=1.0)
        (b,) = build(dome, tmp_path, products=("hillshade",), hillshade_z_factor=5.0)
        assert a.asset.path != b.asset.path

    def test_the_store_is_fanned_out(self, dome: np.ndarray, tmp_path: Path) -> None:
        """All rasters in one directory stops being workable at scale."""
        (layer,) = build(dome, tmp_path, products=("dem",))
        assert layer.asset.path.parent != tmp_path
        assert len(layer.asset.path.parent.name) == 2


class TestTileTemplates:
    def test_the_template_keeps_its_placeholders(self, dome: np.ndarray, tmp_path: Path) -> None:
        """The map client fills these; expanding them here would break it."""
        for layer in build(dome, tmp_path):
            for placeholder in ("{z}", "{x}", "{y}"):
                assert placeholder in layer.tile_url_template, layer.product

    def test_it_points_at_the_written_raster(self, dome: np.ndarray, tmp_path: Path) -> None:
        for layer in build(dome, tmp_path):
            query = urllib.parse.parse_qs(layer.tile_url_template.split("?", 1)[1])
            assert query["url"] == [str(layer.asset.path)]

    def test_every_layer_carries_a_rescale(self, dome: np.ndarray, tmp_path: Path) -> None:
        """Without one, a float32 band renders as an almost-black tile."""
        for layer in build(dome, tmp_path):
            query = urllib.parse.parse_qs(layer.tile_url_template.split("?", 1)[1])
            low, high = (float(v) for v in query["rescale"][0].split(","))
            assert high > low, layer.product

    def test_slope_uses_a_fixed_range(self, dome: np.ndarray, tmp_path: Path) -> None:
        """Per-raster rescaling would make a flat plateau look like a hillside."""
        (layer,) = build(dome, tmp_path, products=("slope",))
        query = urllib.parse.parse_qs(layer.tile_url_template.split("?", 1)[1])
        assert query["rescale"] == ["0,15"]

    def test_the_dem_rescales_to_its_own_percentiles(
        self, dome: np.ndarray, tmp_path: Path
    ) -> None:
        """A fixed elevation range renders a 30 m-relief plateau one flat colour."""
        (layer,) = build(dome, tmp_path, products=("dem",))
        query = urllib.parse.parse_qs(layer.tile_url_template.split("?", 1)[1])
        low, high = (float(v) for v in query["rescale"][0].split(","))
        assert 275 < low < high < 300

    def test_a_perfectly_flat_dem_does_not_produce_a_zero_width_rescale(
        self, tmp_path: Path
    ) -> None:
        """`rescale=x,x` makes the tiler divide by zero."""
        flat = np.full((600, 600), 281.0, dtype=np.float32)
        (layer,) = build(flat, tmp_path, products=("dem",))
        query = urllib.parse.parse_qs(layer.tile_url_template.split("?", 1)[1])
        low, high = (float(v) for v in query["rescale"][0].split(","))
        assert high > low

    def test_only_the_dem_and_slope_get_a_colormap(self, dome: np.ndarray, tmp_path: Path) -> None:
        """The hillshade band *is* the grey value; colouring it destroys it."""
        by_product = {layer.product: layer.tile_url_template for layer in build(dome, tmp_path)}
        assert "colormap_name" in by_product["dem"]
        assert "colormap_name" in by_product["slope"]
        assert "colormap_name" not in by_product["hillshade"]

    def test_every_layer_has_a_legend(self, dome: np.ndarray, tmp_path: Path) -> None:
        for layer in build(dome, tmp_path):
            assert layer.legend.strip(), layer.product


class TestTheTilePathIsTheTilersPath:
    """A tile URL carries `?url=<the COG>`, and TiTiler opens that path itself.

    So it has to be TiTiler's path, not ours. The two strings agree only when the
    API is also a container. Run the API on the host and it writes
    `<repo>/data/cache/x.tif` while TiTiler serves the identical bytes from
    `/data/cache/x.tif` -- so every tile answered HTTP 500 "No such file or
    directory" while the API itself reported success and the slope and
    shaded-relief layers were simply blank. A silent blank layer is the worst
    shape this failure could take, which is why it is pinned here.
    """

    def test_the_store_prefix_is_rewritten(self, monkeypatch, tmp_path: Path) -> None:
        from app.config import get_settings

        store = tmp_path / "cache"
        (store / "cog" / "ab").mkdir(parents=True)
        cog = store / "cog" / "ab" / "abc-slope.tif"
        cog.touch()

        settings = get_settings()
        monkeypatch.setattr(settings, "COG_STORE_PATH", str(store), raising=False)
        monkeypatch.setattr(settings, "TILER_STORE_PATH", "/data/cache", raising=False)
        assert derivatives.tiler_path(cog) == "/data/cache/cog/ab/abc-slope.tif"

    def test_equal_paths_are_a_no_op(self, monkeypatch, tmp_path: Path) -> None:
        """The container case: translating would be a bug, not a nicety."""
        from app.config import get_settings

        store = tmp_path / "cache"
        store.mkdir()
        cog = store / "x.tif"
        cog.touch()
        settings = get_settings()
        monkeypatch.setattr(settings, "COG_STORE_PATH", str(store), raising=False)
        monkeypatch.setattr(settings, "TILER_STORE_PATH", str(store), raising=False)
        assert derivatives.tiler_path(cog) == str(cog)

    def test_a_path_outside_the_store_is_untouched(self, monkeypatch, tmp_path: Path) -> None:
        """A remote /vsicurl URL is already openable; a prefix would break it."""
        from app.config import get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "COG_STORE_PATH", str(tmp_path / "cache"), raising=False)
        monkeypatch.setattr(settings, "TILER_STORE_PATH", "/data/cache", raising=False)
        remote = "/vsicurl/https://example.org/dem.tif"
        assert derivatives.tiler_path(remote) == remote

    def test_an_empty_tiler_path_disables_translation(self, monkeypatch, tmp_path: Path) -> None:
        from app.config import get_settings

        store = tmp_path / "cache"
        store.mkdir()
        cog = store / "x.tif"
        cog.touch()
        settings = get_settings()
        monkeypatch.setattr(settings, "COG_STORE_PATH", str(store), raising=False)
        monkeypatch.setattr(settings, "TILER_STORE_PATH", "", raising=False)
        assert derivatives.tiler_path(cog) == str(cog)

    def test_the_emitted_template_carries_the_translated_path(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        """End to end through `_tile_url`, not just the helper."""
        from app.config import get_settings

        store = tmp_path / "cache"
        (store / "cog").mkdir(parents=True)
        cog = store / "cog" / "s.tif"
        cog.touch()
        settings = get_settings()
        monkeypatch.setattr(settings, "COG_STORE_PATH", str(store), raising=False)
        monkeypatch.setattr(settings, "TILER_STORE_PATH", "/data/cache", raising=False)

        asset = derivatives.raster.RasterAsset(
            product="slope",
            path=cog,
            epsg=32644,
            resolution_m=5.0,
            width=10,
            height=10,
            dtype="float32",
            nodata=-9999.0,
            bounds_4326=(81.0, 21.0, 81.1, 21.1),
            size_bytes=1,
            checksum_sha256="0" * 64,
            stats={"p2": 0.0, "p98": 10.0},
        )
        query = urllib.parse.parse_qs(urllib.parse.urlparse(derivatives._tile_url(asset)).query)
        assert query["url"][0] == "/data/cache/cog/s.tif"
        assert not query["url"][0].startswith(str(tmp_path)), "our own path must not leak out"
