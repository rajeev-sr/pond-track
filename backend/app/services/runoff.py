"""Runoff estimation by the SCS Curve Number method (HLD §6.6, MC-17).

Three things here are deliberate departures from the textbook US formulation,
each for a documented Indian-practice reason:

* **Ia = 0.3 S**, not 0.2 S. CWC/IMD practice; the US ratio over-predicts runoff
  for Indian monsoon regimes (HLD CH-15).
* **Applied to the daily series, then summed.** The SCS equation is convex, so
  feeding it an annual total inflates runoff enormously -- 2.3x on the worked
  example in HLD §6.9. This is the single most common error in implementations of
  the method.
* **Antecedent moisture is derived per day** from the preceding five days of
  rainfall, and the growing season comes from the *derived* monsoon window rather
  than an assumed June-September.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np

from app.core.units import runoff_depth_mm_to_volume_m3

#: Initial-abstraction ratio. 0.2 is the US default; 0.3 is Indian practice.
LAMBDA_IA = 0.3

AmcClass = Literal["I", "II", "III"]

#: CN(II) by land cover and Hydrologic Soil Group. NRCS TR-55 values mapped onto
#: ESA WorldCover classes, cross-checked against the land-use/cover complexes in
#: the Handbook of Hydrology (Ministry of Agriculture, Government of India),
#: which carries the Indian classes TR-55 lacks -- cropped land, fallow,
#: wasteland, scrub forest, degraded pasture.
CURVE_NUMBERS: dict[str, dict[str, int]] = {
    "tree_cover": {"A": 30, "B": 55, "C": 70, "D": 77},
    "shrubland": {"A": 35, "B": 56, "C": 70, "D": 77},
    "grassland": {"A": 49, "B": 69, "C": 79, "D": 84},
    "cropland": {"A": 67, "B": 78, "C": 85, "D": 89},
    "built_up": {"A": 77, "B": 85, "C": 90, "D": 92},
    "bare_sparse_vegetation": {"A": 77, "B": 86, "C": 91, "D": 94},
    "herbaceous_wetland": {"A": 85, "B": 88, "C": 90, "D": 92},
    "moss_and_lichen": {"A": 68, "B": 79, "C": 86, "D": 89},
    "snow_and_ice": {"A": 98, "B": 98, "C": 98, "D": 98},
    "permanent_water": {"A": 100, "B": 100, "C": 100, "D": 100},
    "mangroves": {"A": 100, "B": 100, "C": 100, "D": 100},
}

#: Used when land cover is unavailable, so the tier ladder can still produce a
#: runoff figure. Cropland is the most common cover in rural India and is stated
#: explicitly in the response as an assumption, not passed off as measured.
FALLBACK_LAND_COVER = "cropland"

#: AMC thresholds on the preceding 5 days of rainfall, in mm (NRCS).
AMC_GROWING = (35.5, 53.3)
AMC_DORMANT = (12.7, 27.9)


def _clamp_cn(value: float) -> float:
    """Keep a curve number inside [1, 100]."""
    return min(100.0, max(1.0, value))


@dataclass(frozen=True)
class CurveNumber:
    composite_cn2: float
    breakdown: list[dict[str, Any]]
    hydrologic_soil_group: str
    land_cover_source: str

    @property
    def cn1(self) -> float:
        """Dry antecedent conditions (NRCS AMC-I adjustment).

        Clamped to the valid range: both adjustment formulas are asymptotic to
        100, so at CN = 100 (open water) floating point returns 100.000000000001
        and would be rejected as out of range.
        """
        return _clamp_cn(4.2 * self.composite_cn2 / (10.0 - 0.058 * self.composite_cn2))

    @property
    def cn3(self) -> float:
        """Wet antecedent conditions (NRCS AMC-III adjustment)."""
        return _clamp_cn(23.0 * self.composite_cn2 / (10.0 + 0.13 * self.composite_cn2))

    def as_dict(self) -> dict[str, Any]:
        return {
            "composite_cn_amc2": round(self.composite_cn2, 1),
            "composite_cn_amc1_dry": round(self.cn1, 1),
            "composite_cn_amc3_wet": round(self.cn3, 1),
            "hydrologic_soil_group": self.hydrologic_soil_group,
            "land_cover_source": self.land_cover_source,
            "breakdown": self.breakdown,
        }


@dataclass(frozen=True)
class RunoffEstimate:
    annual_mean_mm: float
    annual_mean_volume_m3: float
    dependable_75_mm: float
    dependable_75_volume_m3: float
    runoff_coefficient: float
    annual_by_year_mm: dict[int, float]
    monthly_mean_mm: list[float]
    catchment_area_m2: float
    curve_number: CurveNumber
    method: str = "SCS-CN, daily, Ia = 0.3 S"
    assumptions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "catchment_area_ha": round(self.catchment_area_m2 / 10_000.0, 3),
            "curve_number": self.curve_number.as_dict(),
            "annual_mean": {
                "runoff_depth_mm": round(self.annual_mean_mm, 1),
                "runoff_volume_m3": round(self.annual_mean_volume_m3, 0),
                "runoff_coefficient": round(self.runoff_coefficient, 3),
            },
            "design_75_percent_dependable": {
                "runoff_depth_mm": round(self.dependable_75_mm, 1),
                "runoff_volume_m3": round(self.dependable_75_volume_m3, 0),
                "note": (
                    "Dependable *runoff*, taken from the ranked annual runoff series "
                    "rather than computed from dependable rainfall -- the SCS "
                    "equation is non-linear, so the two are not the same."
                ),
            },
            "monthly_mean_runoff_mm": [round(v, 1) for v in self.monthly_mean_mm],
            "annual_by_year_mm": {y: round(v, 1) for y, v in self.annual_by_year_mm.items()},
            "assumptions": list(self.assumptions),
            "warnings": list(self.warnings),
        }


def potential_retention_mm(cn: float) -> float:
    """S = 25400/CN - 254, in millimetres."""
    if not 1.0 <= cn <= 100.0:
        raise ValueError(f"curve number must be within [1, 100], got {cn}")
    return 25400.0 / cn - 254.0


def runoff_depth_mm(rainfall_mm: float, cn: float, lam: float = LAMBDA_IA) -> float:
    """SCS-CN runoff depth for a single rainfall event."""
    s = potential_retention_mm(cn)
    ia = lam * s
    if rainfall_mm <= ia:
        return 0.0
    excess = rainfall_mm - ia
    return float(excess**2 / (excess + s))


def composite_curve_number(
    cover_fractions: dict[str, float],
    hsg: str,
    *,
    land_cover_source: str = "esa_worldcover",
) -> CurveNumber:
    """Area-weighted CN across land-cover classes for one soil group."""
    if hsg not in ("A", "B", "C", "D"):
        raise ValueError(f"hydrologic soil group must be A-D, got {hsg!r}")
    if not cover_fractions:
        raise ValueError("no land-cover fractions supplied")

    total = sum(cover_fractions.values())
    if total <= 0:
        raise ValueError("land-cover fractions sum to zero")

    weighted = 0.0
    breakdown: list[dict[str, Any]] = []
    for cover, raw_fraction in sorted(cover_fractions.items(), key=lambda kv: -kv[1]):
        fraction = raw_fraction / total
        table = CURVE_NUMBERS.get(cover)
        if table is None:  # an unmapped class: fall back, and say so
            table = CURVE_NUMBERS[FALLBACK_LAND_COVER]
        cn = float(table[hsg])
        weighted += fraction * cn
        breakdown.append(
            {
                "land_cover": cover,
                "hydrologic_soil_group": hsg,
                "area_fraction": round(fraction, 4),
                "curve_number": cn,
                "weighted_contribution": round(fraction * cn, 2),
            }
        )
    return CurveNumber(
        composite_cn2=weighted,
        breakdown=breakdown,
        hydrologic_soil_group=hsg,
        land_cover_source=land_cover_source,
    )


def _amc_class(antecedent_5day_mm: float, growing_season: bool) -> AmcClass:
    lo, hi = AMC_GROWING if growing_season else AMC_DORMANT
    if antecedent_5day_mm < lo:
        return "I"
    if antecedent_5day_mm > hi:
        return "III"
    return "II"


def estimate_runoff(
    daily_mm: np.ndarray,
    years: np.ndarray,
    months: np.ndarray,
    curve_number: CurveNumber,
    catchment_area_m2: float,
    *,
    monsoon_months: list[int] | None = None,
    lam: float = LAMBDA_IA,
) -> RunoffEstimate:
    """Run SCS-CN over a daily rainfall series and aggregate.

    Antecedent moisture is classified per day from the preceding five days, so a
    storm falling on already-saturated ground yields more runoff than the same
    storm on dry ground -- which is the whole point of the AMC adjustment and is
    lost if a single CN is applied to the entire record.
    """
    if daily_mm.size != years.size or daily_mm.size != months.size:
        raise ValueError("rainfall, year and month arrays must be the same length")
    if catchment_area_m2 <= 0:
        raise ValueError(f"catchment area must be positive, got {catchment_area_m2}")

    growing = set(monsoon_months or [6, 7, 8, 9])
    cn_by_amc = {
        "I": curve_number.cn1,
        "II": curve_number.composite_cn2,
        "III": curve_number.cn3,
    }

    # Rolling 5-day antecedent total, excluding the day itself.
    padded = np.concatenate([np.zeros(5), daily_mm])
    antecedent = np.convolve(padded, np.ones(5), mode="valid")[:-1]

    depths = np.empty_like(daily_mm)
    for i, rain in enumerate(daily_mm):
        amc = _amc_class(float(antecedent[i]), int(months[i]) in growing)
        depths[i] = runoff_depth_mm(float(rain), cn_by_amc[amc], lam)

    annual: dict[int, float] = {}
    rain_annual: dict[int, float] = {}
    for y in sorted(set(years.tolist())):
        sel = years == y
        if int(sel.sum()) < 360:
            continue
        annual[int(y)] = float(depths[sel].sum())
        rain_annual[int(y)] = float(daily_mm[sel].sum())
    if not annual:
        raise ValueError("no complete year in the rainfall series")

    n_years = len(annual)
    monthly = [float(depths[months == m].sum()) / n_years for m in range(1, 13)]
    mean_runoff = float(np.mean(list(annual.values())))
    mean_rain = float(np.mean(list(rain_annual.values())))

    # Dependable *runoff*: rank the annual runoff series directly. Deriving it
    # from dependable rainfall would be wrong, because the SCS relation is not
    # linear -- the 75th-percentile rainfall year is not the 75th-percentile
    # runoff year.
    ranked = np.sort(np.asarray(list(annual.values())))[::-1]
    exceedance = np.arange(1, ranked.size + 1) / (ranked.size + 1)
    dep75 = float(np.interp(0.75, exceedance, ranked))

    assumptions = [
        f"Ia = {lam} S (Indian practice per CWC/IMD, not the US default 0.2 S)",
        "SCS-CN applied to the daily series and summed, never to annual totals",
        "Antecedent moisture classified per day from the preceding 5 days of rainfall",
        f"Growing season taken as the derived monsoon window {sorted(growing)}",
        f"Curve numbers from NRCS TR-55 mapped to {curve_number.land_cover_source} "
        "classes, cross-checked against the Indian Handbook of Hydrology",
    ]
    return RunoffEstimate(
        annual_mean_mm=mean_runoff,
        annual_mean_volume_m3=runoff_depth_mm_to_volume_m3(mean_runoff, catchment_area_m2),
        dependable_75_mm=dep75,
        dependable_75_volume_m3=runoff_depth_mm_to_volume_m3(dep75, catchment_area_m2),
        runoff_coefficient=(mean_runoff / mean_rain) if mean_rain > 0 else 0.0,
        annual_by_year_mm=annual,
        monthly_mean_mm=monthly,
        catchment_area_m2=catchment_area_m2,
        curve_number=curve_number,
        assumptions=assumptions,
    )
