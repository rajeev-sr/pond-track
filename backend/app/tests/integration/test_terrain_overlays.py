"""Slope and shaded relief as images, with no tile server.

The tiled layers need TiTiler, which the lab deployment does not run, so the two
legend entries could be switched on and drew nothing. These are single PNGs the
map pins to the grid's four corners.
"""

from __future__ import annotations

import io
from typing import Any

import numpy as np
import pytest

from app.providers.elevation import copernicus_aws
from app.providers.elevation.base import Bounds
from app.services import overlays
from app.tests.area_fakes import DURG_BBOX, FakeFetch, fake_dem


@pytest.fixture
def dem() -> Any:
    return fake_dem(Bounds(*DURG_BBOX))


class TestTheImages:
    @pytest.mark.parametrize("product", overlays.PRODUCTS)
    def test_a_png_the_size_of_the_grid(self, dem: Any, product: str) -> None:
        from PIL import Image

        png = overlays.render(dem, product)  # type: ignore[arg-type]
        assert png.startswith(b"\x89PNG")
        image = Image.open(io.BytesIO(png))
        assert image.mode == "RGBA"
        assert image.size == (dem.shape[1], dem.shape[0])

    def test_no_elevation_is_transparent(self, dem: Any) -> None:
        from PIL import Image

        dem.elevation[:10, :10] = np.nan
        alpha = np.asarray(Image.open(io.BytesIO(overlays.render(dem, "slope"))))[..., 3]
        assert (alpha[:10, :10] == 0).all()
        assert (alpha[20:, 20:] == 255).all()

    def test_the_corners_are_in_image_order(self, dem: Any) -> None:
        tl, tr, br, bl = overlays.corners_4326(dem)
        assert tl[1] > bl[1] and tr[1] > br[1]  # top above bottom
        assert tr[0] > tl[0] and br[0] > bl[0]  # right east of left
        # The grid includes the 500 m margin around the drawn box.
        assert tl[0] < DURG_BBOX[0] and br[0] > DURG_BBOX[2]


class TestTheRoutes:
    @pytest.fixture
    def dem_id(self, client: Any, monkeypatch: pytest.MonkeyPatch) -> str:
        monkeypatch.setattr(copernicus_aws, "fetch_dem", FakeFetch())
        body = client.post("/api/v1/analyzeArea", json={"bbox": DURG_BBOX, "enrich": False}).json()
        return str(body["dem_id"])

    def test_the_metadata_lists_both_layers(self, client: Any, dem_id: str) -> None:
        body = client.get(f"/api/v1/terrain/{dem_id}/overlays").json()
        assert [o["product"] for o in body["overlays"]] == ["hillshade", "slope"]
        for overlay in body["overlays"]:
            assert len(overlay["coordinates"]) == 4
            assert overlay["url"].endswith(f"/overlay/{overlay['product']}")

    def test_each_url_serves_its_image(self, client: Any, dem_id: str) -> None:
        for overlay in client.get(f"/api/v1/terrain/{dem_id}/overlays").json()["overlays"]:
            response = client.get(overlay["url"])
            assert response.status_code == 200
            assert response.headers["content-type"] == "image/png"
            assert response.content.startswith(b"\x89PNG")

    def test_a_second_request_is_served_from_disk(self, client: Any, dem_id: str) -> None:
        url = f"/api/v1/terrain/{dem_id}/overlay/hillshade"
        assert client.get(url).content == client.get(url).content

    def test_an_unknown_layer_is_a_400(self, client: Any, dem_id: str) -> None:
        assert client.get(f"/api/v1/terrain/{dem_id}/overlay/aspect").status_code == 400

    def test_an_unknown_dem_is_a_404(self, client: Any) -> None:
        assert client.get("/api/v1/terrain/0123456789abcdef/overlays").status_code == 404
        response = client.get("/api/v1/terrain/0123456789abcdef/overlay/slope")
        assert response.status_code == 404
