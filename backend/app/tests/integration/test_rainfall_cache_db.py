"""The rainfall cache against a real database (M4-5).

The quantisation is covered offline in `unit/test_rainfall_cache.py`. What needs
PostGIS is the round-trip and the constraint: without the unique index on
`(source, cell_key, observed_on)` a re-fetch appends a second copy of every day,
and reads start double-counting rainfall -- which would inflate every runoff
figure derived from the cache while looking entirely normal.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterator

import numpy as np
import pytest
from sqlalchemy import text

from app.providers.rainfall import cache

pytestmark = pytest.mark.integration

START = dt.date(2020, 1, 1)
DAYS = 400


@pytest.fixture
def scratch_source() -> Iterator[str]:
    """A source name unique to this test, removed afterwards.

    Named rather than shared so a run cannot collide with a real seeded cache or
    with a parallel run of itself.
    """
    from sqlalchemy.exc import OperationalError

    from app.db.session import get_sessionmaker

    name = f"test-{uuid.uuid4().hex[:12]}"
    try:
        with get_sessionmaker()() as session:
            session.execute(text("SELECT 1"))
    except OperationalError as exc:
        pytest.skip(f"no database reachable: {exc.__class__.__name__}")

    yield name

    with get_sessionmaker()() as session:
        session.execute(text("DELETE FROM rainfall_cache WHERE source = :src"), {"src": name})
        session.commit()


def series(days: int = DAYS) -> tuple[list[dt.date], np.ndarray, np.ndarray]:
    dates = [START + dt.timedelta(days=i) for i in range(days)]
    rain = np.array([12.0 if day.month in (6, 7, 8, 9) else 0.0 for day in dates])
    temps = np.array([28.0] * days)
    return dates, rain, temps


class TestTheRoundTrip:
    def test_what_is_written_comes_back(self, scratch_source: str) -> None:
        dates, rain, temps = series()
        written = cache.write(
            scratch_source,
            81.297,
            21.2517,
            dates=dates,
            precipitation_mm=rain,
            temperature_c=temps,
        )
        assert written == DAYS

        read = cache.read(scratch_source, 81.297, 21.2517, dates[0], dates[-1])
        assert read is not None
        assert len(read.dates) == DAYS
        assert read.precipitation_mm.sum() == pytest.approx(rain.sum())
        assert read.temperature_c is not None
        assert read.coverage == pytest.approx(1.0)

    def test_a_nearby_point_reads_the_same_series(self, scratch_source: str) -> None:
        """The design: the key is the grid cell, not the query point."""
        dates, rain, _ = series()
        cache.write(scratch_source, 81.297, 21.2517, dates=dates, precipitation_mm=rain)
        # 300 m away, comfortably inside any of these grids.
        read = cache.read(scratch_source, 81.300, 21.2540, dates[0], dates[-1])
        assert read is not None
        assert read.precipitation_mm.sum() == pytest.approx(rain.sum())

    def test_writing_twice_does_not_double_count(self, scratch_source: str) -> None:
        """The unique constraint earning its place. Two copies of every day would
        double the annual rainfall and every runoff figure with it."""
        dates, rain, _ = series()
        cache.write(scratch_source, 81.297, 21.2517, dates=dates, precipitation_mm=rain)
        cache.write(scratch_source, 81.297, 21.2517, dates=dates, precipitation_mm=rain)

        read = cache.read(scratch_source, 81.297, 21.2517, dates[0], dates[-1])
        assert read is not None
        assert len(read.dates) == DAYS, "the second write appended instead of updating"
        assert read.precipitation_mm.sum() == pytest.approx(rain.sum())

    def test_a_re_write_updates_the_values(self, scratch_source: str) -> None:
        """A reanalysis revision should replace what is stored, not sit beside it."""
        dates, rain, _ = series()
        cache.write(scratch_source, 81.297, 21.2517, dates=dates, precipitation_mm=rain)
        cache.write(scratch_source, 81.297, 21.2517, dates=dates, precipitation_mm=rain * 2.0)
        read = cache.read(scratch_source, 81.297, 21.2517, dates[0], dates[-1])
        assert read is not None
        assert read.precipitation_mm.sum() == pytest.approx(rain.sum() * 2.0)

    def test_extras_from_different_sources_do_not_erase_each_other(
        self, scratch_source: str
    ) -> None:
        """POWER carries temperature and no ET0; Open-Meteo the reverse. A second
        write must not null the first's contribution for the same cell and day.
        """
        dates, rain, temps = series(30)
        cache.write(
            scratch_source,
            81.297,
            21.2517,
            dates=dates,
            precipitation_mm=rain,
            temperature_c=temps,
        )
        cache.write(
            scratch_source,
            81.297,
            21.2517,
            dates=dates,
            precipitation_mm=rain,
            et0_mm=np.array([4.0] * 30),
        )
        read = cache.read(scratch_source, 81.297, 21.2517, dates[0], dates[-1])
        assert read is not None
        assert read.temperature_c is not None, "the temperature was erased"
        assert read.et0_mm is not None, "the ET0 was not stored"


class TestCoverage:
    def test_a_sparse_cache_is_a_miss(self, scratch_source: str) -> None:
        """Returning a partial series would quietly change the annual totals and
        the coefficient of variation the pond is sized on."""
        dates, rain, _ = series()
        keep = slice(0, DAYS // 2)
        cache.write(
            scratch_source,
            81.297,
            21.2517,
            dates=dates[keep],
            precipitation_mm=rain[keep],
        )
        assert cache.read(scratch_source, 81.297, 21.2517, dates[0], dates[-1]) is None

    def test_but_it_still_serves_the_range_it_does_cover(self, scratch_source: str) -> None:
        """A partial write is not wasted: the covered range is a hit."""
        dates, rain, _ = series()
        keep = slice(0, DAYS // 2)
        cache.write(
            scratch_source,
            81.297,
            21.2517,
            dates=dates[keep],
            precipitation_mm=rain[keep],
        )
        read = cache.read(scratch_source, 81.297, 21.2517, dates[0], dates[DAYS // 2 - 1])
        assert read is not None
        assert read.coverage >= cache.MIN_COVERAGE

    def test_an_empty_cell_is_a_miss(self, scratch_source: str) -> None:
        assert cache.read(scratch_source, 1.0, 1.0, START, START + dt.timedelta(days=400)) is None
