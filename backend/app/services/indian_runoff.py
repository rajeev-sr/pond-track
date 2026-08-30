"""Indian empirical runoff formulae, as cross-checks on SCS-CN (M4-11).

HLD CH-15 is the reason this exists. **SCS-CN is a US model** calibrated on
American watersheds, and it under-performs for Indian monsoon regimes -- a single
storm dropping a third of the annual total behaves nothing like the temperate
rainfall the curve numbers were fitted to. The pipeline already applies the
Indian corrections (`Ia = 0.3S` per CWC/IMD rather than the US 0.2S, AMC
adjustment, daily series rather than annual totals), but a corrected US model is
still a US model.

So the answer is cross-checked against formulae derived from *Indian* gauged
catchments. They are old, coarse, and regional -- and that is the point: they were
fitted to the monsoon, on rivers a few hundred kilometres from where this tool
will be used. Where they and SCS-CN agree, the estimate is worth more. Where they
diverge, the spread is the honest uncertainty and is reported as such.

Implemented here, with the forms as HLD 6.6 states them:

* **Inglis & DeSouza (1929)** -- 53 stream-gauging sites in Western India.
* **Khosla (1960)** -- a monthly water balance; needs mean monthly temperature.
* **Barlow (1912)** -- Uttar Pradesh catchments, a coefficient by catchment class.
* **Rational method** -- peak flow rather than volume, for spillway sizing.

**Strange (1928) is deliberately not implemented.** It is a *tabulation*, not a
formula -- runoff as a percentage of monsoon rainfall, listed against rainfall for
Good / Average / Bad catchment character -- and the table has to come from Strange
or a standard text that reproduces it. Writing down values from memory and
presenting them as a cross-check would be worse than having no cross-check: it
would lend false confidence to the number it was checking. `strange` therefore
reports what it needs rather than returning a figure. See `STRANGE_REQUIREMENT`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

log = logging.getLogger(__name__)

Region = Literal["deccan", "western_ghats", "gangetic", "general"]
CatchmentClass = Literal["flat_cultivated", "flat_partly_cultivated", "average", "hilly_barren"]

#: What is needed before Strange's method can be offered. Stated in the response
#: rather than silently omitted, so the gap is visible to whoever reads it.
STRANGE_REQUIREMENT = (
    "Strange (1928) is a tabulation of runoff as a percentage of monsoon rainfall "
    "for Good / Average / Bad catchment character, not a closed-form expression. "
    "It needs the published table, from Strange's original or a standard Indian "
    "irrigation text that reproduces it (Garg, Modi, or the CWC manuals). Until "
    "those values are entered from a citable source they are not offered: an "
    "unverified cross-check lends false confidence to the figure it is checking."
)

#: Barlow's runoff coefficient K by catchment class, for Uttar Pradesh
#: catchments. These are the four classes Barlow (1912) distinguished; the
#: coefficient is applied to the *monsoon* rainfall.
BARLOW_K: dict[CatchmentClass, float] = {
    "flat_cultivated": 0.07,
    "flat_partly_cultivated": 0.12,
    "average": 0.16,
    "hilly_barren": 0.36,
}

#: Khosla's monthly loss coefficient: L = 0.48 * T, T in degrees Celsius.
KHOSLA_LOSS_PER_DEGREE = 0.48

#: A catchment runoff coefficient above this is physically implausible outside
#: paved ground, and any method reporting one is being applied outside its range.
#: Khosla's method reaches it routinely in monsoon India: its loss term is
#: 0.48 x T, about 14 mm for a 30 C month, against monsoon rainfall of 300-400 mm
#: -- so nearly all of it is counted as runoff, while actual monthly
#: evapotranspiration in central India runs 100-200 mm. The estimate is still
#: reported, because the formula is what the method *is*, but it is flagged and
#: excluded from the comparison range rather than dragging it upward.
IMPLAUSIBLE_RUNOFF_COEFFICIENT = 0.75

#: How far the SCS-CN figure may sit outside the empirical range and still count
#: as agreement, as a fraction of that range's bounds.
#:
#: A tolerance rather than strict containment, because containment is meaningless
#: when only one method could be evaluated -- the "range" is then a single point
#: and nothing lands inside it. A quarter is not arbitrary either: these formulae
#: are regional fits from the 1910s-1930s applied outside their own catchments, so
#: agreement to within a quarter is as much as either family of model can claim.
AGREEMENT_TOLERANCE = 0.25

#: Below this monthly mean temperature Khosla's linear loss term stops being
#: meaningful -- it was fitted on Indian monthly means, which do not go near
#: freezing in the plains, and a negative loss would manufacture runoff.
KHOSLA_MIN_TEMP_C = 0.0


@dataclass(frozen=True)
class MethodResult:
    """One method's estimate, or why it could not produce one."""

    method: str
    #: Runoff depth over the catchment, in millimetres. None when unavailable.
    runoff_mm: float | None
    runoff_coefficient: float | None
    applicable: bool
    #: Why this method applies here, or why it does not.
    note: str
    reference: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "runoff_mm": None if self.runoff_mm is None else round(self.runoff_mm, 1),
            "runoff_coefficient": (
                None if self.runoff_coefficient is None else round(self.runoff_coefficient, 4)
            ),
            "applicable": self.applicable,
            "note": self.note,
            "reference": self.reference,
        }


