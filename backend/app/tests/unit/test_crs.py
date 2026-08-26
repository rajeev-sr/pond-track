"""CRS helpers and the metric-operation guard.

Covers HLD ADR-5 / CH-10: computing area in degrees is silent and catastrophic,
so the guard that prevents it needs real tests.
"""

from __future__ import annotations

import pytest

from app.core.crs import (
    INDIA_UTM_ZONES,
    CRSError,
    CRSGuard,
    is_within_india,
    utm_epsg_for,
    utm_zone_for,
)


class TestUtmZone:
    @pytest.mark.parametrize(
        ("lon", "zone"),
        [
            (68.0, 42),  # Gujarat / west Rajasthan - India's western edge
            (71.99, 42),  # just inside zone 42
            (72.0, 43),  # zone 42|43 boundary
            (77.4126, 43),  # Bhopal
            (77.99, 43),
            (78.0, 44),  # zone 43|44 boundary
            (88.36, 45),  # Kolkata
            (90.0, 46),
            (96.0, 47),
            (97.4, 47),  # Arunachal - India's eastern edge
        ],
    )
    def test_india_longitudes(self, lon: float, zone: int) -> None:
        assert utm_zone_for(lon) == zone

    def test_every_india_zone_is_documented(self) -> None:
        # Any zone the arithmetic can produce for India must appear in the table
        # used by the docs and by error messages.
        produced = {utm_zone_for(lon) for lon in (68.0, 73.0, 79.0, 85.0, 91.0, 97.0)}
        assert produced == set(INDIA_UTM_ZONES)

    @pytest.mark.parametrize(("lon", "zone"), [(-180.0, 1), (-177.0, 1), (0.0, 31), (180.0, 1)])
    def test_global_edges(self, lon: float, zone: int) -> None:
        # lon == 180 must wrap to zone 1, not overflow to a non-existent zone 61.
        assert utm_zone_for(lon) == zone

    def test_all_longitudes_produce_a_valid_zone(self) -> None:
        for i in range(-1800, 1801):
            assert 1 <= utm_zone_for(i / 10.0) <= 60

    @pytest.mark.parametrize("lon", [-180.001, 180.001, 1e9, float("nan"), float("inf")])
    def test_rejects_bad_longitude(self, lon: float) -> None:
        with pytest.raises(CRSError):
            utm_zone_for(lon)


class TestUtmEpsg:
    def test_bhopal_is_utm_43n(self) -> None:
        # The worked example in HLD 6.9 sits in Sehore district, MP.
        assert utm_epsg_for(77.4126, 23.2599) == 32643

    @pytest.mark.parametrize(
        ("lon", "lat", "epsg"),
        [
            (72.8777, 19.0760, 32643),  # Mumbai
            (80.2707, 13.0827, 32644),  # Chennai
            (88.3639, 22.5726, 32645),  # Kolkata
            (91.7362, 26.1445, 32646),  # Guwahati
            (69.6293, 22.4707, 32642),  # Jamnagar
        ],
    )
    def test_indian_cities(self, lon: float, lat: float, epsg: int) -> None:
        assert utm_epsg_for(lon, lat) == epsg

    def test_southern_hemisphere_uses_327xx(self) -> None:
        assert utm_epsg_for(77.4, -23.0) == 32743

    def test_equator_counts_as_north(self) -> None:
        assert utm_epsg_for(77.4, 0.0) == 32643

    @pytest.mark.parametrize("lat", [-90.1, 90.1, float("nan")])
    def test_rejects_bad_latitude(self, lat: float) -> None:
        with pytest.raises(CRSError):
            utm_epsg_for(77.4, lat)


class TestIsWithinIndia:
    @pytest.mark.parametrize(
        ("lon", "lat"), [(77.4, 23.2), (68.2, 23.7), (97.3, 27.0), (72.8, 8.3)]
    )
    def test_inside(self, lon: float, lat: float) -> None:
        assert is_within_india(lon, lat)

    @pytest.mark.parametrize(
        ("lon", "lat"), [(0.0, 0.0), (-74.0, 40.7), (100.5, 13.7), (77.4, 45.0)]
    )
    def test_outside(self, lon: float, lat: float) -> None:
        assert not is_within_india(lon, lat)


class TestCRSGuard:
    def test_allows_projected(self, projected_crs: object) -> None:
        CRSGuard.require_projected(projected_crs, "area calculation")

    def test_blocks_geographic(self, geographic_crs: object) -> None:
        with pytest.raises(CRSError, match="geographic CRS"):
            CRSGuard.require_projected(geographic_crs, "area calculation")

    def test_error_names_the_operation_and_the_fix(self, geographic_crs: object) -> None:
        # The message has to be actionable: a bare "invalid CRS" would send the
        # reader hunting. It must say what failed and what to do.
        with pytest.raises(CRSError) as exc:
            CRSGuard.require_projected(geographic_crs, "catchment area")
        msg = str(exc.value)
        assert "catchment area" in msg
        assert "degrees" in msg
        assert "utm_epsg_for" in msg

    def test_blocks_none(self) -> None:
        with pytest.raises(CRSError, match="requires a projected CRS"):
            CRSGuard.require_projected(None, "volume calculation")

    def test_require_geographic_blocks_projected(self, projected_crs: object) -> None:
        with pytest.raises(CRSError, match="EPSG:4326"):
            CRSGuard.require_geographic(projected_crs, "GeoJSON response")

    def test_require_geographic_allows_geographic(self, geographic_crs: object) -> None:
        CRSGuard.require_geographic(geographic_crs, "GeoJSON response")


class TestCRSGuardWithRealPyproj:
    """The stubs above prove the logic; these prove it works on real CRS objects."""

    def test_epsg_int_and_string_forms(self) -> None:
        pyproj = pytest.importorskip("pyproj")
        assert pyproj  # silence linters
        CRSGuard.require_projected(32643, "area")
        CRSGuard.require_projected("EPSG:32643", "area")
        with pytest.raises(CRSError):
            CRSGuard.require_projected(4326, "area")
        with pytest.raises(CRSError):
            CRSGuard.require_projected("EPSG:4326", "area")

    def test_pyproj_crs_object(self) -> None:
        pyproj = pytest.importorskip("pyproj")
        CRSGuard.require_projected(pyproj.CRS.from_epsg(32643), "area")
        with pytest.raises(CRSError):
            CRSGuard.require_projected(pyproj.CRS.from_epsg(4326), "area")

    def test_every_india_utm_epsg_is_projected(self) -> None:
        pytest.importorskip("pyproj")
        for zone in INDIA_UTM_ZONES:
            CRSGuard.require_projected(32600 + zone, f"area in zone {zone}")

    def test_unrecognised_crs_raises_crserror(self) -> None:
        pytest.importorskip("pyproj")
        with pytest.raises(CRSError, match="unrecognised CRS"):
            CRSGuard.require_projected("not-a-crs", "area")
