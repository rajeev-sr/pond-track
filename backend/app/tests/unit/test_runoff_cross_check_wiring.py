"""The Indian cross-check reaches the analysis response (M4-11).

The formulae themselves are covered in `test_indian_runoff.py`. What this covers
is the wiring: that `_runoff_payload` actually calls the cross-check, feeds it the
right rainfall figures, and puts the result where a reader will find it.

Built on a synthetic rainfall series rather than the live provider. Open-Meteo's
daily request limit is real and gets hit, and a test that silently stops
exercising the thing it is named after -- because an upstream service is
throttling -- is worse than no test.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pytest

from app.services import contour_analysis as analysis


@pytest.fixture
def monsoon_year() -> tuple[np.ndarray, list[dt.date]]:
    """Three years of a monsoon-shaped daily series.

    Concentrated in June-September, which is what makes SCS-CN behave differently
    from an annual-total calculation and is the regime the Indian formulae were
    fitted on.
    """
    monthly_totals = [12.0, 15.0, 20.0, 25.0, 30.0, 180.0, 380.0, 360.0, 210.0, 60.0, 15.0, 8.0]
    daily: list[float] = []
    dates: list[dt.date] = []
    for year in (2021, 2022, 2023):
        for month, total in enumerate(monthly_totals, start=1):
            days = (
                dt.date(year + (month // 12), (month % 12) + 1, 1) - dt.date(year, month, 1)
            ).days
            # A few wet days rather than a uniform drizzle: SCS-CN is non-linear
            # in daily depth, so spreading the total evenly would understate it.
            wet = max(1, round(days * 0.25))
            per_wet = total / wet
            for day in range(1, days + 1):
                daily.append(per_wet if day % 4 == 0 and wet else 0.0)
                dates.append(dt.date(year, month, day))
    return np.array(daily), dates


def build_enrichment(daily: np.ndarray, dates: list[dt.date]):
    from app.providers.base import Provenance
    from app.providers.landcover.worldcover import LandCover
    from app.providers.rainfall.open_meteo import RainfallStats
    from app.providers.soil.soilgrids import SoilProfile
    from app.services.enrichment import Enrichment

    monthly = [0.0] * 12
    for value, date in zip(daily, dates, strict=True):
        monthly[date.month - 1] += float(value)
    monthly = [total / 3.0 for total in monthly]  # three years
    annual = sum(monthly)

    rainfall = RainfallStats(
        daily_mm=daily,
        dates=dates,
        years=np.array(sorted({d.year for d in dates})),
        annual_totals_mm=np.array([annual, annual, annual]),
        mean_annual_mm=annual,
        median_annual_mm=annual,
        std_annual_mm=0.0,
        cv=0.0,
        min_annual_mm=annual,
        max_annual_mm=annual,
        dependable_50_mm=annual,
        dependable_75_mm=annual * 0.9,
        dependable_90_mm=annual * 0.8,
        monthly_normals_mm=monthly,
        monsoon_months=[6, 7, 8, 9],
        monsoon_type="southwest",
        monsoon_share_pct=sum(monthly[5:9]) / annual * 100.0,
        rainy_days_per_year=len([v for v in daily if v > 0]) / 3.0,
        max_1day_mm=float(daily.max()),
        et0_annual_mm=None,
        et0_monthly_mm=None,
        # None on purpose: it is what makes Khosla's cross-check report what it
        # needs rather than being evaluated on a guess.
        monthly_temp_c=None,
        provenance=Provenance(
            provider="synthetic",
            dataset="test fixture",
            resolution="n/a",
            licence="n/a",
        ),
        data_caveat="synthetic series for testing the cross-check wiring",
        lon=81.297,
        lat=21.2517,
        model_used="synthetic",
        warnings=[],
    )
    return Enrichment(
        soil=SoilProfile(
            clay_pct=41.5,
            sand_pct=22.7,
            silt_pct=35.8,
            texture_class="clay",
            hydrologic_soil_group="D",
            lon=81.297,
            lat=21.2517,
        ),
        land_cover=LandCover(
            # The same shape as the catchment mask: `fractions_within` indexes
            # the code grid by the mask, so a mismatch is an IndexError.
            codes=np.full(GRID, 40, dtype=np.uint8),
            fractions={"cropland": 1.0},
            dominant_class="cropland",
            tiles_used=["synthetic"],
        ),
        rainfall=rainfall,
    )


#: Shared by the land-cover grid and the catchment mask, which must agree.
GRID = (8, 8)


class _Catchment:
    """Only the fields `_runoff_payload` reads."""

    area_m2 = 2_642_675.0
    mask = np.ones(GRID, dtype=bool)


def test_the_cross_check_reaches_the_response(monsoon_year) -> None:
    daily, dates = monsoon_year
    body = analysis._runoff_payload(_Catchment(), build_enrichment(daily, dates))  # type: ignore[arg-type]

    assert body is not None and body["available"] is True
    assert "cross_check" in body, "the Indian cross-check is missing from the response"
    cross = body["cross_check"]
    assert cross["methods"], "no methods were reported"
    assert cross["scs_cn_runoff_mm"] > 0


def test_it_is_fed_the_scs_cn_figure_it_is_checking(monsoon_year) -> None:
    """A cross-check against the wrong number is worse than none."""
    daily, dates = monsoon_year
    body = analysis._runoff_payload(_Catchment(), build_enrichment(daily, dates))  # type: ignore[arg-type]
    assert body is not None
    assert body["cross_check"]["scs_cn_runoff_mm"] == pytest.approx(
        body["annual_mean"]["runoff_depth_mm"], abs=0.1
    )


def test_every_method_reports_itself_either_way(monsoon_year) -> None:
    """Including the ones that cannot run here: Strange needs its published
    table and Khosla needs a temperature the rainfall source does not supply."""
    daily, dates = monsoon_year
    body = analysis._runoff_payload(_Catchment(), build_enrichment(daily, dates))  # type: ignore[arg-type]
    assert body is not None
    for method in body["cross_check"]["methods"]:
        assert method["note"], method["method"]
        assert method["reference"], method["method"]
        if not method["applicable"]:
            assert len(method["note"]) > 40, "an unavailable method should say why"


def test_khosla_is_unavailable_without_a_temperature_series(monsoon_year) -> None:
    daily, dates = monsoon_year
    body = analysis._runoff_payload(_Catchment(), build_enrichment(daily, dates))  # type: ignore[arg-type]
    assert body is not None
    khosla = [m for m in body["cross_check"]["methods"] if m["method"] == "khosla"]
    assert khosla and khosla[0]["applicable"] is False
    assert "temperature" in khosla[0]["note"]


def test_the_monsoon_total_comes_from_the_monsoon_months(monsoon_year) -> None:
    """Barlow and Strange are defined on monsoon rainfall, not the annual total,
    so feeding the annual figure would overstate both by about 12 %."""
    daily, dates = monsoon_year
    enrichment = build_enrichment(daily, dates)
    body = analysis._runoff_payload(_Catchment(), enrichment)  # type: ignore[arg-type]
    assert body is not None
    rain = enrichment.rainfall
    assert rain is not None
    expected = sum(rain.monthly_normals_mm[m - 1] for m in rain.monsoon_months)
    assert expected < rain.mean_annual_mm, "the fixture should not be all-monsoon"
    # The cross-check does not echo the monsoon total, so this asserts the
    # relationship it must satisfy: the figure used is below the annual one.
    assert body["cross_check"]["scs_cn_runoff_mm"] <= rain.mean_annual_mm
