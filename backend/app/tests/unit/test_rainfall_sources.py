"""NASA POWER, the shared statistics, and the two-source ensemble (M4-1, M4-2).

Offline against fixtures. The response quirks tested here are the ones that would
otherwise pass silently:

* POWER reports missing values as **-999.0**, not null. Summed naively that is not
  a gap in the record, it is a year with minus three hundred metres of rainfall.
* Its values are keyed by `YYYYMMDD` in an object, not parallel to a time array,
  so a missing day is an absent key rather than a hole to line up.
* A missing *temperature* must not become 0 degrees: that would halve Khosla's
  loss term for the month and look entirely reasonable.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pytest

from app.providers.base import Provenance, ProviderUnavailableError
from app.providers.rainfall import nasa_power
from app.providers.rainfall.base import (
    MIN_DAYS_IN_COMPLETE_YEAR,
    RAINY_DAY_THRESHOLD_MM,
    build_stats,
    dependable_rainfall,
)

PROVENANCE = Provenance(provider="test", dataset="fixture", resolution="n/a", licence="n/a")


def series(
    years: range, *, monsoon_mm: float = 300.0, temp_c: float | None = 28.0
) -> tuple[np.ndarray, list[dt.date], np.ndarray | None]:
    """A monsoon-shaped daily series over whole calendar years."""
    dates: list[dt.date] = []
    rain: list[float] = []
    for year in years:
        day = dt.date(year, 1, 1)
        while day.year == year:
            wet = day.month in (6, 7, 8, 9) and day.day % 3 == 0
            rain.append(monsoon_mm / 10.0 if wet else 0.0)
            dates.append(day)
            day += dt.timedelta(days=1)
    temps = None if temp_c is None else np.full(len(rain), temp_c)
    return np.array(rain), dates, temps


def stats_for(*args: object, **kwargs: object):
    rain, dates, temps = series(range(2019, 2024))
    payload = {
        "daily_mm": rain,
        "dates": dates,
        "lon": 81.3,
        "lat": 21.25,
        "model_used": "fixture",
        "provenance": PROVENANCE,
        "data_caveat": "",
        "temp_daily_c": temps,
    }
    payload.update(kwargs)  # type: ignore[arg-type]
    return build_stats("test", **payload)  # type: ignore[arg-type]


class TestSharedStatistics:
    def test_it_derives_complete_years_only(self) -> None:
        """A part-year is not a dry year. Averaging one in understates the mean
        and inflates the CV, which propagates into the dependable rainfall the
        pond is sized on."""
        rain, dates, _ = series(range(2019, 2023))
        # Append a stub of 2023 -- 40 days, far below a complete year.
        extra = [dt.date(2023, 1, 1) + dt.timedelta(days=i) for i in range(40)]
        stats = build_stats(
            "test",
            daily_mm=np.concatenate([rain, np.zeros(40)]),
            dates=dates + extra,
            lon=81.3,
            lat=21.25,
            model_used="fixture",
            provenance=PROVENANCE,
            data_caveat="",
        )
        assert 2023 not in stats.years
        assert any("2023" in w for w in stats.warnings)

    def test_the_threshold_for_a_complete_year_is_stated(self) -> None:
        assert 300 < MIN_DAYS_IN_COMPLETE_YEAR <= 366

    def test_a_rainy_day_uses_the_imd_threshold(self) -> None:
        assert RAINY_DAY_THRESHOLD_MM == 2.5

    def test_monthly_temperature_is_a_mean_not_a_sum(self) -> None:
        """Summing it would report a January of several hundred degrees."""
        stats = stats_for()
        assert stats.monthly_temp_c is not None
        assert all(20.0 < value < 40.0 for value in stats.monthly_temp_c)

    def test_a_gap_in_the_temperature_record_does_not_poison_the_month(self) -> None:
        """One NaN day would make the month's mean NaN, then Khosla's loss term
        NaN, then the runoff NaN -- three steps from where the gap was."""
        rain, dates, temps = series(range(2019, 2024))
        assert temps is not None
        temps = temps.copy()
        temps[5:20] = np.nan
        stats = stats_for(temp_daily_c=temps)
        assert stats.monthly_temp_c is not None
        assert np.isfinite(stats.monthly_temp_c[0]), "January went NaN over a 15-day gap"

    def test_without_temperature_the_field_is_none(self) -> None:
        """Which is what makes Khosla's cross-check say what it needs."""
        assert stats_for(temp_daily_c=None).monthly_temp_c is None

    def test_a_series_shorter_than_a_year_is_refused(self) -> None:
        with pytest.raises(ProviderUnavailableError, match="at least a year"):
            build_stats(
                "test",
                daily_mm=np.zeros(100),
                dates=[dt.date(2023, 1, 1) + dt.timedelta(days=i) for i in range(100)],
                lon=81.3,
                lat=21.25,
                model_used="fixture",
                provenance=PROVENANCE,
                data_caveat="",
            )

    def test_dependable_rainfall_orders_correctly(self) -> None:
        """A higher dependability is a lower rainfall: it is the amount you can
        rely on more often, not more of it."""
        stats = stats_for()
        assert stats.dependable_50_mm >= stats.dependable_75_mm >= stats.dependable_90_mm

    def test_weibull_plotting_positions(self) -> None:
        """n/(N+1) -- the 50 % dependable of five ranked totals is the median."""
        totals = [800.0, 900.0, 1000.0, 1100.0, 1200.0]
        assert dependable_rainfall(totals, 0.50) == pytest.approx(1000.0, rel=0.05)