def region_for(state: str | None) -> Region:
    """Which regional method set applies, per HLD 6.6.

    A coarse mapping on purpose: these formulae were fitted regionally and the
    HLD assigns them by region, so the choice is a lookup rather than a judgement.
    An unrecognised state falls to `general`, which is the honest answer -- none of
    the regional fits demonstrably applies.
    """
    if not state:
        return "general"
    name = state.strip().lower()
    if any(key in name for key in ("maharashtra", "karnataka")):
        return "deccan"
    if any(key in name for key in ("goa", "konkan", "kerala")):
        return "western_ghats"
    if any(key in name for key in ("uttar pradesh", "bihar", "haryana", "punjab", "delhi")):
        return "gangetic"
    return "general"


def inglis_desouza(
    annual_rainfall_mm: float, *, terrain: Literal["ghat", "plains"] = "plains"
) -> MethodResult:
    """Inglis & DeSouza (1929), from 53 stream-gauging sites in Western India.

    Stated in centimetres in the original and here converted, because the
    constants are not dimensionless::

        ghat areas : R = 0.85 P - 30.5
        plains     : R = (P - 17.8) * P / 254        (R, P in cm/year)

    The plains form is quadratic in P, so it must not be extrapolated far past
    the rainfall range it was fitted on -- at very high P it exceeds the rainfall
    itself, which the guard below catches.
    """
    p_cm = annual_rainfall_mm / 10.0
    if terrain == "ghat":
        r_cm = 0.85 * p_cm - 30.5
        note = "Western Ghats form; fitted on high-rainfall ghat catchments"
    else:
        r_cm = (p_cm - 17.8) * p_cm / 254.0
        note = "plains form; quadratic in rainfall, so not for extrapolation"

    if r_cm <= 0:
        return MethodResult(
            method="inglis_desouza",
            runoff_mm=0.0,
            runoff_coefficient=0.0,
            applicable=True,
            note=(
                f"{note}. At {annual_rainfall_mm:.0f} mm the formula gives no runoff: "
                "below its fitted range."
            ),
            reference="Inglis, C.C. & DeSouza, A. (1929), Runoff formulae for Western India",
        )

    runoff_mm = r_cm * 10.0
    coefficient = runoff_mm / annual_rainfall_mm if annual_rainfall_mm > 0 else None
    if coefficient is not None and coefficient > 1.0:
        return MethodResult(
            method="inglis_desouza",
            runoff_mm=None,
            runoff_coefficient=None,
            applicable=False,
            note=(
                f"{note}. At {annual_rainfall_mm:.0f} mm the quadratic exceeds the "
                "rainfall itself, which means it is being extrapolated beyond its "
                "fitted range. Not reported."
            ),
            reference="Inglis, C.C. & DeSouza, A. (1929), Runoff formulae for Western India",
        )

    return MethodResult(
        method="inglis_desouza",
        runoff_mm=runoff_mm,
        runoff_coefficient=coefficient,
        applicable=True,
        note=note,
        reference="Inglis, C.C. & DeSouza, A. (1929), Runoff formulae for Western India",
    )


