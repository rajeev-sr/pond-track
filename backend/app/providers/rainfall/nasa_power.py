"""NASA POWER daily rainfall and temperature (HLD 4.2 B3, M4-1).

A second, independent rainfall source, and the only one here that supplies mean
air temperature -- which is what Khosla's runoff formula needs and what
Open-Meteo's archive is not requested for.

Independent in the way that matters: Open-Meteo serves ERA5-Land, a European
reanalysis; POWER serves NASA's MERRA-2 and satellite-derived products. Two
reanalyses that disagree about a monsoon are telling you the uncertainty is real,
which is the point of having both. Where they agree, the figure the pond is sized
on is worth more.

The trade-off is resolution. ERA5-Land is 0.1 deg (~11 km); POWER is
0.5 x 0.625 deg (~55 x 60 km), so a POWER cell can span several districts and a
whole range of hills. It is a cross-check and a temperature source, not a
replacement -- which is why `fetch_rainfall` in `open_meteo` remains the primary.

Two response details that will bite anyone who assumes otherwise:

* Missing values are **-999.0**, not null. Summed naively that is not a gap in the
  record, it is a year with minus three hundred metres of rainfall.
* Dates are `YYYYMMDD` strings keyed in an object, not an array parallel to the
  values -- so the series has to be built by sorting the keys, and a missing day
  is simply an absent key rather than a hole to line up.
"""

from __future__ import annotations

import datetime as dt
import logging

import numpy as np

from app.providers.base import Provenance, ProviderUnavailableError, get_json
from app.providers.rainfall import cache
from app.providers.rainfall.base import RainfallStats, build_stats

log = logging.getLogger(__name__)

PROVIDER = "nasa_power"
BASE_URL = "https://power.larc.nasa.gov/api/temporal/daily/point"

PROVENANCE = Provenance(
    provider="NASA POWER",
    dataset="MERRA-2 / satellite-derived daily (community=AG)",
    resolution="0.5 x 0.625 deg (~55 x 60 km)",
    licence="Public domain (NASA), attribution requested",
)

DATA_CAVEAT = (
    "NASA POWER at 0.5 x 0.625 degrees -- roughly 55 by 60 km, so one cell can "
    "span several districts. Held here as an independent cross-check on the "
    "finer ERA5-Land series and as the source of mean temperature; not a "
    "substitute for either that or IMD's gauge-based grid."
)

#: POWER's sentinel for a missing value. Not null -- summing it silently gives a
#: year with minus three hundred metres of rain.
FILL_VALUE = -999.0

#: Bias-corrected total precipitation, mm/day, and mean air temperature at 2 m.
PARAMETERS = ("PRECTOTCORR", "T2M")

#: `community=AG` selects the agroclimatology parameter set, which is the one
#: PRECTOTCORR and T2M belong to.
COMMUNITY = "AG"

DEFAULT_YEARS = 30

#: POWER lags real time by roughly two months for the corrected products.
_LAG_DAYS = 75

#: Above this share of missing days the series is not usable for design
#: statistics -- the same guard the Open-Meteo provider applies.
MAX_FILL_FRACTION = 0.10

REQUEST_TIMEOUT_S = 90.0


def fetch_rainfall(lon: float, lat: float, years: int = DEFAULT_YEARS) -> RainfallStats:
    """Daily rainfall and mean temperature for a coordinate, with design statistics.

    Requests whole calendar years up to the last complete one, so the annual
    totals `build_stats` derives are comparable with each other rather than one
    of them being a part-year.
    """
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
        model_used="MERRA-2 (POWER daily, community=AG)",
    )
    if cached is not None:
        return cached  # type: ignore[return-value]

    payload = get_json(
        PROVIDER,
        BASE_URL,
        params={
            "parameters": ",".join(PARAMETERS),
            "community": COMMUNITY,
            "latitude": lat,
            "longitude": lon,
            "start": start.strftime("%Y%m%d"),
            "end": end.strftime("%Y%m%d"),
            "format": "JSON",
        },
        timeout=REQUEST_TIMEOUT_S,
    )

    try:
        series = payload["properties"]["parameter"]
        precip_raw = series["PRECTOTCORR"]
        temp_raw = series["T2M"]
    except (KeyError, TypeError) as exc:
        raise ProviderUnavailableError(PROVIDER, f"unexpected response: {exc}") from exc

    # The values are keyed by date rather than parallel to a time array, so the
    # series is built by sorting keys. A missing day is an absent key, not a hole
    # to align -- which is why this does not zip two lists together.
    dates: list[dt.date] = []
    precip: list[float] = []
    temps: list[float] = []
    filled = 0

    for key in sorted(precip_raw):
        try:
            day = dt.datetime.strptime(key, "%Y%m%d").date()
        except ValueError:
            log.warning("nasa_power_bad_date", extra={"key": key})
            continue

        rain = float(precip_raw[key])
        temp = float(temp_raw.get(key, FILL_VALUE))
        if rain <= FILL_VALUE + 1.0:
            # Treated as zero rainfall but counted as a gap, so the fill fraction
            # guard below can refuse a series that is mostly sentinel.
            filled += 1
            rain = 0.0
        dates.append(day)
        precip.append(rain)
        # A missing temperature is carried as NaN rather than zero: 0 degrees is a
        # plausible-looking value that would halve Khosla's loss term for that
        # month, whereas NaN propagates visibly.
        temps.append(np.nan if temp <= FILL_VALUE + 1.0 else temp)

    if not dates:
        raise ProviderUnavailableError(PROVIDER, "the response contained no dated values")

    if filled / len(dates) > MAX_FILL_FRACTION:
        raise ProviderUnavailableError(
            PROVIDER,
            f"{100 * filled / len(dates):.0f}% of days are fill values "
            f"({FILL_VALUE}); the series is not usable for design statistics",
        )

    warnings: list[str] = []
    if filled:
        warnings.append(
            f"{filled} of {len(dates)} days were fill values and counted as zero rainfall"
        )

    temp_array = np.array(temps)
    missing_temp = int(np.isnan(temp_array).sum())
    if missing_temp:
        warnings.append(f"{missing_temp} days had no temperature and were excluded from the means")

    daily = np.array(precip)
    # Stored before the statistics are derived, so a series that turns out to
    # have too few complete years is still cached -- the next request for a
    # longer range can extend it rather than re-fetching what is already here.
    cache.write(
        PROVIDER,
        lon,
        lat,
        dates=dates,
        precipitation_mm=daily,
        temperature_c=temp_array,
    )

    return build_stats(
        PROVIDER,
        daily_mm=daily,
        dates=dates,
        lon=lon,
        lat=lat,
        model_used="MERRA-2 (POWER daily, community=AG)",
        provenance=PROVENANCE,
        data_caveat=DATA_CAVEAT,
        # POWER's AG set does not carry a reference-ET product in this request.
        et0_daily_mm=None,
        temp_daily_c=temp_array,
        warnings=warnings,
    )
