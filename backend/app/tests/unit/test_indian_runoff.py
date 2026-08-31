"""The Indian empirical runoff formulae (M4-11).

Each closed-form method is checked against the arithmetic HLD 6.6 states, because
these are the cross-checks the SCS-CN figure is judged against -- a wrong constant
here would not fail loudly, it would quietly certify a wrong runoff volume.

The forms, from HLD 6.6:

* Inglis & DeSouza, ghat:   R = 0.85 P - 30.5        (R, P in cm/year)
* Inglis & DeSouza, plains: R = (P - 17.8) P / 254   (R, P in cm/year)
* Khosla:                   R_m = P_m - 0.48 T_m     (mm, degrees C)
* Barlow:                   R = K P                  (K by catchment class)
* Rational:                 Q = C i A / 360          (m3/s, mm/h, hectares)
"""

from __future__ import annotations

import pytest

from app.services.indian_runoff import (
    AGREEMENT_TOLERANCE,
    BARLOW_K,
    IMPLAUSIBLE_RUNOFF_COEFFICIENT,
    barlow,
    cross_check,
    inglis_desouza,
    khosla,
    rational_peak_m3s,
    region_for,
    strange,
)

#: A monsoon-shaped year: most of the rain in June-September.
MONTHLY_RAIN = [12.0, 15.0, 20.0, 25.0, 30.0, 180.0, 380.0, 360.0, 210.0, 60.0, 15.0, 8.0]
MONTHLY_TEMP = [21.0, 24.0, 29.0, 33.0, 35.0, 32.0, 28.0, 27.0, 28.0, 26.0, 22.0, 19.0]


class TestInglisDeSouza:
    @pytest.mark.parametrize("rainfall_mm", [500.0, 1000.0, 1313.0, 2000.0])
    def test_the_plains_form_matches_the_stated_formula(self, rainfall_mm: float) -> None:
        p_cm = rainfall_mm / 10.0
        expected_mm = (p_cm - 17.8) * p_cm / 254.0 * 10.0
        result = inglis_desouza(rainfall_mm, terrain="plains")
        assert result.runoff_mm == pytest.approx(expected_mm, rel=1e-9)

    @pytest.mark.parametrize("rainfall_mm", [1000.0, 2000.0, 3000.0])
    def test_the_ghat_form_matches_the_stated_formula(self, rainfall_mm: float) -> None:
        p_cm = rainfall_mm / 10.0
        expected_mm = (0.85 * p_cm - 30.5) * 10.0
        result = inglis_desouza(rainfall_mm, terrain="ghat")
        assert result.runoff_mm == pytest.approx(expected_mm, rel=1e-9)

    def test_the_two_forms_differ(self) -> None:
        """They are separate fits; using one for the other is a real error."""
        plains = inglis_desouza(1500.0, terrain="plains").runoff_mm
        ghat = inglis_desouza(1500.0, terrain="ghat").runoff_mm
        assert plains is not None and ghat is not None
        assert ghat > plains, "the ghat form should give more runoff at the same rainfall"

    def test_rainfall_below_the_fitted_range_gives_no_runoff(self) -> None:
        """The ghat form is negative below 359 mm, which means nothing physical."""
        result = inglis_desouza(200.0, terrain="ghat")
        assert result.runoff_mm == 0.0
        assert "below its fitted range" in result.note

    def test_extrapolation_past_the_fitted_range_is_refused(self) -> None:
        """The plains form is quadratic, so at extreme rainfall it exceeds the
        rainfall itself -- which is arithmetic, not hydrology."""
        result = inglis_desouza(50_000.0, terrain="plains")
        assert result.applicable is False
        assert result.runoff_mm is None
        assert "beyond its fitted range" in result.note

    def test_the_coefficient_never_exceeds_one_when_reported(self) -> None:
        for rainfall in (300.0, 800.0, 1500.0, 3000.0, 6000.0):
            for terrain in ("plains", "ghat"):
                result = inglis_desouza(rainfall, terrain=terrain)  # type: ignore[arg-type]
                if result.runoff_coefficient is not None:
                    assert 0.0 <= result.runoff_coefficient <= 1.0, (rainfall, terrain)