def khosla(monthly_rainfall_mm: list[float], monthly_temp_c: list[float] | None) -> MethodResult:
    """Khosla (1960): a monthly water balance, `R_m = P_m - L_m`, `L_m = 0.48 T_m`.

    The loss term is a linear function of temperature and is floored at zero -- a
    cold month cannot produce negative loss, and allowing it would manufacture
    runoff out of the arithmetic. Months where loss exceeds rainfall contribute
    nothing rather than a negative, for the same reason.

    Returns unavailable without temperature, which is the common case here: the
    rainfall provider supplies precipitation and reference evapotranspiration but
    not mean temperature. Reporting that plainly is better than substituting a
    climatological guess and presenting the result as a measurement.
    """
    reference = "Khosla, A.N. (1960), Appraisal of water resources"
    if monthly_temp_c is None or len(monthly_temp_c) != len(monthly_rainfall_mm):
        return MethodResult(
            method="khosla",
            runoff_mm=None,
            runoff_coefficient=None,
            applicable=False,
            note=(
                "needs mean monthly temperature, which the rainfall source does not "
                "currently supply. Khosla's loss term is 0.48 x T, so there is no "
                "way to evaluate it without T."
            ),
            reference=reference,
        )

    total_rain = sum(monthly_rainfall_mm)
    runoff = 0.0
    for rain, temp in zip(monthly_rainfall_mm, monthly_temp_c, strict=True):
        loss = KHOSLA_LOSS_PER_DEGREE * max(temp, KHOSLA_MIN_TEMP_C)
        runoff += max(0.0, rain - loss)

    coefficient = runoff / total_rain if total_rain > 0 else None
    note = (
        "monthly water balance; loss floored at zero so a dry or cold month "
        "contributes nothing rather than negative runoff"
    )
    plausible = coefficient is None or coefficient <= IMPLAUSIBLE_RUNOFF_COEFFICIENT
    if not plausible:
        note = (
            f"{note}. The coefficient comes out at {coefficient:.2f}, which is not "
            "physically plausible for a rural catchment: Khosla's loss term is "
            "0.48 x T, roughly 14 mm for a 30 C month, against monsoon rainfall of "
            "300-400 mm -- while actual monthly evapotranspiration in central India "
            "runs 100-200 mm. The method is known to over-predict in high-rainfall "
            "regimes and is reported here but excluded from the comparison range"
        )
    return MethodResult(
        method="khosla",
        runoff_mm=runoff,
        runoff_coefficient=coefficient,
        # Not `applicable`: the number is real but it should not widen the range
        # the SCS-CN figure is judged against.
        applicable=plausible,
        note=note,
        reference=reference,
    )


def barlow(
    monsoon_rainfall_mm: float, *, catchment_class: CatchmentClass = "average"
) -> MethodResult:
    """Barlow (1912): `R = K P`, K by catchment class, on Uttar Pradesh catchments.

    Applied to the *monsoon* rainfall rather than the annual total, which is what
    Barlow tabulated. The coefficient spans 0.07 for flat cultivated land to 0.36
    for hilly barren -- a factor of five, which is why the class matters more than
    the rainfall does.
    """
    coefficient = BARLOW_K[catchment_class]
    return MethodResult(
        method="barlow",
        runoff_mm=coefficient * monsoon_rainfall_mm,
        runoff_coefficient=coefficient,
        applicable=True,
        note=(
            f"K = {coefficient} for {catchment_class.replace('_', ' ')}; applied to "
            "monsoon rainfall, as Barlow tabulated it. Fitted on Uttar Pradesh "
            "catchments, so it is a weaker check further from the Gangetic plain"
        ),
        reference="Barlow (1912), runoff coefficients for UP catchments",
    )


