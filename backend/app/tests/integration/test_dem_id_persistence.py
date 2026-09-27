"""A `dem_id` outlives its place in memory.

On a 512 MB lab system `DEM_CACHE_LIMIT` is 1, and with a memory-only registry
the second user's run evicted the first user's terrain: their next click on the
map -- streams, click-to-delineate, available land -- answered 404. Grids are
now written to disk as they are registered and read back on a miss.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from app.providers.elevation import copernicus_aws
from app.providers.elevation.base import Bounds
from app.services import dem_cache
from app.tests.area_fakes import DURG_BBOX, FakeFetch, fake_dem


@pytest.fixture
def one_in_memory(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(dem_cache, "CACHE_LIMIT", 1)
    dem_cache.clear()
    yield
    dem_cache.clear()


def register(offset: float = 0.0) -> tuple[str, Any, Bounds]:
    bounds = Bounds(DURG_BBOX[0] + offset, DURG_BBOX[1], DURG_BBOX[2] + offset, DURG_BBOX[3])
    dem = fake_dem(bounds)
    return (
        dem_cache.remember(None, dem, None, bounds=bounds, source="copernicus_glo30"),
        dem,
        bounds,
    )


class TestTheRegistry:
    def test_an_evicted_grid_is_read_back_from_disk(self, one_in_memory: None) -> None:
        first, dem, bounds = register()
        register(0.01)  # evicts `first` from memory
        assert dem_cache.size() == 1
        entry = dem_cache.get(first)
        assert entry is not None
        np.testing.assert_array_equal(entry["dem"].elevation, dem.elevation)
        assert entry["dem"].transform == pytest.approx(dem.transform)
        assert entry["dem"].epsg == dem.epsg
        assert entry["bounds"] == bounds
        assert entry["source"] == "copernicus_glo30"

    def test_a_grid_survives_a_restart(self, one_in_memory: None) -> None:
        dem_id, _, _ = register()
        dem_cache.clear()  # what a restart does to memory
        assert dem_cache.get(dem_id) is not None

    def test_a_read_back_entry_has_no_parsed_lines(self, one_in_memory: None) -> None:
        """So the contours route traces them from the grid instead."""
        first, _, _ = register()
        register(0.01)
        entry = dem_cache.get(first)
        assert entry is not None and entry["parsed"] is None

    def test_memory_keeps_the_most_recently_used(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(dem_cache, "CACHE_LIMIT", 2)
        dem_cache.clear()
        a, _, _ = register()
        b, _, _ = register(0.01)
        dem_cache.get(a)  # touch a: b is now the oldest
        register(0.02)
        with dem_cache._lock:
            held = set(dem_cache._entries)
        assert a in held and b not in held
        dem_cache.clear()

    @pytest.mark.parametrize(
        "dem_id", ["../../etc/passwd", "..", "ABCDEF0123456789", "0123", "x" * 16, ""]
    )
    def test_a_malformed_id_never_touches_the_filesystem(self, dem_id: str) -> None:
        assert dem_cache.get(dem_id) is None

    def test_an_unknown_id_is_none(self) -> None:
        assert dem_cache.get("0123456789abcdef") is None

    def test_a_disk_that_refuses_still_leaves_memory_working(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.config import get_settings

        blocked = tmp_path / "not-a-directory"
        blocked.write_text("")
        monkeypatch.setenv("COG_STORE_PATH", str(blocked))
        get_settings.cache_clear()
        dem_id, _, _ = register()
        assert dem_cache.get(dem_id) is not None

    def test_the_disk_copy_is_bounded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(dem_cache, "MAX_DISK_ENTRIES", 3)
        for i in range(5):
            register(i * 0.01)
        assert len(list(dem_cache._store().glob("*.npz"))) == 3
        assert not list(dem_cache._store().glob("*.tmp"))


class TestTheSecondUserDoesNotBreakTheFirst:
    def test_follow_ups_on_an_evicted_run_still_work(
        self, client: Any, one_in_memory: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(copernicus_aws, "fetch_dem", FakeFetch())
        first = client.post("/api/v1/analyzeArea", json={"bbox": DURG_BBOX, "enrich": False})
        moved = [DURG_BBOX[0] + 0.01, DURG_BBOX[1], DURG_BBOX[2] + 0.01, DURG_BBOX[3]]
        second = client.post("/api/v1/analyzeArea", json={"bbox": moved, "enrich": False})
        assert first.status_code == second.status_code == 200
        dem_id = first.json()["dem_id"]
        streams = client.post("/api/v1/hydrology/streams", data={"dem_id": dem_id})
        assert streams.status_code == 200, streams.text
        contours = client.get(f"/api/v1/terrain/contour-map/{dem_id}/contours")
        assert contours.status_code == 200
