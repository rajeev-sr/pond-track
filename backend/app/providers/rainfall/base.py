"""Shared rainfall statistics, independent of which source supplied the series.

Two providers now return daily rainfall for a point -- Open-Meteo's ERA5-Land
reanalysis and NASA POWER -- and every design figure downstream is derived the
same way from either: annual totals, coefficient of variation, Weibull dependable
rainfall, monthly normals, and the monsoon window found from those normals rather
than assumed.

So the derivation lives here rather than in a provider. Duplicating fifty lines
of it per source would guarantee the two drift, and a 75 %-dependable rainfall
that means something slightly different depending on which service answered is
worse than having only one service.

`monthly_temp_c` is part of this shape because Khosla's runoff formula needs mean
monthly temperature and only NASA POWER supplies it. Open-Meteo leaves it None,
which is what makes the cross-check say "Khosla needs a temperature this source
does not provide" rather than quietly evaluating it on a guess.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from app.providers.base import Provenance, ProviderUnavailableError

#: A year with fewer days than this is a part-year and is excluded from the
#: statistics: an incomplete year drags the mean down and the CV up.
MIN_DAYS_IN_COMPLETE_YEAR = 360

#: A day is "rainy" at or above this depth, the IMD convention.
RAINY_DAY_THRESHOLD_MM = 2.5

MONTH_NAMES = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


@dataclass(frozen=True)
class RainfallStats:
    """Design statistics derived from a daily series."""

    daily_mm: np.ndarray  # the series itself, for SCS-CN
    dates: list[dt.date]
    years: tuple[int, ...]
    annual_totals_mm: dict[int, float]
    mean_annual_mm: float
    median_annual_mm: float
    std_annual_mm: float
    cv: float
    min_annual_mm: float
    max_annual_mm: float
    dependable_50_mm: float
    dependable_75_mm: float
    dependable_90_mm: float
    monthly_normals_mm: list[float]
    monsoon_months: list[int]
    monsoon_type: str
    monsoon_share_pct: float
    rainy_days_per_year: float
    max_1day_mm: float
    et0_annual_mm: float | None
    et0_monthly_mm: list[float] | None
    #: Mean temperature per calendar month. Only NASA POWER supplies it;
    #: Open-Meteo leaves it None, which is what makes Khosla's cross-check
    #: report what it needs instead of guessing.
    monthly_temp_c: list[float] | None
    lon: float
    lat: float
    #: Who produced this series, and what a reader should know about it. Fields
    #: rather than module constants, because they describe *this* series -- a
    #: shared `as_dict()` that reached for one provider's constants would label
    #: every source as that provider.
    provenance: Provenance
    data_caveat: str
    model_used: str = "default (best available)"
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "period": {
                "start": self.dates[0].isoformat(),
                "end": self.dates[-1].isoformat(),
                "complete_years": len(self.years),
            },
            "annual": {
                "mean_mm": round(self.mean_annual_mm, 1),
                "median_mm": round(self.median_annual_mm, 1),
                "std_dev_mm": round(self.std_annual_mm, 1),
                "coefficient_of_variation": round(self.cv, 3),
                "min_mm": round(self.min_annual_mm, 1),
                "max_mm": round(self.max_annual_mm, 1),
                "dependable_50_mm": round(self.dependable_50_mm, 1),
                "dependable_75_mm": round(self.dependable_75_mm, 1),
                "dependable_90_mm": round(self.dependable_90_mm, 1),
            },
            "monsoon": {
                "type": self.monsoon_type,
                "months": [MONTH_NAMES[m - 1] for m in self.monsoon_months],
                "share_pct": round(self.monsoon_share_pct, 1),
                "note": (
                    "Window derived from the monthly normals -- the four "
                    "consecutive months carrying the largest share -- not assumed "
                    "to be June-September."
                ),
            },
            "monthly_normals_mm": [round(v, 1) for v in self.monthly_normals_mm],
            "rainy_days_per_year": round(self.rainy_days_per_year, 1),
            "max_1day_mm": round(self.max_1day_mm, 1),
            "reference_evapotranspiration": (
                None
                if self.et0_annual_mm is None
                else {
                    "annual_mm": round(self.et0_annual_mm, 1),
                    "monthly_mm": [round(v, 1) for v in (self.et0_monthly_mm or [])],
                }
            ),
            "sampled_at": {"lon": round(self.lon, 6), "lat": round(self.lat, 6)},
            "reanalysis_model": self.model_used,
            "source": self.provenance.as_dict(),
            "data_caveat": self.data_caveat,
            "warnings": list(self.warnings),
        }


def dependable_rainfall(annual_totals: list[float], probability: float) -> float:
    """Rainfall equalled or exceeded in `probability` of years (Weibull).

    Sort descending, assign each year an exceedance probability m/(N+1), then
    interpolate. The 75 % value is the standard design figure for Indian minor
    irrigation: sizing on the mean over-estimates supply in one year out of two.
    """
    if not annual_totals:
        raise ValueError("no annual totals")
    values = np.sort(np.asarray(annual_totals, dtype=float))[::-1]
    n = values.size
    exceedance = np.arange(1, n + 1) / (n + 1)
    # np.interp needs an increasing x, and exceedance rises as rainfall falls.
    return float(np.interp(probability, exceedance, values))


def derive_monsoon_window(monthly_normals: list[float]) -> tuple[list[int], str, float]:
    """The four consecutive months carrying the largest share of annual rainfall.

    Derived rather than assumed. Most of India peaks with the south-west monsoon
    in June-September, but Tamil Nadu, coastal Andhra and parts of Kerala peak
    with the retreating north-east monsoon in October-December. Hard-coding
    June-September would mis-state the design season for the whole south-east
    peninsula (HLD CH-23 note).
    """
    total = sum(monthly_normals)
    if total <= 0:
        return [6, 7, 8, 9], "undetermined", 0.0
    best_start, best_sum = 0, -1.0
    for start in range(12):
        window = [(start + k) % 12 for k in range(4)]
        s = sum(monthly_normals[m] for m in window)
        if s > best_sum:
            best_start, best_sum = start, s
    months = [((best_start + k) % 12) + 1 for k in range(4)]
    share = 100.0 * best_sum / total
    if set(months) & {6, 7, 8} and max(months) <= 10:
        kind = "southwest"
    elif set(months) & {10, 11, 12}:
        kind = "northeast"
    else:
        kind = "other"
    return months, kind, share


def build_stats(
    provider: str,
    *,
    daily_mm: np.ndarray,
    dates: list[dt.date],
    lon: float,
    lat: float,
    model_used: str,
    provenance: Provenance,
    data_caveat: str,
    et0_daily_mm: np.ndarray | None = None,
    temp_daily_c: np.ndarray | None = None,
    warnings: list[str] | None = None,
) -> RainfallStats:
    """Derive the design statistics from a daily series, whatever produced it.

    Part-years are dropped rather than scaled: a year with 200 days of record is
    not a dry year, and averaging it in as one understates the mean and inflates
    the coefficient of variation -- which then propagates into the dependable
    rainfall that the pond is sized on.
    """
    warnings = list(warnings or [])
    if daily_mm.size < 365:
        raise ProviderUnavailableError(
            provider, f"only {daily_mm.size} days returned; need at least a year"
        )

    year_arr = np.array([d.year for d in dates])
    month_arr = np.array([d.month for d in dates])

    annual: dict[int, float] = {}
    for year in sorted(set(year_arr.tolist())):
        selected = year_arr == year
        days = int(selected.sum())
        if days < MIN_DAYS_IN_COMPLETE_YEAR:
            warnings.append(f"{year} had only {days} days and was excluded")
            continue
        annual[int(year)] = float(daily_mm[selected].sum())
    if not annual:
        raise ProviderUnavailableError(provider, "no complete year in the returned series")

    totals = list(annual.values())
    complete = len(annual)
    monthly = [float(daily_mm[month_arr == m].sum()) / complete for m in range(1, 13)]
    months, kind, share = derive_monsoon_window(monthly)

    et0_annual: float | None = None
    et0_monthly: list[float] | None = None
    if et0_daily_mm is not None and et0_daily_mm.size == daily_mm.size:
        et0_annual = float(et0_daily_mm.sum()) / complete
        et0_monthly = [float(et0_daily_mm[month_arr == m].sum()) / complete for m in range(1, 13)]

    monthly_temp: list[float] | None = None
    if temp_daily_c is not None and temp_daily_c.size == daily_mm.size:
        # A *mean* per month, not a sum -- unlike rainfall and ET0, which
        # accumulate. Summing it would give a "temperature" in the thousands.
        monthly_temp = []
        for month in range(1, 13):
            selected = month_arr == month
            values = temp_daily_c[selected]
            usable = values[np.isfinite(values)]
            # Filtered rather than `.mean()`: NASA POWER carries a missing
            # temperature as NaN, and one of them would make the whole month's
            # mean NaN -- which then makes Khosla's loss term NaN and the runoff
            # estimate NaN, three steps from where the gap actually was.
            monthly_temp.append(float(usable.mean()) if usable.size else float("nan"))

    spread = float(np.std(totals, ddof=1)) if len(totals) > 1 else 0.0
    mean = float(np.mean(totals))
    return RainfallStats(
        daily_mm=daily_mm,
        dates=dates,
        years=tuple(sorted(annual)),
        annual_totals_mm=annual,
        mean_annual_mm=mean,
        median_annual_mm=float(np.median(totals)),
        std_annual_mm=spread,
        cv=(spread / mean) if mean > 0 else 0.0,
        min_annual_mm=float(np.min(totals)),
        max_annual_mm=float(np.max(totals)),
        dependable_50_mm=dependable_rainfall(totals, 0.50),
        dependable_75_mm=dependable_rainfall(totals, 0.75),
        dependable_90_mm=dependable_rainfall(totals, 0.90),
        monthly_normals_mm=monthly,
        monsoon_months=months,
        monsoon_type=kind,
        monsoon_share_pct=share,
        rainy_days_per_year=float((daily_mm >= RAINY_DAY_THRESHOLD_MM).sum()) / complete,
        max_1day_mm=float(daily_mm.max()),
        et0_annual_mm=et0_annual,
        et0_monthly_mm=et0_monthly,
        monthly_temp_c=monthly_temp,
        lon=lon,
        lat=lat,
        provenance=provenance,
        data_caveat=data_caveat,
        model_used=model_used,
        warnings=warnings,
    )
