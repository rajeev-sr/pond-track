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
from typing import Any

import numpy as np

from app.providers.base import Provenance, ProviderUnavailableError, get_json
from app.providers.rainfall import cache
from app.providers.rainfall.base import RainfallStats, build_stats

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


def fetch_rainfall(lon: float, lat: float, years: int = DEFAULT_YEARS) -> RainfallStats:
    """Daily rainfall and ET0 for a coordinate, with design statistics."""
    end = dt.date.today() - dt.timedelta(days=_LAG_DAYS)
    end = dt.date(end.year - 1, 12, 31)  # last complete calendar year
    start = dt.date(end.year - years + 1, 1, 1)

    cached = cache.stats_from_cache(
        PROVIDER,
        lon,
        lat,
        start,
        end,
        provenance=PROVENANCE,
        data_caveat=DATA_CAVEAT,
        model_used="ERA5-Land",
    )
    if cached is not None:
        return cached  # type: ignore[return-value]

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
    et0_series = (
        None if not et0_raw else np.array([0.0 if v is None else float(v) for v in et0_raw])
    )
    # Stored before the statistics are derived: a series with too few complete
    # years is still worth keeping, because the next request can extend it
    # rather than re-fetching what is already here.
    cache.write(PROVIDER, lon, lat, dates=times, precipitation_mm=precip, et0_mm=et0_series)

    return build_stats(
        PROVIDER,
        daily_mm=precip,
        dates=times,
        lon=lon,
        lat=lat,
        model_used=model_used,
        provenance=PROVENANCE,
        data_caveat=DATA_CAVEAT,
        et0_daily_mm=(
            None if not et0_raw else np.array([0.0 if v is None else float(v) for v in et0_raw])
        ),
        # Open-Meteo's archive is not requested with a temperature variable: an
        # unknown `daily` name makes the whole call fail rather than omitting the
        # field, so adding one unverified would risk the rainfall series itself.
        # NASA POWER supplies temperature instead, and Khosla's cross-check says
        # so when it is absent.
        temp_daily_c=None,
        warnings=list(attempts),
    )
