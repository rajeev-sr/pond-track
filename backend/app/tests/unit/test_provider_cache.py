"""The soil and land-cover cache, and what DEMO_MODE actually enforces (M7-10).

`DEMO_MODE` was declared in config, echoed by `/health`, and documented in
INSTALL.md as "serve warmed fixtures instead of live providers" — while nothing
in the code read it. These tests pin down the behaviour that claim now refers to:
with it set, a provider may not reach the network, and a miss degrades rather
than hanging on a socket.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from app.providers.base import ProviderUnavailableError
from app.providers.landcover.worldcover import LandCover
from app.providers.soil.soilgrids import SoilProfile
from app.services import provider_cache

LON, LAT = 81.29703, 21.25170
BOUNDS = (81.28, 21.24, 81.31, 21.26)
SHAPE = (16, 16)
TRANSFORM = (5.0, 0.0, 500_000.0, 0.0, -5.0, 2_350_000.0)
EPSG = 32644


def profile() -> SoilProfile:
    return SoilProfile(
        clay_pct=41.2,
        sand_pct=28.0,
        silt_pct=30.8,
        texture_class="clay",
        hydrologic_soil_group="D",
        lon=LON,
        lat=LAT,
    )


def cover() -> LandCover:
    return LandCover(
        codes=np.full(SHAPE, 40, dtype=np.uint8),
        fractions={"cropland": 1.0},
        dominant_class="cropland",
        tiles_used=["ESA_WorldCover_10m_N21E081"],
    )


class TestTheSoilCache:
    def test_the_first_call_fetches_and_the_second_does_not(self, tmp_path: Path) -> None:
        calls: list[tuple[float, float]] = []

        def fake(lon: float, lat: float) -> SoilProfile:
            calls.append((lon, lat))
            return profile()

        first = provider_cache.cached_soil(LON, LAT, tmp_path, fetch=fake)
        second = provider_cache.cached_soil(LON, LAT, tmp_path, fetch=fake)
        assert len(calls) == 1, "the second call went to the network"
        assert second.texture_class == first.texture_class == "clay"
        assert second.hydrologic_soil_group == "D"

    def test_nearby_points_share_an_entry(self, tmp_path: Path) -> None:
        """SoilGrids is 250 m; two points a metre apart cannot differ."""
        calls: list[Any] = []

        def fake(lon: float, lat: float) -> SoilProfile:
            calls.append(1)
            return profile()

        provider_cache.cached_soil(LON, LAT, tmp_path, fetch=fake)
        provider_cache.cached_soil(LON + 1e-6, LAT - 1e-6, tmp_path, fetch=fake)
        assert len(calls) == 1

    def test_a_genuinely_different_point_does_not(self, tmp_path: Path) -> None:
        calls: list[Any] = []

        def fake(lon: float, lat: float) -> SoilProfile:
            calls.append(1)
            return profile()

        provider_cache.cached_soil(LON, LAT, tmp_path, fetch=fake)
        provider_cache.cached_soil(77.0, 28.0, tmp_path, fetch=fake)
        assert len(calls) == 2

    def test_a_stale_entry_is_refetched(self, tmp_path: Path) -> None:
        calls: list[Any] = []

        def fake(lon: float, lat: float) -> SoilProfile:
            calls.append(1)
            return profile()

        provider_cache.cached_soil(LON, LAT, tmp_path, fetch=fake)
        provider_cache.cached_soil(LON, LAT, tmp_path, ttl_s=-1.0, fetch=fake)
        assert len(calls) == 2

    def test_a_corrupt_entry_is_a_miss_not_a_crash(self, tmp_path: Path) -> None:
        provider_cache.cached_soil(LON, LAT, tmp_path, fetch=lambda a, b: profile())
        for path in (tmp_path / "providers" / "soil").rglob("*.json"):
            path.write_text("{not json", encoding="utf-8")
        again = provider_cache.cached_soil(LON, LAT, tmp_path, fetch=lambda a, b: profile())
        assert again.texture_class == "clay"

    def test_an_unwritable_store_still_returns_the_answer(self, tmp_path: Path) -> None:
        """A cache write failing must not cost the caller their data."""
        blocked = tmp_path / "not-a-dir"
        blocked.write_text("", encoding="utf-8")
        got = provider_cache.cached_soil(LON, LAT, blocked, fetch=lambda a, b: profile())
        assert got.hydrologic_soil_group == "D"


class TestTheLandCoverCache:
    def test_the_raster_round_trips(self, tmp_path: Path) -> None:
        provider_cache.cached_landcover(
            BOUNDS, SHAPE, TRANSFORM, EPSG, tmp_path, fetch=lambda *a: cover()
        )
        back = provider_cache.cached_landcover(
            BOUNDS, SHAPE, TRANSFORM, EPSG, tmp_path, fetch=lambda *a: pytest.fail("refetched")
        )
        assert back.codes.shape == SHAPE
        assert back.codes.dtype == np.uint8
        assert int(back.codes[0, 0]) == 40
        assert back.dominant_class == "cropland"
        assert back.tiles_used == ["ESA_WorldCover_10m_N21E081"]

    def test_a_different_grid_is_a_different_entry(self, tmp_path: Path) -> None:
        """Reusing an entry across grids would hand back misaligned classes.

        That is worse than a miss: it produces a plausible composite curve number
        computed from the wrong cells.
        """
        calls: list[Any] = []

        def fake(*_a: Any) -> LandCover:
            calls.append(1)
            return cover()

        provider_cache.cached_landcover(BOUNDS, SHAPE, TRANSFORM, EPSG, tmp_path, fetch=fake)
        other = (10.0, 0.0, 500_000.0, 0.0, -10.0, 2_350_000.0)
        provider_cache.cached_landcover(BOUNDS, SHAPE, other, EPSG, tmp_path, fetch=fake)
        assert len(calls) == 2


class TestDemoModeForbidsTheNetwork:
    def test_a_miss_raises_rather_than_fetching(self, tmp_path: Path) -> None:
        def forbidden(*_a: Any) -> SoilProfile:
            pytest.fail("DEMO_MODE reached the network")

        with pytest.raises(ProviderUnavailableError, match="DEMO_MODE"):
            provider_cache.cached_soil(LON, LAT, tmp_path, demo_mode=True, fetch=forbidden)

    def test_the_message_says_how_to_fix_it(self, tmp_path: Path) -> None:
        with pytest.raises(ProviderUnavailableError) as exc:
            provider_cache.cached_soil(LON, LAT, tmp_path, demo_mode=True)
        assert "demo-warm" in exc.value.detail

    def test_a_hit_is_served_without_the_network(self, tmp_path: Path) -> None:
        provider_cache.cached_soil(LON, LAT, tmp_path, fetch=lambda a, b: profile())
        got = provider_cache.cached_soil(
            LON, LAT, tmp_path, demo_mode=True, fetch=lambda a, b: pytest.fail("fetched")
        )
        assert got.texture_class == "clay"

    def test_land_cover_is_blocked_too(self, tmp_path: Path) -> None:
        with pytest.raises(ProviderUnavailableError, match="DEMO_MODE"):
            provider_cache.cached_landcover(
                BOUNDS, SHAPE, TRANSFORM, EPSG, tmp_path, demo_mode=True
            )


class TestTheStatsReport:
    def test_it_counts_entries_and_real_bytes(self, tmp_path: Path) -> None:
        """The size must include the .npz, not just its JSON sidecar — counting
        the sidecar alone reported a cached raster as 0 kB."""
        provider_cache.cached_soil(LON, LAT, tmp_path, fetch=lambda a, b: profile())
        provider_cache.cached_landcover(
            BOUNDS, SHAPE, TRANSFORM, EPSG, tmp_path, fetch=lambda *a: cover()
        )
        stats = provider_cache.stats(tmp_path)
        assert stats["soil"]["entries"] == 1
        assert stats["landcover"]["entries"] == 1
        assert stats["landcover"]["bytes"] > 100

    def test_an_empty_store_reports_zeroes(self, tmp_path: Path) -> None:
        stats = provider_cache.stats(tmp_path)
        assert stats["soil"]["entries"] == 0
        assert stats["landcover"]["bytes"] == 0
