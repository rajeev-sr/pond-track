"""Historical rainfall and reference evapotranspiration (HLD §4.2 B2).

Open-Meteo's archive serves ERA5-Land at 0.1 deg (~11 km) from 1950, keyless,
and returns `et0_fao_evapotranspiration` alongside precipitation -- so one call
supplies both the runoff input and the evaporation term a pond water balance
needs.

HLD §4.2 B1 names IMD's 0.25 deg gauge-based grid as the authoritative Indian
record. It is reached through `imdlib`, which downloads bulk binaries rather than
answering a request, so it belongs in a seeding step (M8-8) rather than here. The
`data_caveat` field on every response says so, instead of letting a reanalysis
figure pass for the official one.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from app.providers.base import Provenance, ProviderUnavailableError, get_json

PROVIDER = "open_meteo_era5_land"
BASE_URL = "https://archive-api.open-meteo.com/v1/archive"
PROVENANCE = Provenance(
    provider="Open-Meteo",
    dataset="ERA5-Land reanalysis (archive)",
    resolution="0.1 deg (~11 km)",
    licence="CC-BY 4.0, non-commercial use",
)

DEFAULT_YEARS = 30

#: Reanalysis models to try, in order. ERA5-Land is finer (0.1 deg vs 0.25) but
#: its coverage has gaps: at the sample location it returns an all-null series.
#: Falling back to the default model is what makes the provider usable, and the
#: model that answered is reported so a figure is never anonymous.
MODEL_PREFERENCE: tuple[str | None, ...] = ("era5_land", None)

#: Above this fraction of nulls the series is unusable. Without an explicit
#: check, coercing `None -> 0.0` turns a total provider failure into a confident
#: "no rain for thirty years" -- plausible-looking and completely wrong.
MAX_NULL_FRACTION = 0.10
#: ERA5-Land lags real time by several days; ending the window at last year-end
#: keeps every year in the series complete, which matters because the statistics
#: are annual totals.
_LAG_DAYS = 10

DATA_CAVEAT = (
    "ERA5-Land reanalysis at ~11 km. Suitable for design screening, but for a "
    "submitted scheme cross-check against IMD's 0.25 deg gauge-based grid, which "
    "is the authoritative Indian record."
)

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
    lon: float
    lat: float
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
            "source": PROVENANCE.as_dict(),
            "data_caveat": DATA_CAVEAT,
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


def fetch_rainfall(lon: float, lat: float, years: int = DEFAULT_YEARS) -> RainfallStats:
    """Daily rainfall and ET0 for a coordinate, with design statistics."""
    end = dt.date.today() - dt.timedelta(days=_LAG_DAYS)
    end = dt.date(end.year - 1, 12, 31)  # last complete calendar year
    start = dt.date(end.year - years + 1, 1, 1)

    attempts: list[str] = []
    times: list[dt.date] = []
    precip = np.empty(0)
    et0_raw: Any = None
    model_used: str | None = None

    for model in MODEL_PREFERENCE:
        params: dict[str, Any] = {
            "latitude": lat,
            "longitude": lon,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "daily": "precipitation_sum,et0_fao_evapotranspiration",
            "timezone": "Asia/Kolkata",
        }
        if model:
            params["models"] = model
        payload = get_json(PROVIDER, BASE_URL, params=params, timeout=60.0)
        try:
            daily = payload["daily"]
            raw_times = daily["time"]
            raw_precip = daily["precipitation_sum"]
        except (KeyError, TypeError) as exc:
            raise ProviderUnavailableError(PROVIDER, f"unexpected response: {exc}") from exc

        nulls = sum(1 for v in raw_precip if v is None)
        if raw_precip and nulls / len(raw_precip) > MAX_NULL_FRACTION:
            attempts.append(
                f"{model or 'default'}: {100 * nulls / len(raw_precip):.0f}% of days null"
            )
            continue

        times = [dt.date.fromisoformat(t) for t in raw_times]
        precip = np.array([0.0 if v is None else float(v) for v in raw_precip])
        et0_raw = daily.get("et0_fao_evapotranspiration")
        model_used = model or "default (best available)"
        break

    if model_used is None:
        raise ProviderUnavailableError(
            PROVIDER,
            "no reanalysis model returned a usable series for this location ("
            + "; ".join(attempts)
            + ")",
        )
    if precip.size < 365:
        raise ProviderUnavailableError(
            PROVIDER, f"only {precip.size} days returned; need at least a year"
        )

    year_arr = np.array([d.year for d in times])
    month_arr = np.array([d.month for d in times])
    warnings: list[str] = list(attempts)

    annual: dict[int, float] = {}
    for y in sorted(set(year_arr.tolist())):
        sel = year_arr == y
        # Drop part-years: an incomplete year would drag the mean down.
        if int(sel.sum()) < 360:
            warnings.append(f"{y} had only {int(sel.sum())} days and was excluded")
            continue
        annual[int(y)] = float(precip[sel].sum())
    if not annual:
        raise ProviderUnavailableError(PROVIDER, "no complete year in the returned series")

    totals = list(annual.values())
    monthly = [float(precip[month_arr == m].sum()) / max(1, len(annual)) for m in range(1, 13)]
    months, kind, share = derive_monsoon_window(monthly)

    et0_annual: float | None = None
    et0_monthly: list[float] | None = None
    if et0_raw:
        et0 = np.array([0.0 if v is None else float(v) for v in et0_raw])
        et0_annual = float(et0.sum()) / len(annual)
        et0_monthly = [float(et0[month_arr == m].sum()) / max(1, len(annual)) for m in range(1, 13)]

    return RainfallStats(
        daily_mm=precip,
        dates=times,
        years=tuple(sorted(annual)),
        annual_totals_mm=annual,
        mean_annual_mm=float(np.mean(totals)),
        median_annual_mm=float(np.median(totals)),
        std_annual_mm=float(np.std(totals, ddof=1)) if len(totals) > 1 else 0.0,
        cv=(
            (float(np.std(totals, ddof=1)) / float(np.mean(totals)))
            if len(totals) > 1 and np.mean(totals) > 0
            else 0.0
        ),
        min_annual_mm=float(np.min(totals)),
        max_annual_mm=float(np.max(totals)),
        dependable_50_mm=dependable_rainfall(totals, 0.50),
        dependable_75_mm=dependable_rainfall(totals, 0.75),
        dependable_90_mm=dependable_rainfall(totals, 0.90),
        monthly_normals_mm=monthly,
        monsoon_months=months,
        monsoon_type=kind,
        monsoon_share_pct=share,
        rainy_days_per_year=float((precip >= 2.5).sum()) / len(annual),
        max_1day_mm=float(precip.max()),
        et0_annual_mm=et0_annual,
        et0_monthly_mm=et0_monthly,
        lon=lon,
        lat=lat,
        model_used=model_used,
        warnings=warnings,
    )
