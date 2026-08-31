"""Monthly water balance for a designed pond (FR-13, M10-5).

Capacity answers "how much can it hold". A village asks something else: **will
there be water in April?** That needs the year simulated month by month, because
a pond in central India fills over four monsoon months and then loses water
continuously to evaporation and seepage through eight dry ones.

    storage(t) = storage(t-1) + inflow(t) - evaporation(t) - seepage(t)

with overflow above capacity spilling downstream and storage floored at zero.

Three modelling decisions are worth stating plainly, because each is a choice a
reader could reasonably disagree with:

* **Evaporation is scaled from reference ET.** The providers give ET0 -- the
  demand of a short grass reference crop. Open water evaporates faster, so ET0 is
  multiplied by `OPEN_WATER_COEFFICIENT`. This is the standard FAO-56 treatment,
  and the coefficient is exposed rather than buried.
* **Seepage is the largest uncertainty by far**, and it is a property of the pond
  bed rather than of the catchment. The rates below are indicative for unlined
  ponds by hydrologic soil group; a lined pond is a different structure. They are
  order-of-magnitude figures, not a design standard, and the result reports them
  so nobody mistakes the output for a measurement.
* **Evaporating area shrinks as the pond empties.** Using the full top area all
  year would over-state losses badly -- a pond at a fifth of capacity exposes far
  less than a fifth of its surface, because the sides slope inward. The area is
  taken from the pond's own prismoidal geometry at the current depth.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
DAYS_IN_MONTH = (31, 28.25, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)

#: Open water against the grass reference ET0 (FAO-56 treats a free water surface
#: as roughly 1.05 of reference demand for a shallow body).
OPEN_WATER_COEFFICIENT = 1.05

#: Indicative seepage through an unlined bed, mm/day, by hydrologic soil group.
#: The spread is two orders of magnitude, which is exactly why the figure is
#: reported alongside the answer: on sand the pond is a sieve, on clay it holds.
SEEPAGE_MM_PER_DAY: dict[str, float] = {
    "A": 25.0,  # sand -- an unlined pond here loses more to the ground than to the sky
    "B": 12.0,
    "C": 6.0,
    "D": 2.0,  # clay
}
DEFAULT_SEEPAGE_MM_PER_DAY = 6.0

#: Below this fraction of live storage the pond is not usefully supplying anyone,
#: even though it is not literally dry -- the last of it is silt-laden and warm.
USABLE_FRACTION = 0.10

#: Years to run before reporting, so the answer is the pond's repeating annual
#: cycle rather than an artefact of starting it empty.
SPIN_UP_YEARS = 3


@dataclass(frozen=True)
class MonthState:
    month: str
    inflow_m3: float
    evaporation_m3: float
    seepage_m3: float
    spill_m3: float
    storage_m3: float
    water_depth_m: float
    surface_area_m2: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "month": self.month,
            "inflow_m3": round(self.inflow_m3, 1),
            "evaporation_m3": round(self.evaporation_m3, 1),
            "seepage_m3": round(self.seepage_m3, 1),
            "spill_m3": round(self.spill_m3, 1),
            "storage_m3": round(self.storage_m3, 1),
            "water_depth_m": round(self.water_depth_m, 2),
            "surface_area_m2": round(self.surface_area_m2, 1),
        }


@dataclass(frozen=True)
class WaterBalance:
    months: tuple[MonthState, ...]
    capacity_m3: float
    #: First month the pond falls below usefully usable storage, or None.
    dry_month: str | None
    #: Months per year holding usable water.
    months_with_water: int
    reliability_pct: float
    peak_storage_m3: float
    total_spill_m3: float
    annual_evaporation_m3: float
    annual_seepage_m3: float
    seepage_mm_per_day: float
    soil_group: str | None
    open_water_coefficient: float
    assumptions: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "months": [m.as_dict() for m in self.months],
            "capacity_m3": round(self.capacity_m3, 1),
            "peak_storage_m3": round(self.peak_storage_m3, 1),
            "peak_fill_pct": (
                round(100.0 * self.peak_storage_m3 / self.capacity_m3, 1)
                if self.capacity_m3
                else 0.0
            ),
            "dry_month": self.dry_month,
            "months_with_water": self.months_with_water,
            "reliability_pct": round(self.reliability_pct, 1),
            "annual_losses_m3": {
                "evaporation": round(self.annual_evaporation_m3, 1),
                "seepage": round(self.annual_seepage_m3, 1),
                "spill": round(self.total_spill_m3, 1),
            },
            "parameters": {
                "seepage_mm_per_day": self.seepage_mm_per_day,
                "hydrologic_soil_group": self.soil_group,
                "open_water_coefficient": self.open_water_coefficient,
                "usable_fraction": USABLE_FRACTION,
                "spin_up_years": SPIN_UP_YEARS,
            },
            "assumptions": list(self.assumptions),
        }


def _geometry(
    bottom_length_m: float, bottom_width_m: float, depth_m: float, side_slope: float
) -> tuple[Any, Any]:
    """`(area_at, volume_at)` for a truncated pyramid, as functions of water depth.

    Measured from the *bottom* up, which is what a filling pond does. The plan
    dimensions grow by `2 * z * d` as the water rises, so both area and volume are
    strongly non-linear near empty -- the reason a shrinking evaporating surface
    matters.
    """

    def area_at(d: float) -> float:
        d = max(0.0, min(d, depth_m))
        return (bottom_length_m + 2.0 * side_slope * d) * (bottom_width_m + 2.0 * side_slope * d)

    def volume_at(d: float) -> float:
        d = max(0.0, min(d, depth_m))
        if d <= 0:
            return 0.0
        a_bottom = bottom_length_m * bottom_width_m
        a_top = area_at(d)
        # Prismoidal, as the design itself uses.
        return (d / 3.0) * (a_top + a_bottom + math.sqrt(a_top * a_bottom))

    return area_at, volume_at


def _depth_for_volume(volume_m3: float, depth_m: float, volume_at: Any) -> float:
    """Invert the volume curve by bisection; it is monotonic, so this converges."""
    if volume_m3 <= 0:
        return 0.0
    if volume_m3 >= volume_at(depth_m):
        return depth_m
    lo, hi = 0.0, depth_m
    for _ in range(40):
        mid = (lo + hi) / 2.0
        if volume_at(mid) < volume_m3:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def simulate(
    *,
    monthly_runoff_mm: list[float],
    catchment_area_m2: float,
    monthly_et0_mm: list[float] | None,
    bottom_length_m: float,
    bottom_width_m: float,
    depth_m: float,
    side_slope: float,
    capacity_m3: float,
    soil_group: str | None = None,
    seepage_mm_per_day: float | None = None,
    open_water_coefficient: float = OPEN_WATER_COEFFICIENT,
) -> WaterBalance:
    """Run the monthly balance to its repeating annual cycle.

    Raises `ValueError` when evaporation cannot be estimated: a water balance
    without evaporation is not a conservative answer, it is a wrong one, and
    silently dropping the term would report a pond holding water it would not.
    """
    if len(monthly_runoff_mm) != 12:
        raise ValueError(f"need 12 monthly runoff values, got {len(monthly_runoff_mm)}")
    if not monthly_et0_mm or len(monthly_et0_mm) != 12:
        raise ValueError(
            "monthly reference evapotranspiration is required: without it the "
            "balance would omit the largest dry-season loss and overstate how "
            "long the pond holds water"
        )
    if depth_m <= 0 or capacity_m3 <= 0:
        raise ValueError("the pond has no depth or no capacity to simulate")

    seepage = (
        seepage_mm_per_day
        if seepage_mm_per_day is not None
        else SEEPAGE_MM_PER_DAY.get((soil_group or "").upper(), DEFAULT_SEEPAGE_MM_PER_DAY)
    )
    area_at, volume_at = _geometry(bottom_length_m, bottom_width_m, depth_m, side_slope)

    storage = 0.0
    states: list[MonthState] = []
    for _year in range(SPIN_UP_YEARS):
        states = []
        for index in range(12):
            inflow = monthly_runoff_mm[index] / 1000.0 * catchment_area_m2
            water_depth = _depth_for_volume(storage, depth_m, volume_at)
            area = area_at(water_depth) if storage > 0 else 0.0
            days = DAYS_IN_MONTH[index]

            evaporation = monthly_et0_mm[index] * open_water_coefficient / 1000.0 * area
            seep = seepage * days / 1000.0 * area

            storage += inflow
            spill = max(0.0, storage - capacity_m3)
            storage = min(storage, capacity_m3)
            # Losses are applied after inflow and capped at what is there, so the
            # balance cannot go negative and then "recover" from a debt.
            losses = min(storage, evaporation + seep)
            if evaporation + seep > 0:
                share = losses / (evaporation + seep)
                evaporation *= share
                seep *= share
            storage = max(0.0, storage - losses)

            final_depth = _depth_for_volume(storage, depth_m, volume_at)
            states.append(
                MonthState(
                    month=MONTHS[index],
                    inflow_m3=inflow,
                    evaporation_m3=evaporation,
                    seepage_m3=seep,
                    spill_m3=spill,
                    storage_m3=storage,
                    water_depth_m=final_depth,
                    surface_area_m2=area_at(final_depth) if storage > 0 else 0.0,
                )
            )

    usable = USABLE_FRACTION * capacity_m3
    with_water = sum(1 for m in states if m.storage_m3 > usable)

    # The dry month is the first *after the peak* that falls below usable, which
    # is the question being asked -- "when does it run out" -- rather than the
    # January of a pond that has not filled yet.
    peak_index = max(range(12), key=lambda i: states[i].storage_m3)
    dry_month = None
    for offset in range(1, 13):
        state = states[(peak_index + offset) % 12]
        if state.storage_m3 <= usable:
            dry_month = state.month
            break

    assumptions = (
        f"Evaporation is reference ET0 x {open_water_coefficient} for an open water surface.",
        f"Seepage {seepage:g} mm/day through an unlined bed"
        + (f", indicative for hydrologic soil group {soil_group}." if soil_group else "."),
        "Seepage is the largest uncertainty here and is a property of the bed, "
        "not the catchment; a lined pond is a different structure.",
        "The evaporating surface shrinks with the water level, following the "
        "pond's own side slopes.",
        f"Run for {SPIN_UP_YEARS} years so the result is the repeating annual "
        "cycle rather than an artefact of starting empty.",
        "Mean monthly runoff is used, so this is an average year -- not a "
        "drought year, and not a design storm.",
    )

    return WaterBalance(
        months=tuple(states),
        capacity_m3=capacity_m3,
        dry_month=dry_month,
        months_with_water=with_water,
        reliability_pct=100.0 * with_water / 12.0,
        peak_storage_m3=max(m.storage_m3 for m in states),
        total_spill_m3=sum(m.spill_m3 for m in states),
        annual_evaporation_m3=sum(m.evaporation_m3 for m in states),
        annual_seepage_m3=sum(m.seepage_m3 for m in states),
        seepage_mm_per_day=seepage,
        soil_group=soil_group,
        open_water_coefficient=open_water_coefficient,
        assumptions=assumptions,
    )
