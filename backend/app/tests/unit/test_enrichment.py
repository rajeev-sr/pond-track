"""Location-derived enrichment and the tier ladder (MC-19).

Providers are stubbed: the point under test is the *degradation behaviour* --
that each layer fails independently and the tier reflects exactly what arrived.
Hitting the real services would make these tests slow, flaky and dependent on
someone else's uptime.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.providers.base import ProviderUnavailableError
from app.providers.elevation.base import Bounds, DemGrid
from app.providers.landcover.worldcover import AVAILABILITY, LandCover
from app.providers.soil.soilgrids import SoilProfile
from app.services import enrichment as enr


def dem() -> DemGrid:
    return DemGrid(
        elevation=np.full((10, 10), 100.0, dtype=np.float32),
        transform=(5.0, 0.0, 500_000.0, 0.0, -5.0, 2_340_050.0),
        epsg=32644,
        cell_size_m=5.0,
    )


def bounds() -> Bounds:
    return Bounds(81.28, 21.24, 81.31, 21.26)


def fake_soil() -> SoilProfile:
    return SoilProfile(
        clay_pct=41.5,
        sand_pct=22.7,
        silt_pct=35.8,
        texture_class="clay",
        hydrologic_soil_group="D",
        lon=81.29,
        lat=21.25,
    )


def fake_cover(codes: np.ndarray | None = None) -> LandCover:
    if codes is None:
        codes = np.full((10, 10), 40, dtype=np.uint8)  # cropland
    return LandCover(
        codes=codes.astype(np.uint8),
        fractions={"cropland": 1.0},
        dominant_class="cropland",
        tiles_used=["fake"],
    )


class _FakeRain:
    """Minimal stand-in: only what Enrichment touches."""

    warnings: list[str] = []
    monsoon_months = [6, 7, 8, 9]
    mean_annual_mm = 1300.0
    monthly_temp_c = None

    def as_dict(self) -> dict[str, object]:
        return {"annual": {"mean_mm": 1300.0}}


class _FakeEnsemble:
    """What `fetch_ensemble` returns: a primary series plus its stablemates.

    Enrichment now fetches an ensemble rather than a single series, so the stub
    has to be one -- and `primary` is the field that decides which daily series
    SCS-CN runs on.
    """

    def __init__(self, primary: object | None = None) -> None:
        self.primary = primary if primary is not None else _FakeRain()
        self.primary_source = "fake"
        self.members = {"fake": self.primary}
        self.failures: list[dict[str, str]] = []

    def as_dict(self) -> dict[str, object]:
        return {"primary_source": self.primary_source, "sources": {}, "failures": []}


def patch(monkeypatch, *, soil=..., cover=..., rain=..., water=None) -> None:  # type: ignore[no-untyped-def]
    """Stub each provider with either a value or an exception to raise.

    `water` stubs the OSM existing-water layer, which is a fourth concurrent job.
    It defaults to an empty mask rather than being left live: unstubbed it
    reaches Overpass, misses the enrichment deadline, and shows up as a spurious
    provider failure in every test that asserts on the failure list.
    """

    def make(value):  # type: ignore[no-untyped-def]
        def fn(*a, **k):  # type: ignore[no-untyped-def]
            if isinstance(value, Exception):
                raise value
            # A callable stub is *called*, so a test can inject slowness or a
            # side effect. Returning it unevaluated made the future complete
            # instantly and silently defeated the deadline test.
            if callable(value):
                return value(*a, **k)
            return value

        return fn

    if soil is not ...:
        monkeypatch.setattr(enr, "fetch_soil_profile", make(soil))
    if cover is not ...:
        monkeypatch.setattr(enr, "fetch_landcover", make(cover))
    if rain is not ...:
        monkeypatch.setattr(enr, "fetch_ensemble", make(rain))

    # The water job reaches OSM through two hops; stub the outer one.
    import numpy as _np

    from app.providers.vector import osm_cache as _osm_cache
    from app.providers.vector.overpass import OsmContext as _OsmContext

    if water is None:
        monkeypatch.setattr(_osm_cache, "fetch_cached", lambda *a, **k: (_OsmContext(), True))
        monkeypatch.setattr(
            "app.services.land.osm_exclusion_mask",
            lambda context, dem, **k: (_np.zeros(dem.shape, dtype=bool), {}),
        )
    else:
        monkeypatch.setattr(_osm_cache, "fetch_cached", make(water))


class TestTierLadder:
    def test_everything_available_is_full(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        patch(monkeypatch, soil=fake_soil(), cover=fake_cover(), rain=_FakeEnsemble())
        e = enr.fetch_enrichment(bounds(), dem())
        assert e.tier == "full"
        assert e.failures == []
        assert "soil_hydrologic_group" in e.layers_used
        assert e.layers_unavailable == []

    def test_rainfall_only_is_no_soil_lulc(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        patch(
            monkeypatch,
            soil=ProviderUnavailableError("soilgrids", "HTTP 503"),
            cover=ProviderUnavailableError("esa_worldcover", "no tile"),
            rain=_FakeEnsemble(),
        )
        e = enr.fetch_enrichment(bounds(), dem())
        assert e.tier == "no_soil_lulc"
        assert {f["layer"] for f in e.failures} == {"soil_hydrologic_group", "land_use_land_cover"}
        assert "rainfall" in e.layers_used

    def test_nothing_available_is_terrain_only(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        boom = ProviderUnavailableError("x", "down")
        patch(monkeypatch, soil=boom, cover=boom, rain=boom)
        e = enr.fetch_enrichment(bounds(), dem())
        assert e.tier == "terrain_only"
        assert len(e.failures) == 3

    def test_soil_and_cover_without_rainfall_is_still_terrain_only(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        """Rainfall is what makes a runoff figure possible, so the tier turns on it."""
        patch(
            monkeypatch,
            soil=fake_soil(),
            cover=fake_cover(),
            rain=ProviderUnavailableError("open_meteo", "timeout"),
        )
        assert enr.fetch_enrichment(bounds(), dem()).tier == "terrain_only"

    def test_disabled_skips_the_network_entirely(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        def explode(*_a, **_k):  # type: ignore[no-untyped-def]
            raise AssertionError("a provider was called with enrichment disabled")

        patch(monkeypatch, soil=explode, cover=explode, rain=explode)
        e = enr.fetch_enrichment(bounds(), dem(), enabled=False)
        assert e.skipped is True
        assert e.tier == "terrain_only"

    def test_an_unexpected_exception_degrades_rather_than_propagating(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        """Enrichment must never be able to fail the whole analysis."""
        patch(
            monkeypatch,
            soil=RuntimeError("something odd"),
            cover=fake_cover(),
            rain=_FakeEnsemble(),
        )
        e = enr.fetch_enrichment(bounds(), dem())
        assert e.tier == "no_soil_lulc"
        assert e.failures[0]["layer"] == "soil_hydrologic_group"

    def test_every_tier_has_a_stated_meaning(self) -> None:
        for tier in ("full", "no_soil_lulc", "terrain_only"):
            assert enr.TIER_MEANING[tier]


class TestSoilFallback:
    def test_measured_group_is_used_and_flagged(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        patch(monkeypatch, soil=fake_soil(), cover=fake_cover(), rain=_FakeEnsemble())
        hsg, measured = enr.fetch_enrichment(bounds(), dem()).hydrologic_soil_group()
        assert (hsg, measured) == ("D", True)

    def test_assumed_group_is_flagged_as_assumed(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        patch(
            monkeypatch,
            soil=ProviderUnavailableError("s", "down"),
            cover=fake_cover(),
            rain=_FakeEnsemble(),
        )
        hsg, measured = enr.fetch_enrichment(bounds(), dem()).hydrologic_soil_group()
        assert hsg == enr.ASSUMED_HSG
        assert measured is False


class TestAvailabilityGrid:
    def test_osm_water_still_protects_when_land_cover_is_down(
        self, monkeypatch
    ) -> None:  # type: ignore[no-untyped-def]
        """This used to assert the grid was `None` without land cover.

        That was the bug: with no availability grid, siting had no water
        information at all, and three of five recommended sites landed inside
        permanent water -- an existing tank maximises both depression depth and
        flow accumulation. OSM water is an independent second source, so one
        provider failing no longer removes the protection.
        """
        patch(
            monkeypatch,
            soil=fake_soil(),
            cover=ProviderUnavailableError("c", "down"),
            rain=_FakeEnsemble(),
        )
        result = enr.fetch_enrichment(bounds(), dem())
        assert result.land_cover is None
        # `availability_grid()` is land-cover-only, so it is None here. The
        # protection moved to the siting veto, which OSM still supplies -- that
        # is the whole point of having two independent sources.
        assert result.availability_grid() is None
        assert result.osm is not None
        assert result.water_exclusion["confidence"] == "partial"
        veto = result.siting_exclusions(dem())
        assert "OpenStreetMap" in veto.sources

    def test_none_only_when_no_water_source_answers(
        self, monkeypatch
    ) -> None:  # type: ignore[no-untyped-def]
        """`None` now means exactly one thing: nothing knows where the water is."""
        patch(
            monkeypatch,
            soil=fake_soil(),
            cover=ProviderUnavailableError("c", "down"),
            rain=_FakeEnsemble(),
            water=ProviderUnavailableError("overpass", "down"),
        )
        result = enr.fetch_enrichment(bounds(), dem())
        assert result.availability_grid() is None
        assert result.osm is None
        assert result.water_exclusion["confidence"] == "none"
        assert "could not be excluded" in result.water_exclusion["note"]
        veto = result.siting_exclusions(dem())
        assert veto.confidence == "terrain-only"
        assert not veto.mask.any(), "nothing known, so nothing vetoed -- and it says so"

    def test_maps_classes_to_buildability(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        codes = np.full((10, 10), 40, dtype=np.uint8)  # cropland
        codes[0, 0] = 50  # built-up
        codes[0, 1] = 80  # open water
        codes[0, 2] = 60  # bare / wasteland
        patch(monkeypatch, soil=fake_soil(), cover=fake_cover(codes), rain=_FakeEnsemble())
        av = enr.fetch_enrichment(bounds(), dem()).availability_grid()
        assert av is not None
        assert av[0, 0] == 0.0, "built-up must be unbuildable"
        assert av[0, 1] == 0.0, "open water must be unbuildable"
        assert av[0, 2] == pytest.approx(AVAILABILITY[60])
        assert av[5, 5] == pytest.approx(AVAILABILITY[40])

    def test_wasteland_outranks_cropland(self) -> None:
        """Bare/sparse land is the class India actually allots for ponds."""
        assert AVAILABILITY[60] > AVAILABILITY[30] > AVAILABILITY[40]

    def test_excluded_classes_all_score_zero(self) -> None:
        for code in enr.EXCLUDED_COVER_CODES:
            assert AVAILABILITY[code] == 0.0


class TestSerialisation:
    def test_reports_what_was_used_and_what_failed(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        patch(
            monkeypatch,
            soil=ProviderUnavailableError("soilgrids", "HTTP 500"),
            cover=fake_cover(),
            rain=_FakeEnsemble(),
        )
        d = enr.fetch_enrichment(bounds(), dem()).as_dict()
        assert d["analysis_tier"] == "no_soil_lulc"
        assert d["soil"] is None
        assert d["land_cover"] is not None
        assert d["provider_failures"][0]["reason"] == "HTTP 500"
        assert d["tier_meaning"]

    def test_serialises(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        import json

        patch(monkeypatch, soil=fake_soil(), cover=fake_cover(), rain=_FakeEnsemble())
        json.dumps(enr.fetch_enrichment(bounds(), dem()).as_dict())


class TestBudget:
    """A slow provider must not be able to dominate the request."""

    def test_a_provider_that_misses_the_budget_is_dropped(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        import time as _time

        def slow(*_a, **_k):  # type: ignore[no-untyped-def]
            _time.sleep(5.0)
            return fake_soil()

        patch(monkeypatch, soil=slow, cover=fake_cover(), rain=_FakeEnsemble())
        t = _time.perf_counter()
        e = enr.fetch_enrichment(bounds(), dem(), budget_s=0.4)
        elapsed = _time.perf_counter() - t

        assert elapsed < 3.0, f"the deadline did not bound the wait ({elapsed:.1f}s)"
        assert e.tier == "no_soil_lulc"
        assert e.soil is None
        reason = next(f["reason"] for f in e.failures if f["layer"] == "soil_hydrologic_group")
        assert "budget" in reason

    def test_the_budget_is_reported(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        patch(monkeypatch, soil=fake_soil(), cover=fake_cover(), rain=_FakeEnsemble())
        d = enr.fetch_enrichment(bounds(), dem(), budget_s=17.0).as_dict()
        assert d["enrichment_budget_s"] == 17.0

    def test_fast_providers_still_land_within_a_tight_budget(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        patch(monkeypatch, soil=fake_soil(), cover=fake_cover(), rain=_FakeEnsemble())
        assert enr.fetch_enrichment(bounds(), dem(), budget_s=5.0).tier == "full"
