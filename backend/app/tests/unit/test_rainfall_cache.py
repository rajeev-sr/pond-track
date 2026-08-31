"""The rainfall cache, and the quantisation that makes it work (M4-5).

The cell arithmetic is pure and is tested offline. The database round-trip needs
PostGIS and lives in `app/tests/integration/test_rainfall_cache_db.py`.

Why this matters more than a typical cache: keying on the coordinate that was
*asked for* rather than the source's grid cell does not make the cache slower, it
makes it useless. ERA5-Land is a 0.1-degree reanalysis, so every point inside one
cell receives the same series -- two clicks 200 m apart would each store 11,000
rows of identical data and neither would ever hit the other.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pytest

from app.providers.rainfall.cache import (
    DEFAULT_RESOLUTION,
    GRID_RESOLUTION,
    MIN_COVERAGE,
    cell_for,
)


class TestCellQuantisation:
    def test_nearby_points_share_a_cell(self) -> None:
        """The point of the whole thing. Two clicks a few hundred metres apart
        must resolve to one key, or the cache never hits."""
        first = cell_for("open_meteo_era5_land", 81.297, 21.2517)
        second = cell_for("open_meteo_era5_land", 81.300, 21.2530)
        assert first.key == second.key

    def test_distant_points_do_not(self) -> None:
        """Over-merging would serve one district's rainfall for another's."""
        first = cell_for("open_meteo_era5_land", 81.30, 21.25)
        second = cell_for("open_meteo_era5_land", 81.90, 21.85)
        assert first.key != second.key

    def test_each_source_is_quantised_to_its_own_grid(self) -> None:
        """ERA5-Land is 0.1 degrees, POWER is 0.5 x 0.625. Using one figure for
        both would either split cells that share a series or merge cells that do
        not."""
        fine = cell_for("open_meteo_era5_land", 81.297, 21.2517)
        coarse = cell_for("nasa_power", 81.297, 21.2517)
        assert fine.key != coarse.key
        assert coarse.offset_km > fine.offset_km

    def test_powers_grid_is_asymmetric(self) -> None:
        """MERRA-2 is 0.5 degrees north-south and 0.625 east-west; treating them
        as equal would misplace the cell centre."""
        lat_step, lon_step = GRID_RESOLUTION["nasa_power"]
        assert lat_step != lon_step
        assert (lat_step, lon_step) == (0.5, 0.625)

    def test_the_key_is_the_cell_centre_not_the_query(self) -> None:
        cell = cell_for("nasa_power", 81.297, 21.2517)
        assert cell.key == f"{cell.lat:.4f},{cell.lon:.4f}"
        assert (cell.lat, cell.lon) != (21.2517, 81.297)

    def test_the_offset_is_reported_so_it_can_be_disclosed(self) -> None:
        """At POWER's resolution a cached series can describe a point 30 km away,
        which a reader should be told rather than left to infer."""
        cell = cell_for("nasa_power", 81.297, 21.2517)
        assert cell.offset_km > 0
        # Bounded by half a cell diagonal: 0.25 deg lat and 0.3125 deg lon.
        assert cell.offset_km < 50

    def test_a_point_on_a_cell_centre_has_no_offset(self) -> None:
        exact = cell_for("open_meteo_era5_land", 81.3, 21.3)
        assert exact.offset_km == pytest.approx(0.0, abs=0.01)

    def test_an_unknown_source_falls_back_to_the_coarsest_grid(self) -> None:
        """Over-coarse is the safe direction: it serves a slightly displaced
        series -- and says how displaced -- rather than never hitting."""
        cell = cell_for("some_new_provider", 81.297, 21.2517)
        assert cell.key == cell_for("nasa_power", 81.297, 21.2517).key
        assert GRID_RESOLUTION["nasa_power"] == DEFAULT_RESOLUTION

    @pytest.mark.parametrize(
        ("lon", "lat"), [(0.0, 0.0), (-179.9, -89.9), (179.9, 89.9), (81.25, 21.5)]
    )
    def test_it_holds_anywhere_on_the_globe(self, lon: float, lat: float) -> None:
        cell = cell_for("open_meteo_era5_land", lon, lat)
        assert cell.key
        assert cell.offset_km >= 0
        assert np.isfinite(cell.lat) and np.isfinite(cell.lon)

    def test_quantisation_is_idempotent(self) -> None:
        """Re-quantising a cell centre must give the same cell, or a cached
        series could be filed under a second key on the next lookup."""
        cell = cell_for("nasa_power", 81.297, 21.2517)
        again = cell_for("nasa_power", cell.lon, cell.lat)
        assert again.key == cell.key


class TestCoverageThreshold:
    def test_it_is_high_but_not_total(self) -> None:
        """Not 100 %: a source can legitimately lack days -- POWER's fill values,
        a reanalysis gap -- so demanding every date would never hit the cache for
        a series that will never be complete. Not much lower either: the annual
        totals and the CV are derived from these days.
        """
        assert 0.9 <= MIN_COVERAGE < 1.0


class TestBestEffortBehaviour:
    """The contour endpoints work with no database at all -- a documented
    decision. A cache that could fail an analysis would be worse than no cache.

    The unreachable database is *arranged*, not assumed. These tests used to rely
    on the host being unable to resolve the `postgis` hostname, which made them
    pass on a laptop and fail inside the API container -- where the database is
    right there, so the write succeeded and returned 1 row.
    """

    @pytest.fixture
    def no_database(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Make the database genuinely unreachable.

        Patched at `app.db.session.get_sessionmaker`, which is the seam the cache
        actually reaches through -- it imports it inside the function, so
        patching the cache module itself would miss.
        """
        from sqlalchemy.exc import OperationalError

        from app.db import session as db_session

        def unreachable(*_args: object, **_kwargs: object) -> None:
            raise OperationalError("connect", {}, Exception("no database here"))

        monkeypatch.setattr(db_session, "get_sessionmaker", unreachable)

    def test_a_read_without_a_database_is_a_miss_not_an_error(self, no_database: None) -> None:
        from app.providers.rainfall import cache

        assert (
            cache.read(
                "open_meteo_era5_land",
                81.3,
                21.25,
                dt.date(2020, 1, 1),
                dt.date(2020, 12, 31),
            )
            is None
        )

    def test_a_write_without_a_database_reports_zero_rows(self, no_database: None) -> None:
        from app.providers.rainfall import cache

        written = cache.write(
            "open_meteo_era5_land",
            81.3,
            21.25,
            dates=[dt.date(2020, 1, 1)],
            precipitation_mm=np.array([5.0]),
        )
        assert written == 0

    def test_writing_nothing_is_not_an_error(self) -> None:
        from app.providers.rainfall import cache

        assert (
            cache.write(
                "open_meteo_era5_land",
                81.3,
                21.25,
                dates=[],
                precipitation_mm=np.array([]),
            )
            == 0
        )
