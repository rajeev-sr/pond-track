"""AHP weight derivation and the consistency check (HLD §6.5.2).

The cases are chosen to be hand-verifiable rather than regression-frozen: a
matrix built from known ratios has known weights, a cyclic judgement must be
refused, and the shipped weight vector has to survive its own audit. That last
one is the point of the exercise -- nine hardcoded numbers are unfalsifiable
until something can show they are coherent.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.services import ahp
from app.services.siting import _ELICITED_PRIORITIES, AHP_WEIGHTS

#: Perfectly consistent by construction: a12=2, a13=4, a23=2, and 2 x 2 = 4.
#: Weights must therefore be (4, 2, 1) / 7.
CONSISTENT_3 = [
    [1.0, 2.0, 4.0],
    [0.5, 1.0, 2.0],
    [0.25, 0.5, 1.0],
]

#: A cycle: A dominates B, B dominates C, and C dominates A. No weight vector can
#: express this, which is exactly what CR exists to detect.
CYCLIC_3 = [
    [1.0, 9.0, 1 / 9],
    [1 / 9, 1.0, 9.0],
    [9.0, 1 / 9, 1.0],
]


class TestTheRandomIndexTable:
    def test_it_matches_the_value_the_hld_fixes(self) -> None:
        assert ahp.RANDOM_INDEX[9] == 1.45

    def test_a_single_comparison_cannot_be_inconsistent(self) -> None:
        # With n <= 2 there is one judgement and nothing for it to contradict,
        # so RI is 0 and the ratio is undefined rather than infinite.
        assert ahp.RANDOM_INDEX[1] == 0.0
        assert ahp.RANDOM_INDEX[2] == 0.0

    def test_it_increases_with_order_over_the_useful_range(self) -> None:
        """More criteria admit more room for random inconsistency."""
        values = [ahp.RANDOM_INDEX[n] for n in range(3, 12)]
        assert values == sorted(values)


class TestAConsistentMatrix:
    def test_weights_are_the_ratios_it_was_built_from(self) -> None:
        d = ahp.derive_weights(("a", "b", "c"), CONSISTENT_3)
        expected = np.array([4.0, 2.0, 1.0]) / 7.0
        assert [d.weights[k] for k in ("a", "b", "c")] == pytest.approx(expected, abs=1e-9)

    def test_consistency_ratio_is_zero(self) -> None:
        d = ahp.derive_weights(("a", "b", "c"), CONSISTENT_3)
        assert d.consistency_ratio == pytest.approx(0.0, abs=1e-9)
        assert d.is_consistent

    def test_lambda_max_equals_n(self) -> None:
        """Equality holds only for a perfectly consistent matrix."""
        d = ahp.derive_weights(("a", "b", "c"), CONSISTENT_3)
        assert d.lambda_max == pytest.approx(3.0, abs=1e-9)

    def test_all_equal_judgements_give_equal_weights(self) -> None:
        d = ahp.derive_weights(("a", "b", "c", "d"), np.ones((4, 4)))
        assert list(d.weights.values()) == pytest.approx([0.25] * 4)
        assert d.consistency_ratio == pytest.approx(0.0, abs=1e-9)

    def test_weights_always_sum_to_one(self) -> None:
        for matrix, names in ((CONSISTENT_3, ("a", "b", "c")), (np.ones((5, 5)), tuple("abcde"))):
            d = ahp.derive_weights(names, matrix)
            assert sum(d.weights.values()) == pytest.approx(1.0)


class TestAnInconsistentMatrixIsRefused:
    def test_a_cycle_is_rejected(self) -> None:
        with pytest.raises(ahp.InconsistentMatrixError) as exc:
            ahp.derive_weights(("a", "b", "c"), CYCLIC_3)
        assert exc.value.consistency_ratio >= ahp.MAX_CONSISTENCY_RATIO

    def test_the_error_reports_the_number_and_the_threshold(self) -> None:
        """A refusal that does not say how far off it was cannot be acted on."""
        with pytest.raises(ahp.InconsistentMatrixError, match=r"CR = \d\.\d+"):
            ahp.derive_weights(("a", "b", "c"), CYCLIC_3)

    def test_it_can_be_measured_without_being_enforced(self) -> None:
        d = ahp.derive_weights(("a", "b", "c"), CYCLIC_3, require_consistent=False)
        assert not d.is_consistent
        assert d.consistency_ratio > 1.0, "a full cycle should be wildly inconsistent"

    def test_the_threshold_is_saaty_s(self) -> None:
        assert ahp.MAX_CONSISTENCY_RATIO == 0.10


class TestMatrixValidation:
    def test_a_non_square_matrix_is_refused(self) -> None:
        with pytest.raises(ValueError, match="square"):
            ahp.derive_weights(("a", "b"), [[1.0, 2.0, 3.0], [0.5, 1.0, 2.0]])

    def test_a_size_mismatch_with_the_names_is_refused(self) -> None:
        with pytest.raises(ValueError, match="but 2 criteria were named"):
            ahp.derive_weights(("a", "b"), CONSISTENT_3)

    def test_a_non_reciprocal_entry_names_the_cell(self) -> None:
        bad = [[1.0, 2.0, 4.0], [0.5, 1.0, 2.0], [0.25, 3.0, 1.0]]
        with pytest.raises(ValueError, match=r"reciprocal.*a\[1\]\[2\]|a\[2\]\[1\]"):
            ahp.derive_weights(("a", "b", "c"), bad)

    def test_a_diagonal_that_is_not_one_is_refused(self) -> None:
        bad = np.array(CONSISTENT_3)
        bad[1, 1] = 2.0
        with pytest.raises(ValueError, match="diagonal"):
            ahp.derive_weights(("a", "b", "c"), bad)

    def test_a_comparison_off_the_saaty_scale_is_refused(self) -> None:
        bad = [[1.0, 20.0], [0.05, 1.0]]
        with pytest.raises(ValueError, match="Saaty scale"):
            ahp.derive_weights(("a", "b"), bad)

    def test_a_zero_or_negative_comparison_is_refused(self) -> None:
        with pytest.raises(ValueError, match="strictly positive"):
            ahp.derive_weights(("a", "b"), [[1.0, 0.0], [0.0, 1.0]])

    def test_a_single_criterion_cannot_be_compared(self) -> None:
        with pytest.raises(ValueError, match="at least two"):
            ahp.derive_weights(("a",), [[1.0]])

    def test_an_order_with_no_published_random_index_is_refused(self) -> None:
        """Inventing an RI would invent the threshold the check depends on."""
        n = max(ahp.RANDOM_INDEX) + 1
        with pytest.raises(ValueError, match="Random Index"):
            ahp.derive_weights(tuple(f"c{i}" for i in range(n)), np.ones((n, n)))

    def test_duplicate_criterion_names_are_refused(self) -> None:
        with pytest.raises(ValueError, match="unique"):
            ahp.derive_weights(("a", "a"), [[1.0, 2.0], [0.5, 1.0]])

    def test_a_reciprocal_typed_as_a_decimal_is_accepted(self) -> None:
        """An expert entering 0.333 rather than 1/3 should not be rejected."""
        d = ahp.derive_weights(("a", "b"), [[1.0, 3.0], [0.333, 1.0]])
        assert d.weights["a"] > d.weights["b"]


class TestTheTwoMethodsAgree:
    def test_they_match_on_a_consistent_matrix(self) -> None:
        d = ahp.derive_weights(("a", "b", "c"), CONSISTENT_3)
        assert d.method_agreement < 1e-9

    def test_agreement_is_reported_even_when_the_matrix_is_incoherent(self) -> None:
        """The two methods agreeing does not certify the matrix.

        A symmetric cycle is circulant, so both methods return exactly equal
        weights and agree perfectly while CR is above 6. Agreement is a
        cross-check on the *arithmetic*, not evidence of consistency -- CR is the
        only thing that measures that, which is why both are reported.
        """
        bad = ahp.derive_weights(("a", "b", "c"), CYCLIC_3, require_consistent=False)
        assert bad.method_agreement < 1e-9
        assert bad.consistency_ratio > 1.0

    def test_they_diverge_on_a_lopsided_inconsistent_matrix(self) -> None:
        """Where the inconsistency is not symmetric, the methods do part company."""
        lopsided = [
            [1.0, 9.0, 5.0],
            [1 / 9, 1.0, 7.0],
            [1 / 5, 1 / 7, 1.0],
        ]
        d = ahp.derive_weights(("a", "b", "c"), lopsided, require_consistent=False)
        assert not d.is_consistent
        assert d.method_agreement > 1e-3


class TestTheSaatyScale:
    def test_it_is_one_to_nine_and_their_reciprocals(self) -> None:
        assert len(ahp.SAATY_VALUES) == 17, "1..9 plus eight reciprocals; 1 is shared"
        assert min(ahp.SAATY_VALUES) == pytest.approx(1 / 9)
        assert max(ahp.SAATY_VALUES) == 9.0

    def test_rounding_happens_in_log_space(self) -> None:
        """Linear rounding would collapse most sub-unit ratios onto 1/9.

        3.5 sits between 3 and 4; in log space it is nearer 4 (3.5/3 = 1.167
        against 4/3.5 = 1.143). And 0.4 lands on 1/3, not on 1/2: the log
        distances are 0.183 and 0.223 respectively. Linear rounding would give
        1/2 for 0.4 and would collapse most sub-unit ratios onto 1/9.
        """
        assert ahp.nearest_saaty(3.5) == 4.0
        assert ahp.nearest_saaty(0.4) == pytest.approx(1 / 3)
        assert ahp.nearest_saaty(0.45) == pytest.approx(1 / 2)
        assert ahp.nearest_saaty(1.0) == 1.0

    def test_it_is_symmetric_under_inversion(self) -> None:
        for ratio in (1.3, 2.7, 5.0, 8.9):
            assert ahp.nearest_saaty(1 / ratio) == pytest.approx(1 / ahp.nearest_saaty(ratio))

    def test_a_ratio_beyond_the_scale_saturates(self) -> None:
        assert ahp.nearest_saaty(100.0) == 9.0
        assert ahp.nearest_saaty(0.001) == pytest.approx(1 / 9)

    def test_a_nonsense_ratio_is_refused(self) -> None:
        for bad in (0.0, -2.0, float("nan"), float("inf")):
            with pytest.raises(ValueError):
                ahp.nearest_saaty(bad)


class TestTheShippedWeightsSurviveTheirOwnAudit:
    """★ The reason M6-7 exists.

    `AHP_WEIGHTS` is nine hardcoded numbers attributed to IMSD practice. This
    reconstructs the pairwise matrix they imply, snaps every entry to the Saaty
    scale an expert would actually have used, and re-derives the weights from it.
    Passing means the vector encodes a coherent set of judgements rather than
    nine plausible-looking constants.
    """

    def test_the_matrix_it_implies_is_consistent(self) -> None:
        matrix = ahp.matrix_from_weights(AHP_WEIGHTS)
        d = ahp.derive_weights(tuple(AHP_WEIGHTS), matrix, require_consistent=False)
        assert (
            d.is_consistent
        ), f"the shipped weights are incoherent: CR = {d.consistency_ratio:.4f}"
        assert d.consistency_ratio < 0.02, (
            f"CR = {d.consistency_ratio:.4f}: still under Saaty's 0.10, but far "
            "enough above the measured 0.0091 that the weights have drifted"
        )

    def test_re_derivation_recovers_the_weights(self) -> None:
        matrix = ahp.matrix_from_weights(AHP_WEIGHTS)
        d = ahp.derive_weights(tuple(AHP_WEIGHTS), matrix, require_consistent=False)
        for name, shipped in AHP_WEIGHTS.items():
            assert d.weights[name] == pytest.approx(
                shipped, abs=0.02
            ), f"{name}: shipped {shipped:.4f}, re-derived {d.weights[name]:.4f}"

    def test_the_ranking_is_preserved_exactly(self) -> None:
        """Absolute weights may shift with rounding; the ordering may not."""
        matrix = ahp.matrix_from_weights(AHP_WEIGHTS)
        d = ahp.derive_weights(tuple(AHP_WEIGHTS), matrix, require_consistent=False)
        shipped_order = sorted(AHP_WEIGHTS, key=lambda k: (-AHP_WEIGHTS[k], k))
        derived_order = sorted(d.weights, key=lambda k: (-d.weights[k], k))
        # plan_concavity and distance_to_stream are tied at 0.08 in the shipped
        # vector, so compare on the values rather than on the tie-broken names.
        assert [round(AHP_WEIGHTS[k], 2) for k in shipped_order] == [
            round(AHP_WEIGHTS[k], 2) for k in derived_order
        ]

    def test_the_reconstruction_stays_on_the_saaty_scale(self) -> None:
        matrix = ahp.matrix_from_weights(AHP_WEIGHTS)
        # Would raise if any entry fell off the scale or broke reciprocity.
        ahp.validate_matrix(matrix)
        for value in matrix.flatten():
            assert any(abs(value - s) < 1e-9 for s in ahp.SAATY_VALUES), value

    def test_the_exported_vector_sums_to_one(self) -> None:
        """FR-9 requires it, and the number in a report must be the number used."""
        assert sum(AHP_WEIGHTS.values()) == pytest.approx(1.0)

    def test_the_elicited_table_is_preserved_even_though_it_sums_to_1_05(self) -> None:
        """The 1.05 was an arithmetic slip in the original table.

        It is kept as elicited rather than edited, because choosing which
        criterion was meant to be 0.05 lower would be inventing expert intent.
        Normalising is ratio-preserving, so no judgement, score or ranking moves.
        """
        assert sum(_ELICITED_PRIORITIES.values()) == pytest.approx(1.05)
        assert AHP_WEIGHTS["flow_accumulation"] == pytest.approx(0.21 / 1.05)

    def test_normalising_preserved_every_ratio(self) -> None:
        names = list(_ELICITED_PRIORITIES)
        for i, a in enumerate(names):
            for b in names[i + 1 :]:
                assert AHP_WEIGHTS[a] / AHP_WEIGHTS[b] == pytest.approx(
                    _ELICITED_PRIORITIES[a] / _ELICITED_PRIORITIES[b]
                ), f"{a}:{b} ratio moved"


class TestTheAuditTravelsWithTheAnswer:
    def test_the_response_block_carries_the_numbers(self) -> None:
        d = ahp.derive_weights(("a", "b", "c"), CONSISTENT_3)
        block = d.as_dict()
        assert block["consistency"]["is_consistent"] is True
        assert block["consistency"]["threshold"] == 0.10
        assert block["consistency"]["random_index"] == 0.58
        assert block["n"] == 3
        assert set(block["weights"]) == {"a", "b", "c"}
        assert "max_abs_difference" in block["cross_check"]