def strange() -> MethodResult:
    """Strange (1928) -- not implemented, and reports why.

    See the module docstring and `STRANGE_REQUIREMENT`. Strange's method is a
    table, not a formula, and the table has to come from a citable source.
    """
    return MethodResult(
        method="strange",
        runoff_mm=None,
        runoff_coefficient=None,
        applicable=False,
        note=STRANGE_REQUIREMENT,
        reference="Strange, W.L. (1928), Runoff tables for Deccan catchments",
    )


def rational_peak_m3s(
    catchment_area_ha: float,
    rainfall_intensity_mm_per_h: float,
    runoff_coefficient: float,
) -> float:
    """Rational method peak discharge, `Q = C i A / 360` with A in hectares.

    Peak flow rather than volume: it sizes a spillway, not a pond. The 360 is the
    unit conversion that makes Q come out in cubic metres per second for i in
    mm/h and A in hectares -- the formula is often quoted with 3.6 and A in km2,
    which is the same thing.

    The intensity should be for a storm of duration equal to the time of
    concentration, which is what `catchment_metrics` computes by Kirpich.
    """
    if catchment_area_ha < 0 or rainfall_intensity_mm_per_h < 0:
        raise ValueError("area and intensity must be non-negative")
    if not 0.0 <= runoff_coefficient <= 1.0:
        raise ValueError(f"runoff coefficient must be in [0, 1], got {runoff_coefficient}")
    return runoff_coefficient * rainfall_intensity_mm_per_h * catchment_area_ha / 360.0


@dataclass(frozen=True)
class CrossCheck:
    """SCS-CN against whichever Indian methods apply, and the spread."""

    region: Region
    scs_cn_runoff_mm: float
    methods: list[MethodResult]

    @property
    def comparable(self) -> list[MethodResult]:
        return [m for m in self.methods if m.applicable and m.runoff_mm is not None]

    def as_dict(self) -> dict[str, Any]:
        usable = [m.runoff_mm for m in self.comparable if m.runoff_mm is not None]
        summary: dict[str, Any] = {
            "region": self.region,
            "scs_cn_runoff_mm": round(self.scs_cn_runoff_mm, 1),
            "methods": [m.as_dict() for m in self.methods],
            "comparable_methods": len(usable),
        }
        # Methods that produced a figure but were excluded from the range, so the
        # reader can see there was an answer and why it is not being counted.
        excluded = [
            m.as_dict() for m in self.methods if not m.applicable and m.runoff_mm is not None
        ]
        if excluded:
            summary["reported_but_excluded"] = excluded

        if not usable:
            summary["agreement"] = None
            summary["interpretation"] = (
                "no Indian empirical method could be evaluated here, so the SCS-CN "
                "figure stands uncorroborated. See each method's note for what it "
                "would need."
            )
            return summary

        lowest, highest = min(usable), max(usable)
        # Widened by the tolerance, so a single comparable method still yields a
        # band rather than a point. Strict containment would report disagreement
        # for a figure 1 % away from the only number available to compare it to.
        floor = lowest * (1.0 - AGREEMENT_TOLERANCE)
        ceiling = highest * (1.0 + AGREEMENT_TOLERANCE)
        agrees = floor <= self.scs_cn_runoff_mm <= ceiling

        summary["empirical_range_mm"] = [round(lowest, 1), round(highest, 1)]
        summary["agreement_band_mm"] = [round(floor, 1), round(ceiling, 1)]
        summary["agreement_tolerance"] = AGREEMENT_TOLERANCE
        summary["agrees_with_empirical"] = agrees
        summary["spread_mm"] = round(highest - lowest, 1)
        # The ratio to the nearest bound, which is the number a reader wants when
        # the answer is "no": how far off, not merely that it is off.
        nearest = lowest if self.scs_cn_runoff_mm < lowest else highest
        summary["ratio_to_nearest_empirical"] = (
            None if nearest <= 0 else round(self.scs_cn_runoff_mm / nearest, 3)
        )
        summary["interpretation"] = _agreement_note(self.scs_cn_runoff_mm, lowest, highest, agrees)
        return summary


