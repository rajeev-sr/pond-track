"""Reference evapotranspiration from temperature alone (FAO-56, Hargreaves).

ERA5-Land, through Open-Meteo, carries FAO Penman-Monteith ET0 ready-made. But
Open-Meteo's archive rate-limits (HTTP 429) often enough that NASA POWER is the
series an analysis runs on a good share of the time -- and without ET0 the
pond's month-by-month water balance could not be computed at all, on runs that
were otherwise at the full data tier.

FAO-56 gives the method for exactly this case: when radiation, humidity and wind
are missing, ET0 from the daily temperature range and the radiation arriving at
the top of the atmosphere (eq. 52), which is computed from latitude and date
alone (eq. 21). POWER supplies daily maximum and minimum temperature for free.
"""

from __future__ import annotations

import datetime as dt
import math

import numpy as np
import numpy.typing as npt

#: Solar constant, MJ m-2 min-1 (FAO-56 eq. 21).
GSC = 0.0820

#: MJ m-2 day-1 to the equivalent evaporation in mm/day (FAO-56 eq. 20).
MJ_TO_MM = 0.408


def extraterrestrial_radiation(lat_deg: float, day_of_year: int) -> float:
    """Ra in MJ m-2 day-1 for a latitude and day of year (FAO-56 eqs. 21-25).

    Near the poles the sunset hour angle is clamped, so polar day and night give
    their limits rather than a math domain error.
    """
    phi = math.radians(lat_deg)
    angle = 2.0 * math.pi * day_of_year / 365.0
    dr = 1.0 + 0.033 * math.cos(angle)  # inverse relative Earth-Sun distance
    delta = 0.409 * math.sin(angle - 1.39)  # solar declination
    ws = math.acos(max(-1.0, min(1.0, -math.tan(phi) * math.tan(delta))))
    return (
        (24.0 * 60.0 / math.pi)
        * GSC
        * dr
        * (ws * math.sin(phi) * math.sin(delta) + math.cos(phi) * math.cos(delta) * math.sin(ws))
    )


def hargreaves(
    dates: list[dt.date],
    tmax_c: npt.NDArray[np.float64],
    tmin_c: npt.NDArray[np.float64],
    lat_deg: float,
) -> npt.NDArray[np.float64]:
    """Daily ET0 in mm by FAO-56 eq. 52; NaN where a temperature is missing.

        ET0 = 0.0023 (Tmean + 17.8) (Tmax - Tmin)^0.5 Ra

    with Ra as equivalent evaporation and Tmean the average of the two, as
    FAO-56 specifies for this equation. A day whose maximum is below its minimum
    (a data error) has no range, so no ET0, rather than the root of a negative.
    """
    ra_mm = np.array(
        [extraterrestrial_radiation(lat_deg, d.timetuple().tm_yday) * MJ_TO_MM for d in dates]
    )
    tmax = np.asarray(tmax_c, dtype=float)
    tmin = np.asarray(tmin_c, dtype=float)
    spread = tmax - tmin
    tmean = (tmax + tmin) / 2.0
    with np.errstate(invalid="ignore"):
        et0 = 0.0023 * (tmean + 17.8) * np.sqrt(spread) * ra_mm
    et0[~np.isfinite(et0) | (spread < 0)] = np.nan
    return np.asarray(np.clip(et0, 0.0, None), dtype=np.float64)


def fill_by_month(
    dates: list[dt.date], et0: npt.NDArray[np.float64]
) -> tuple[npt.NDArray[np.float64] | None, int]:
    """Missing days filled with the same calendar month's mean over the record.

    Monthly ET0 is a sum, so a gap left as zero would understate the month and a
    NaN would poison it. A month with no value at all in the whole record cannot
    be filled honestly, so the series is refused (None). Returns the filled
    series and how many days were filled.
    """
    months = np.array([d.month for d in dates])
    filled = et0.copy()
    gaps = int(np.isnan(filled).sum())
    for month in range(1, 13):
        selected = months == month
        if not selected.any():
            continue
        values = filled[selected]
        if np.isnan(values).all():
            return None, gaps
        values[np.isnan(values)] = float(np.nanmean(values))
        filled[selected] = values
    return filled, gaps
