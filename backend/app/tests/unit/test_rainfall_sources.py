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
    with_range: bool = False,
) -> dict[str, object]:
    """A NASA POWER response, keyed by YYYYMMDD as the real one is."""
    precip: dict[str, float] = {}
    temp: dict[str, float] = {}
    tmax: dict[str, float] = {}
    tmin: dict[str, float] = {}
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
        if with_range:
            tmax[key], tmin[key] = 34.0, 22.0
        day += dt.timedelta(days=1)
    parameter: dict[str, object] = {"PRECTOTCORR": precip, "T2M": temp}
    if with_range:
        parameter.update({"T2M_MAX": tmax, "T2M_MIN": tmin})
    return {"properties": {"parameter": parameter}}


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


class TestTheEnsembleDeadline:
    """The caller's deadline wins over the slowest source."""

    def sources(self, monkeypatch: pytest.MonkeyPatch, *, slow: tuple[str, ...]):
        import threading

        from app.providers.rainfall import ensemble

        release = threading.Event()

        def fetcher(name: str):
            def fetch(*_a: object, **_k: object):
                if name in slow:
                    release.wait(10.0)
                return stats_for()

            return fetch

        monkeypatch.setattr(
            ensemble,
            "SOURCES",
            tuple((name, fetcher(name)) for name in ("open_meteo_era5_land", "nasa_power")),
        )
        return release

    def test_it_returns_at_the_budget_with_the_source_that_answered(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import time

        from app.providers.rainfall import ensemble

        release = self.sources(monkeypatch, slow=("open_meteo_era5_land",))
        try:
            t = time.perf_counter()
            result = ensemble.fetch_ensemble(81.3, 21.25, years=3, budget_s=0.3)
            elapsed = time.perf_counter() - t
        finally:
            release.set()
        assert elapsed < 2.0, f"it waited for the slow source ({elapsed:.1f}s)"
        assert result.primary_source == "nasa_power"
        assert result.failures == [
            {"source": "open_meteo_era5_land", "reason": "did not answer within the 0.3 s budget"}
        ]

    def test_with_no_source_in_time_it_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.providers.rainfall import ensemble

        release = self.sources(monkeypatch, slow=("open_meteo_era5_land", "nasa_power"))
        try:
            with pytest.raises(ProviderUnavailableError, match="no rainfall source answered"):
                ensemble.fetch_ensemble(81.3, 21.25, years=3, budget_s=0.2)
        finally:
            release.set()


class TestOpenMeteoRemembersARefusal:
    """From the lab network Open-Meteo took ~70 s to send its 429. Asking again on
    every analysis spent the enrichment budget on an answer known in advance."""

    def refuse(
        self, monkeypatch: pytest.MonkeyPatch, detail: str = "HTTP 429 from open-meteo"
    ) -> list[int]:
        from app.providers.rainfall import open_meteo

        calls: list[int] = []

        def get_json(*_a: object, **_k: object) -> None:
            calls.append(1)
            raise ProviderUnavailableError(open_meteo.PROVIDER, detail)

        monkeypatch.setattr(open_meteo, "get_json", get_json)
        return calls

    def fetch(self) -> object:
        from app.providers.rainfall import open_meteo

        return open_meteo.fetch_rainfall(81.3, 21.25, years=3)

    def test_a_rate_limit_is_not_asked_again_at_once(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = self.refuse(monkeypatch)
        with pytest.raises(ProviderUnavailableError, match="HTTP 429"):
            self.fetch()
        with pytest.raises(ProviderUnavailableError, match="asked again after 60 min"):
            self.fetch()
        assert len(calls) == 1

    def test_an_unreachable_service_counts_as_a_refusal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = self.refuse(monkeypatch, "request failed: ConnectTimeout")
        for _ in range(3):
            with pytest.raises(ProviderUnavailableError, match="ConnectTimeout"):
                self.fetch()
        assert len(calls) == 1

    def test_a_bad_answer_is_not_a_refusal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A 400 is about this request, not the service: the next one may be fine."""
        calls = self.refuse(monkeypatch, "HTTP 400 from open-meteo")
        for _ in range(2):
            with pytest.raises(ProviderUnavailableError, match="HTTP 400"):
                self.fetch()
        assert len(calls) == 2

    def test_an_old_refusal_is_rechecked_off_the_request_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The run does not wait for the re-probe; the probe renews the refusal."""
        import time

        from app.providers.rainfall import open_meteo

        calls = self.refuse(monkeypatch)
        open_meteo._refusal = (time.monotonic() - open_meteo.REFUSAL_TTL_S - 1, "HTTP 429 old")
        with pytest.raises(ProviderUnavailableError, match="being asked again in the background"):
            self.fetch()
        assert open_meteo._reprobe is not None
        open_meteo._reprobe.join(5.0)
        assert len(calls) == 1
        assert open_meteo._refusal is not None
        assert open_meteo._refusal[1] == "HTTP 429 from open-meteo"  # renewed

    def test_when_it_answers_again_the_refusal_is_forgotten(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import time

        from app.providers.rainfall import open_meteo

        monkeypatch.setattr(open_meteo, "get_json", lambda *a, **k: {"daily": {}})
        open_meteo._refusal = (time.monotonic() - open_meteo.REFUSAL_TTL_S - 1, "HTTP 429 old")
        with pytest.raises(ProviderUnavailableError, match="in the background"):
            self.fetch()
        assert open_meteo._reprobe is not None
        open_meteo._reprobe.join(5.0)
        assert open_meteo._refusal is None

    def test_the_refusal_is_learnt_from_a_one_day_probe(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The thirty-year request's refusal is slow; the probe's is not, so the
        full request is never made while Open-Meteo is refusing."""
        from app.providers.rainfall import open_meteo

        asked: list[str] = []

        def get_json(*_a: object, params: dict[str, object], **_k: object) -> None:
            one_day = params["start_date"] == params["end_date"]
            asked.append("probe" if one_day else "full")
            raise ProviderUnavailableError(open_meteo.PROVIDER, "HTTP 429 from open-meteo")

        monkeypatch.setattr(open_meteo, "get_json", get_json)
        with pytest.raises(ProviderUnavailableError, match="HTTP 429"):
            self.fetch()
        assert asked == ["probe"]

    def test_with_quota_left_the_full_series_is_fetched(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.providers.rainfall import open_meteo

        rain, dates, _ = series(range(2019, 2024))
        asked: list[str] = []

        def get_json(*_a: object, params: dict[str, object], **_k: object) -> object:
            one_day = params["start_date"] == params["end_date"]
            asked.append("probe" if one_day else "full")
            return {
                "daily": {
                    "time": [d.isoformat() for d in dates],
                    "precipitation_sum": list(rain),
                    "et0_fao_evapotranspiration": [4.0] * len(dates),
                }
            }

        monkeypatch.setattr(open_meteo, "get_json", get_json)
        stats = self.fetch()
        assert asked == ["probe", "full"]
        assert stats.mean_annual_mm > 0  # type: ignore[attr-defined]

    def test_the_cache_is_still_served_while_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.providers.rainfall import open_meteo

        self.refuse(monkeypatch)
        with pytest.raises(ProviderUnavailableError):
            self.fetch()
        cached = stats_for()
        monkeypatch.setattr(open_meteo.cache, "stats_from_cache", lambda *a, **k: cached)
        assert self.fetch() is cached


class TestReferenceEvapotranspirationFromTemperature:
    """Open-Meteo, the source that carries ET0, rate-limits often; without ET0
    the water balance was unavailable on otherwise full-tier runs. NASA POWER's
    daily temperature range gives it by FAO-56's Hargreaves equation."""

    def test_extraterrestrial_radiation_matches_fao56_example_8(self) -> None:
        """3 September at 20 deg S: Ra = 32.2 MJ m-2 day-1 in the worked example."""
        from app.providers.rainfall import et0

        assert et0.extraterrestrial_radiation(-20.0, 246) == pytest.approx(32.2, abs=0.1)

    def test_polar_night_is_zero_not_an_error(self) -> None:
        from app.providers.rainfall import et0

        assert et0.extraterrestrial_radiation(80.0, 355) == pytest.approx(0.0, abs=1e-9)

    def test_hargreaves_by_hand(self) -> None:
        """0.0023 x (28 + 17.8) x sqrt(12) x Ra x 0.408, worked independently."""
        import math

        from app.providers.rainfall import et0

        day = dt.date(2021, 6, 21)
        ra = et0.extraterrestrial_radiation(21.25, day.timetuple().tm_yday)
        expected = 0.0023 * (28.0 + 17.8) * math.sqrt(12.0) * ra * 0.408
        got = et0.hargreaves([day], np.array([34.0]), np.array([22.0]), 21.25)
        assert got[0] == pytest.approx(expected, rel=1e-9)
        assert 4.0 < got[0] < 8.0  # a hot June day in central India

    def test_a_missing_or_inverted_range_is_no_value(self) -> None:
        from app.providers.rainfall import et0

        days = [dt.date(2021, 1, 1), dt.date(2021, 1, 2)]
        got = et0.hargreaves(days, np.array([np.nan, 20.0]), np.array([10.0, 25.0]), 21.25)
        assert np.isnan(got).all()

    def test_gaps_take_the_months_mean(self) -> None:
        from app.providers.rainfall import et0

        days = [dt.date(2021, 1, d) for d in (1, 2, 3)]
        filled, gaps = et0.fill_by_month(days, np.array([3.0, np.nan, 5.0]))
        assert gaps == 1
        assert filled is not None and filled[1] == pytest.approx(4.0)

    def test_a_month_with_no_value_at_all_is_refused(self) -> None:
        from app.providers.rainfall import et0

        days = [dt.date(2021, 1, 1), dt.date(2021, 2, 1)]
        filled, _ = et0.fill_by_month(days, np.array([3.0, np.nan]))
        assert filled is None

    def test_nasa_power_now_supplies_monthly_et0(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(nasa_power, "get_json", lambda *a, **k: power_payload(with_range=True))
        stats = nasa_power.fetch_rainfall(81.3, 21.25, years=3)
        assert stats.et0_monthly_mm is not None
        assert len(stats.et0_monthly_mm) == 12
        assert all(v > 0 for v in stats.et0_monthly_mm)
        # Central India: roughly 1,400-2,000 mm a year by any method.
        assert stats.et0_annual_mm is not None and 1_200 < stats.et0_annual_mm < 2_400

    def test_without_the_range_there_is_still_rainfall(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(nasa_power, "get_json", lambda *a, **k: power_payload())
        stats = nasa_power.fetch_rainfall(81.3, 21.25, years=3)
        assert stats.et0_monthly_mm is None
        assert stats.mean_annual_mm > 0

    def test_it_asks_for_the_temperature_range(self) -> None:
        assert {"T2M_MAX", "T2M_MIN"} <= set(nasa_power.PARAMETERS)