def _agreement_note(scs: float, lowest: float, highest: float, agrees: bool) -> str:
    band = f"{lowest:.0f} mm" if abs(highest - lowest) < 1.0 else f"{lowest:.0f}-{highest:.0f} mm"
    if agrees:
        return (
            f"the SCS-CN estimate of {scs:.0f} mm agrees with the Indian empirical "
            f"figure of {band} to within {AGREEMENT_TOLERANCE:.0%}, which is as close "
            "as regional fits from the 1910s-1930s applied outside their own "
            "catchments can be asked to come"
        )
    if scs > highest:
        ratio = scs / highest if highest > 0 else float("inf")
        return (
            f"the SCS-CN estimate of {scs:.0f} mm is {ratio:.1f}x the highest Indian "
            f"empirical figure ({highest:.0f} mm). SCS-CN is known to over-predict "
            "for monsoon regimes (HLD CH-15); treat the empirical range as the "
            "conservative case for sizing"
        )
    ratio = lowest / scs if scs > 0 else float("inf")
    return (
        f"the SCS-CN estimate of {scs:.0f} mm is {ratio:.1f}x *below* the lowest "
        f"Indian empirical figure ({lowest:.0f} mm), which is the less common "
        "direction -- check the curve number and the antecedent moisture class "
        "before relying on either"
    )


def cross_check(
    *,
    scs_cn_runoff_mm: float,
    annual_rainfall_mm: float,
    monsoon_rainfall_mm: float,
    monthly_rainfall_mm: list[float] | None = None,
    monthly_temp_c: list[float] | None = None,
    state: str | None = None,
    terrain: Literal["ghat", "plains"] = "plains",
    catchment_class: CatchmentClass = "average",
) -> CrossCheck:
    """Run the Indian methods that apply to this region and compare.

    Which methods run is decided by region, per HLD 6.6: Deccan gets Strange,
    the Western Ghats get the Inglis-DeSouza ghat form, the Gangetic plain gets
    Barlow, and anywhere else gets Khosla plus Strange. Every method is *reported*
    either way -- one that does not apply says so, rather than being silently
    absent, because "no cross-check was available" and "the cross-check agreed"
    are very different statements about a number.
    """
    region = region_for(state)
    methods: list[MethodResult] = []

    if region == "deccan":
        methods.append(strange())
        methods.append(inglis_desouza(annual_rainfall_mm, terrain="plains"))
    elif region == "western_ghats":
        methods.append(inglis_desouza(annual_rainfall_mm, terrain="ghat"))
        methods.append(strange())
    elif region == "gangetic":
        methods.append(barlow(monsoon_rainfall_mm, catchment_class=catchment_class))
        methods.append(khosla(monthly_rainfall_mm or [], monthly_temp_c))
    else:
        methods.append(khosla(monthly_rainfall_mm or [], monthly_temp_c))
        methods.append(strange())
        # Inglis-DeSouza's plains form is the only closed-form volume method that
        # needs nothing but annual rainfall, so it is offered generally with the
        # caveat that it was fitted in Western India.
        result = inglis_desouza(annual_rainfall_mm, terrain=terrain)
        methods.append(
            MethodResult(
                method=result.method,
                runoff_mm=result.runoff_mm,
                runoff_coefficient=result.runoff_coefficient,
                applicable=result.applicable,
                note=result.note + "; fitted in Western India, so indicative here",
                reference=result.reference,
            )
        )

    return CrossCheck(region=region, scs_cn_runoff_mm=scs_cn_runoff_mm, methods=methods)
