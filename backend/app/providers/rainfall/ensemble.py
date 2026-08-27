"""Two rainfall sources, and the spread between them as uncertainty (M4-2).

Open-Meteo serves ERA5-Land, a European reanalysis at 0.1 deg. NASA POWER serves
MERRA-2 at 0.5 x 0.625 deg. Over the sample location they disagree by about 15 %
on mean annual rainfall -- 1313 mm against 1504 mm -- and that disagreement is
information: it is the honest uncertainty in a figure the pond volume is
proportional to.

**What is *not* done here, and why it matters most:** the two daily series are not
averaged. SCS-CN is non-linear in daily rainfall depth -- runoff from 100 mm in one
day far exceeds runoff from 50 mm on each of two days -- and two reanalyses put the
same storm on slightly different days. Averaging them would turn one 100 mm storm
into two 50 mm ones and systematically *understate* runoff, in a way that looks
like a smoother, better-behaved series. So SCS-CN is always run on a single
source's daily series, and the ensemble is used only for the annual statistics
and the uncertainty band around them.

That is the same trap as running SCS-CN on annual totals (HLD 6.9 measures it:
C = 0.907 against the correct 0.393), reached from a different direction.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass
from typing import Any

import numpy as np

from app.providers.base import ProviderUnavailableError
from app.providers.rainfall import nasa_power, open_meteo
from app.providers.rainfall.base import RainfallStats

log = logging.getLogger(__name__)

#: Sources in preference order. The first that succeeds supplies the daily series
#: SCS-CN runs on, because it is the finest resolution available -- 0.1 deg
#: against POWER's 0.5 x 0.625, which can span several districts.
SOURCES: tuple[tuple[str, Callable[..., RainfallStats]], ...] = (
    ("open_meteo_era5_land", open_meteo.fetch_rainfall),
    ("nasa_power", nasa_power.fetch_rainfall),
)

#: Both sources are fetched at once; neither waits on the other.
FETCH_BUDGET_S = 120.0

#: Above this relative spread the two reanalyses disagree enough that a single
#: figure should not be quoted without the range beside it. 0.20 is not arbitrary:
#: it is roughly the point at which the resulting pond volume differs by more than
#: one season's silt allowance.
NOTABLE_DISAGREEMENT = 0.20


@dataclass(frozen=True)
class RainfallEnsemble:
    """Per-source statistics, and the agreement between them."""

    #: The series SCS-CN should run on: a single source, never a blend.
    primary: RainfallStats
    primary_source: str
    #: Every source that answered, keyed by name.
    members: dict[str, RainfallStats]
    failures: list[dict[str, str]]

    @property
    def annual_means(self) -> dict[str, float]:
        return {name: stats.mean_annual_mm for name, stats in self.members.items()}

    def as_dict(self) -> dict[str, Any]:
        means = self.annual_means
        values = sorted(means.values())
        summary: dict[str, Any] = {
            "primary_source": self.primary_source,
            "primary_reason": (
                "finest resolution available; SCS-CN runs on this source's daily "
                "series unblended, because averaging two reanalyses' daily series "
                "would split single storms across days and understate runoff"
            ),
            "sources": {
                name: {
                    "mean_annual_mm": round(stats.mean_annual_mm, 1),
                    "dependable_75_mm": round(stats.dependable_75_mm, 1),
                    "cv": round(stats.cv, 3),
                    "complete_years": len(stats.years),
                    "resolution": stats.provenance.resolution,
                    "has_temperature": stats.monthly_temp_c is not None,
                }
                for name, stats in sorted(self.members.items())
            },
            "failures": self.failures,
        }

        if len(values) < 2:
            summary["agreement"] = None
            summary["interpretation"] = (
                "only one rainfall source answered, so the figure has no independent "
                "corroboration. The spread between reanalyses is typically 10-20 % "
                "for Indian monsoon rainfall; treat this estimate accordingly."
            )
            return summary

        median = float(np.median(values))
        spread = max(values) - min(values)
        relative = spread / median if median > 0 else 0.0
        summary["ensemble_median_annual_mm"] = round(median, 1)
        summary["inter_source_range_mm"] = [round(min(values), 1), round(max(values), 1)]
        summary["inter_source_spread_mm"] = round(spread, 1)
        summary["inter_source_spread_fraction"] = round(relative, 4)
        # Standard deviation across sources, which is the conventional way to
        # express reanalysis-ensemble uncertainty. With two members it is just
        # half the range, and is reported as such rather than dressed up.
        summary["inter_source_sigma_mm"] = round(float(np.std(values, ddof=1)), 1)
        summary["notable_disagreement"] = relative > NOTABLE_DISAGREEMENT
        summary["interpretation"] = _agreement_note(relative, min(values), max(values), median)
        return summary


def _agreement_note(relative: float, lowest: float, highest: float, median: float) -> str:
    if relative <= 0.10:
        return (
            f"the sources agree to within {relative:.0%} "
            f"({lowest:.0f}-{highest:.0f} mm), which is close for two independent "
            "reanalyses of monsoon rainfall"
        )
    if relative <= NOTABLE_DISAGREEMENT:
        return (
            f"the sources differ by {relative:.0%} ({lowest:.0f}-{highest:.0f} mm, "
            f"median {median:.0f} mm) -- ordinary for reanalyses at different "
            "resolutions, and the range is the uncertainty to carry forward"
        )
    return (
        f"the sources differ by {relative:.0%} ({lowest:.0f}-{highest:.0f} mm), "
        "which is enough that a single figure should not be quoted without the "
        "range beside it. Storage sized on the lower bound is the conservative "
        "choice; cross-check against IMD's gauge-based grid before committing"
    )


def fetch_ensemble(
    lon: float, lat: float, years: int = open_meteo.DEFAULT_YEARS
) -> RainfallEnsemble:
    """Fetch every rainfall source concurrently and report their agreement.

    Concurrent because they are independent services and the slower one should not
    add to the wait -- the same reasoning as `services.enrichment`. A source that
    fails is recorded rather than raising: one reanalysis being down is a reason to
    report less confidence, not to refuse an answer.

    Raises only if *no* source answered, because then there is nothing to report.
    """
    members: dict[str, RainfallStats] = {}
    failures: list[dict[str, str]] = []

    with ThreadPoolExecutor(max_workers=len(SOURCES)) as pool:
        futures = {name: pool.submit(fetch, lon, lat, years) for name, fetch in SOURCES}
        wait(list(futures.values()), timeout=FETCH_BUDGET_S)

        for name, future in futures.items():
            if not future.done():
                failures.append(
                    {
                        "source": name,
                        "reason": f"did not answer within the {FETCH_BUDGET_S:g} s budget",
                    }
                )
                continue
            try:
                members[name] = future.result()
            except ProviderUnavailableError as exc:
                failures.append({"source": name, "reason": exc.detail})
            except Exception as exc:  # a provider bug must not lose the other source
                log.exception("rainfall_source_failed", extra={"source": name})
                failures.append({"source": name, "reason": type(exc).__name__})

    if not members:
        raise ProviderUnavailableError(
            "rainfall_ensemble",
            "no rainfall source answered ("
            + "; ".join(f"{f['source']}: {f['reason']}" for f in failures)
            + ")",
        )

    # Preference order, not "whichever was fastest": the choice of daily series
    # changes the runoff, so it must be deterministic.
    primary_name = next(name for name, _ in SOURCES if name in members)
    return RainfallEnsemble(
        primary=members[primary_name],
        primary_source=primary_name,
        members=members,
        failures=failures,
    )


def temperature_from(ensemble: RainfallEnsemble) -> list[float] | None:
    """Monthly mean temperature from whichever source carries it.

    Only NASA POWER does. Returned separately from the primary series because the
    primary is chosen for rainfall resolution, and the source with the finer
    rainfall is not the one with the temperature -- so Khosla's cross-check would
    otherwise stay unavailable even when a temperature was fetched.
    """
    for name, stats in ensemble.members.items():
        if stats.monthly_temp_c is not None and all(
            np.isfinite(value) for value in stats.monthly_temp_c
        ):
            log.debug("temperature_from", extra={"source": name})
            return list(stats.monthly_temp_c)
    return None