class TestBarlow:
    def test_it_multiplies_the_monsoon_rainfall_by_k(self) -> None:
        for catchment_class, k in BARLOW_K.items():
            result = barlow(1000.0, catchment_class=catchment_class)
            assert result.runoff_mm == pytest.approx(k * 1000.0)
            assert result.runoff_coefficient == pytest.approx(k)

    def test_the_tolerance_is_a_documented_constant(self) -> None:
        assert 0.0 < AGREEMENT_TOLERANCE < 1.0

    def test_the_class_matters_more_than_the_rainfall(self) -> None:
        """K spans 0.07 to 0.36 -- a factor of five across catchment character."""
        flat = barlow(1000.0, catchment_class="flat_cultivated").runoff_mm
        hilly = barlow(1000.0, catchment_class="hilly_barren").runoff_mm
        assert flat is not None and hilly is not None
        assert hilly / flat == pytest.approx(0.36 / 0.07, rel=1e-9)

    def test_every_coefficient_is_a_plausible_fraction(self) -> None:
        assert all(0.0 < k < 1.0 for k in BARLOW_K.values())


class TestKhosla:
    def test_it_is_a_monthly_balance_with_the_stated_loss_term(self) -> None:
        expected = sum(
            max(0.0, rain - 0.48 * temp)
            for rain, temp in zip(MONTHLY_RAIN, MONTHLY_TEMP, strict=True)
        )
        assert khosla(MONTHLY_RAIN, MONTHLY_TEMP).runoff_mm == pytest.approx(expected)

    def test_a_month_losing_more_than_it_gains_contributes_nothing(self) -> None:
        """Not a negative. Allowing one would manufacture runoff arithmetically."""
        result = khosla([1.0], [40.0])
        assert result.runoff_mm == 0.0

    def test_a_negative_temperature_cannot_manufacture_runoff(self) -> None:
        """The loss term is floored, so cold does not become a rainfall bonus."""
        cold = khosla([100.0], [-20.0]).runoff_mm
        zero = khosla([100.0], [0.0]).runoff_mm
        assert cold == zero == pytest.approx(100.0)

    def test_without_temperature_it_reports_what_it_needs(self) -> None:
        """Rather than substituting a climatological guess and calling it a
        measurement."""
        result = khosla(MONTHLY_RAIN, None)
        assert result.applicable is False
        assert result.runoff_mm is None
        assert "temperature" in result.note

    def test_mismatched_series_lengths_are_refused(self) -> None:
        result = khosla(MONTHLY_RAIN, MONTHLY_TEMP[:6])
        assert result.applicable is False

    def test_an_implausible_coefficient_is_flagged_and_not_counted(self) -> None:
        """On monsoon rainfall Khosla's loss term is far too small -- about 14 mm
        for a 30 C month against 380 mm of rain -- so it reports a coefficient no
        rural catchment has. The figure is still returned; it is excluded from
        the comparison range rather than dragging it upward.
        """
        result = khosla(MONTHLY_RAIN, MONTHLY_TEMP)
        assert result.runoff_coefficient is not None
        assert result.runoff_coefficient > IMPLAUSIBLE_RUNOFF_COEFFICIENT
        assert result.applicable is False
        assert result.runoff_mm is not None, "the estimate should still be reported"
        assert "not physically plausible" in result.note


class TestStrange:
    def test_it_reports_what_it_needs_instead_of_a_number(self) -> None:
        """A tabulation, not a formula. Values written from memory and presented
        as a cross-check would lend false confidence to the figure being checked.
        """
        result = strange()
        assert result.applicable is False
        assert result.runoff_mm is None
        assert "tabulation" in result.note
        assert "citable source" in result.note


class TestRationalMethod:
    def test_it_matches_the_stated_formula(self) -> None:
        assert rational_peak_m3s(264.26, 40.0, 0.254) == pytest.approx(
            0.254 * 40.0 * 264.26 / 360.0
        )

    def test_the_unit_constant_is_right_for_hectares(self) -> None:
        """C=1, i=360 mm/h, A=1 ha must give exactly 1 m3/s."""
        assert rational_peak_m3s(1.0, 360.0, 1.0) == pytest.approx(1.0)

    def test_it_scales_linearly_in_all_three_inputs(self) -> None:
        base = rational_peak_m3s(100.0, 20.0, 0.3)
        assert rational_peak_m3s(200.0, 20.0, 0.3) == pytest.approx(2 * base)
        assert rational_peak_m3s(100.0, 40.0, 0.3) == pytest.approx(2 * base)
        assert rational_peak_m3s(100.0, 20.0, 0.6) == pytest.approx(2 * base)

    @pytest.mark.parametrize(
        ("area", "intensity", "coefficient"),
        [(-1.0, 20.0, 0.3), (100.0, -1.0, 0.3), (100.0, 20.0, 1.5), (100.0, 20.0, -0.1)],
    )
    def test_impossible_inputs_are_refused(
        self, area: float, intensity: float, coefficient: float
    ) -> None:
        with pytest.raises(ValueError):
            rational_peak_m3s(area, intensity, coefficient)