def power_payload(
    *,
    days: int = 800,
    fill_every: int | None = None,
    missing_temp_every: int | None = None,
    drop_temp_keys: bool = False,
) -> dict[str, object]:
    """A NASA POWER response, keyed by YYYYMMDD as the real one is."""
    precip: dict[str, float] = {}
    temp: dict[str, float] = {}
    day = dt.date(2021, 1, 1)
    for index in range(days):
        key = day.strftime("%Y%m%d")
        wet = day.month in (6, 7, 8, 9) and day.day % 3 == 0
        precip[key] = (
            nasa_power.FILL_VALUE
            if (fill_every and index % fill_every == 0)
            else (30.0 if wet else 0.0)
        )
        if not drop_temp_keys:
            temp[key] = (
                nasa_power.FILL_VALUE
                if (missing_temp_every and index % missing_temp_every == 0)
                else 28.0
            )
        day += dt.timedelta(days=1)
    return {"properties": {"parameter": {"PRECTOTCORR": precip, "T2M": temp}}}


class TestNasaPower:
    def call(self, monkeypatch: pytest.MonkeyPatch, payload: dict[str, object]):
        monkeypatch.setattr(nasa_power, "get_json", lambda *a, **k: payload)
        return nasa_power.fetch_rainfall(81.3, 21.25, years=3)

    def test_it_reads_a_date_keyed_response(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stats = self.call(monkeypatch, power_payload())
        assert len(stats.dates) == 800
        assert stats.dates == sorted(stats.dates), "the series must be in date order"

    def test_the_fill_value_is_not_treated_as_rainfall(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """-999 summed over a year is minus three hundred metres of rain."""
        stats = self.call(monkeypatch, power_payload(fill_every=50))
        assert stats.mean_annual_mm > 0
        assert stats.daily_mm.min() >= 0.0
        assert any("fill value" in w for w in stats.warnings)

    def test_a_mostly_missing_series_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with pytest.raises(ProviderUnavailableError, match="fill values"):
            self.call(monkeypatch, power_payload(fill_every=2))

    def test_a_missing_temperature_does_not_become_zero_degrees(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """0 C is a plausible-looking value that would halve Khosla's loss term
        for the month. It is carried as NaN and excluded from the mean instead."""
        stats = self.call(monkeypatch, power_payload(missing_temp_every=40))
        assert stats.monthly_temp_c is not None
        assert all(25.0 < value < 31.0 for value in stats.monthly_temp_c)
        assert any("temperature" in w for w in stats.warnings)

    def test_it_supplies_the_temperature_khosla_needs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stats = self.call(monkeypatch, power_payload())
        assert stats.monthly_temp_c is not None
        assert len(stats.monthly_temp_c) == 12

    def test_an_unexpected_response_shape_is_named(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with pytest.raises(ProviderUnavailableError, match="unexpected response"):
            self.call(monkeypatch, {"properties": {}})

    def test_an_empty_response_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with pytest.raises(ProviderUnavailableError):
            self.call(monkeypatch, {"properties": {"parameter": {"PRECTOTCORR": {}, "T2M": {}}}})

    def test_the_provenance_states_the_coarse_resolution(self) -> None:
        """One POWER cell can span several districts, and a reader should know."""
        assert "0.5" in nasa_power.PROVENANCE.resolution
        assert "district" in nasa_power.DATA_CAVEAT


class TestTheEnsemble:
    def build(self, members: dict[str, object], failures: list[dict[str, str]] | None = None):
        from app.providers.rainfall.ensemble import RainfallEnsemble

        primary = next(iter(members))
        return RainfallEnsemble(
            primary=members[primary],  # type: ignore[arg-type]
            primary_source=primary,
            members=members,  # type: ignore[arg-type]
            failures=failures or [],
        )

    def two_sources(self, first_mm: float, second_mm: float):
        return {
            "open_meteo_era5_land": stats_for(
                daily_mm=series(range(2019, 2024), monsoon_mm=first_mm)[0]
            ),
            "nasa_power": stats_for(daily_mm=series(range(2019, 2024), monsoon_mm=second_mm)[0]),
        }

    def test_it_reports_every_source_separately(self) -> None:
        report = self.build(self.two_sources(300.0, 345.0)).as_dict()
        assert set(report["sources"]) == {"open_meteo_era5_land", "nasa_power"}

    def test_it_reports_the_spread_as_uncertainty(self) -> None:
        report = self.build(self.two_sources(300.0, 345.0)).as_dict()
        assert report["inter_source_spread_mm"] > 0
        assert report["inter_source_sigma_mm"] > 0
        assert report["ensemble_median_annual_mm"] > 0

    def test_close_agreement_is_reported_as_close(self) -> None:
        report = self.build(self.two_sources(300.0, 310.0)).as_dict()
        assert report["notable_disagreement"] is False
        assert "agree" in report["interpretation"]

    def test_a_wide_disagreement_says_not_to_quote_one_figure(self) -> None:
        report = self.build(self.two_sources(200.0, 500.0)).as_dict()
        assert report["notable_disagreement"] is True
        assert "without the range" in report["interpretation"]

    def test_one_source_alone_says_it_is_uncorroborated(self) -> None:
        """Rather than implying a corroboration that did not happen."""
        report = self.build({"nasa_power": stats_for()}).as_dict()
        assert report["agreement"] is None
        assert "no independent corroboration" in report["interpretation"]

    def test_a_failure_is_recorded_not_hidden(self) -> None:
        report = self.build(
            {"nasa_power": stats_for()},
            failures=[{"source": "open_meteo_era5_land", "reason": "HTTP 429"}],
        ).as_dict()
        assert report["failures"][0]["source"] == "open_meteo_era5_land"

    def test_it_explains_why_the_daily_series_is_not_blended(self) -> None:
        """The most important thing in this module. SCS-CN is non-linear in daily
        depth, and two reanalyses put the same storm on different days -- so
        averaging them turns one 100 mm storm into two 50 mm ones and
        systematically understates runoff.
        """
        report = self.build(self.two_sources(300.0, 345.0)).as_dict()
        assert "understate runoff" in report["primary_reason"]

    def test_temperature_comes_from_whichever_source_has_it(self) -> None:
        from app.providers.rainfall.ensemble import temperature_from

        members = {
            "open_meteo_era5_land": stats_for(temp_daily_c=None),
            "nasa_power": stats_for(),
        }
        found = temperature_from(self.build(members))
        assert found is not None and len(found) == 12

    def test_with_no_temperature_anywhere_it_returns_none(self) -> None:
        from app.providers.rainfall.ensemble import temperature_from

        assert temperature_from(self.build({"a": stats_for(temp_daily_c=None)})) is None