class TestRegionalDispatch:
    @pytest.mark.parametrize(
        ("state", "expected"),
        [
            ("Maharashtra", "deccan"),
            ("Karnataka", "deccan"),
            ("Kerala", "western_ghats"),
            ("Goa", "western_ghats"),
            ("Uttar Pradesh", "gangetic"),
            ("Bihar", "gangetic"),
            ("Chhattisgarh", "general"),
            ("Tamil Nadu", "general"),
            (None, "general"),
            ("", "general"),
        ],
    )
    def test_the_mapping_follows_the_hld(self, state: str | None, expected: str) -> None:
        assert region_for(state) == expected

    def test_it_is_case_and_whitespace_tolerant(self) -> None:
        assert region_for("  MAHARASHTRA  ") == "deccan"


class TestTheCrossCheck:
    def base(self, **overrides: object) -> dict[str, object]:
        payload: dict[str, object] = {
            "scs_cn_runoff_mm": 361.8,
            "annual_rainfall_mm": 1312.8,
            "monsoon_rainfall_mm": 1167.9,
            "monthly_rainfall_mm": MONTHLY_RAIN,
            "monthly_temp_c": None,
            "state": "Chhattisgarh",
        }
        payload.update(overrides)
        return payload

    def test_every_method_is_reported_whether_it_applied_or_not(self) -> None:
        """ "No cross-check was available" and "the cross-check agreed" are very
        different statements about a number, so a method that does not apply says
        so rather than being silently absent."""
        report = cross_check(**self.base()).as_dict()  # type: ignore[arg-type]
        assert len(report["methods"]) >= 2
        assert any(not m["applicable"] for m in report["methods"])
        for method in report["methods"]:
            assert method["note"], method["method"]
            assert method["reference"], method["method"]

    def test_it_says_whether_scs_cn_agrees_with_the_empirical_figure(self) -> None:
        report = cross_check(**self.base()).as_dict()  # type: ignore[arg-type]
        assert isinstance(report["agrees_with_empirical"], bool)
        assert report["interpretation"]
        assert report["ratio_to_nearest_empirical"] is not None

    def test_agreement_is_a_band_not_strict_containment(self) -> None:
        """With one comparable method the "range" is a single point, so strict
        containment would report disagreement for a figure 1 % away from the only
        number available to compare against."""
        report = cross_check(**self.base()).as_dict()  # type: ignore[arg-type]
        low, high = report["empirical_range_mm"]
        band_low, band_high = report["agreement_band_mm"]
        assert band_low < low <= high < band_high

    def test_an_agreeing_estimate_is_reported_as_agreeing(self) -> None:
        """Inglis-DeSouza plains gives 586 mm at 1313 mm of rainfall."""
        report = cross_check(**self.base(scs_cn_runoff_mm=580.0)).as_dict()  # type: ignore[arg-type]
        assert report["agrees_with_empirical"] is True
        assert "agrees" in report["interpretation"]

    def test_an_over_prediction_names_the_known_bias(self) -> None:
        """HLD CH-15: SCS-CN over-predicts for monsoon regimes."""
        report = cross_check(**self.base(scs_cn_runoff_mm=1200.0)).as_dict()  # type: ignore[arg-type]
        assert report["agrees_with_empirical"] is False
        assert "over-predict" in report["interpretation"]
        assert report["ratio_to_nearest_empirical"] > 1.5

    def test_an_excluded_method_is_still_visible(self) -> None:
        report = cross_check(  # type: ignore[arg-type]
            **self.base(monthly_temp_c=MONTHLY_TEMP)
        ).as_dict()
        assert "reported_but_excluded" in report
        assert any(m["method"] == "khosla" for m in report["reported_but_excluded"])

    def test_the_gangetic_region_uses_barlow(self) -> None:
        report = cross_check(**self.base(state="Uttar Pradesh")).as_dict()  # type: ignore[arg-type]
        assert any(m["method"] == "barlow" for m in report["methods"])

    def test_the_western_ghats_use_the_ghat_form(self) -> None:
        report = cross_check(**self.base(state="Kerala")).as_dict()  # type: ignore[arg-type]
        methods = {m["method"] for m in report["methods"]}
        assert "inglis_desouza" in methods
        assert report["region"] == "western_ghats"

    def test_with_nothing_evaluable_it_says_the_figure_stands_alone(self) -> None:
        """Rather than implying corroboration that did not happen."""
        report = cross_check(  # type: ignore[arg-type]
            **self.base(state="Maharashtra", annual_rainfall_mm=50.0)
        ).as_dict()
        if report["comparable_methods"] == 0:
            assert "uncorroborated" in report["interpretation"]
