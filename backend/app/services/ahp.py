"""Analytic Hierarchy Process: weights from pairwise judgements (HLD §6.5.2, M6-7).

The suitability model has always carried a weight vector. What it has not carried
is any way to check that vector against the judgements it claims to encode, which
makes nine numbers between 0.05 and 0.21 unfalsifiable -- a reader can disagree
with them but cannot show they are *incoherent*.

AHP fixes exactly that. An expert compares criteria in pairs on Saaty's 1-9
scale; the principal eigenvector of the comparison matrix is the weight vector;
and the Consistency Ratio measures whether the comparisons contradict each other.
Judging A twice as important as B, B twice as important as C, and C more
important than A is a contradiction, and CR is what detects it. Saaty's threshold
is CR < 0.10, and the HLD requires an inconsistent matrix to be refused rather
than quietly averaged into something plausible-looking.

Two methods are implemented on purpose. The eigenvector is the definition; the
column-normalised row-mean is the hand-calculation from every textbook. They
agree closely on a consistent matrix and diverge as consistency degrades, so
`WeightDerivation.method_agreement` is a free second opinion on the same matrix.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import numpy.typing as npt

#: Saaty's Random Index: the mean CI of randomly generated reciprocal matrices of
#: each order. CR compares an expert's inconsistency against that baseline, which
#: is why a 3x3 matrix is allowed far less absolute inconsistency than a 9x9 one.
#: n=9 is 1.45, matching HLD §6.5.2.
RANDOM_INDEX: dict[int, float] = {
    1: 0.00,
    2: 0.00,
    3: 0.58,
    4: 0.90,
    5: 1.12,
    6: 1.24,
    7: 1.32,
    8: 1.41,
    9: 1.45,
    10: 1.49,
    11: 1.51,
    12: 1.48,
    13: 1.56,
    14: 1.57,
    15: 1.59,
}

#: Saaty (1980). CR at or above this means the judgements contradict each other
#: badly enough that the derived weights are not worth having.
MAX_CONSISTENCY_RATIO = 0.10

#: The scale an expert may use: 1..9 and their reciprocals.
SAATY_VALUES: tuple[float, ...] = tuple(
    sorted({*(float(i) for i in range(1, 10)), *(1.0 / i for i in range(1, 10))})
)

#: Reciprocity and the diagonal are checked to this tolerance, so a matrix typed
#: as 0.333 rather than 1/3 is accepted.
TOLERANCE = 5e-3


class InconsistentMatrixError(ValueError):
    """CR >= 0.10. Carries the numbers so the caller can report them (HTTP 400)."""

    def __init__(self, consistency_ratio: float, n: int) -> None:
        super().__init__(
            f"the pairwise judgements are inconsistent: CR = {consistency_ratio:.3f}, "
            f"which is not below {MAX_CONSISTENCY_RATIO:.2f}. Revise the comparisons "
            "-- typically one triple contradicts the others (if A>B and B>C then A "
            "must outrank C by at least as much)."
        )
        self.consistency_ratio = consistency_ratio
        self.n = n


@dataclass(frozen=True)
class WeightDerivation:
    """Weights from one comparison matrix, with the audit that justifies them."""

    criteria: tuple[str, ...]
    weights: dict[str, float]
    lambda_max: float
    consistency_index: float
    consistency_ratio: float
    random_index: float
    #: Max absolute difference between the eigenvector and the row-mean method.
    method_agreement: float
    is_consistent: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "weights": {k: round(v, 4) for k, v in self.weights.items()},
            "consistency": {
                "lambda_max": round(self.lambda_max, 5),
                "consistency_index": round(self.consistency_index, 5),
                "consistency_ratio": round(self.consistency_ratio, 5),
                "random_index": self.random_index,
                "threshold": MAX_CONSISTENCY_RATIO,
                "is_consistent": self.is_consistent,
            },
            "cross_check": {
                "method": "column-normalised row mean",
                "max_abs_difference": round(self.method_agreement, 5),
                "note": (
                    "The eigenvector is the definition; the row-mean is the "
                    "textbook hand calculation. They agree on a consistent "
                    "matrix, so a large difference here is a second signal that "
                    "the judgements are strained."
                ),
            },
            "n": len(self.criteria),
        }


def validate_matrix(matrix: npt.NDArray[np.float64], *, n: int | None = None) -> None:
    """Reject anything that is not a Saaty reciprocal matrix, and say why.

    Every failure here is a 400: the request is well formed but the matrix is not
    a set of pairwise comparisons.
    """
    if matrix.ndim != 2 or matrix.shape[0] != matrix.shape[1]:
        raise ValueError(f"the comparison matrix must be square; got shape {matrix.shape}")
    size = matrix.shape[0]
    if n is not None and size != n:
        raise ValueError(f"matrix is {size}x{size} but {n} criteria were named")
    if size < 2:
        raise ValueError("at least two criteria are needed to compare anything")
    if size > max(RANDOM_INDEX):
        raise ValueError(
            f"no published Random Index for n={size}; the largest tabulated is "
            f"{max(RANDOM_INDEX)}. Group the criteria into a hierarchy instead."
        )
    if not np.all(np.isfinite(matrix)):
        raise ValueError("the matrix contains a non-finite entry")
    if np.any(matrix <= 0.0):
        raise ValueError("every comparison must be strictly positive")

    if not np.allclose(np.diag(matrix), 1.0, atol=TOLERANCE):
        raise ValueError("the diagonal must be 1: a criterion is equal to itself")

    # a_ji must be 1/a_ij. Compared as a ratio rather than a difference because
    # the entries span two orders of magnitude, so a fixed tolerance would be
    # lax at 9 and impossibly strict at 1/9.
    product = matrix * matrix.T
    if not np.allclose(product, 1.0, atol=1e-2):
        bad = np.argwhere(np.abs(product - 1.0) > 1e-2)
        i, j = (int(v) for v in bad[0])
        raise ValueError(
            f"entries must be reciprocal: a[{i}][{j}] = {matrix[i, j]:.4g} implies "
            f"a[{j}][{i}] = {1 / matrix[i, j]:.4g}, but it is {matrix[j, i]:.4g}"
        )

    lo, hi = min(SAATY_VALUES) - TOLERANCE, max(SAATY_VALUES) + TOLERANCE
    if np.any(matrix < lo) or np.any(matrix > hi):
        raise ValueError(
            f"comparisons must lie on the Saaty scale, between 1/9 and 9; "
            f"the matrix spans {matrix.min():.4g} to {matrix.max():.4g}"
        )


def row_mean_weights(matrix: npt.NDArray[np.float64]) -> npt.NDArray[np.float64]:
    """The textbook approximation: normalise each column, average each row."""
    normalised = matrix / matrix.sum(axis=0, keepdims=True)
    return np.asarray(normalised.mean(axis=1), dtype=np.float64)


def principal_eigenvector(
    matrix: npt.NDArray[np.float64],
) -> tuple[npt.NDArray[np.float64], float]:
    """`(weights, lambda_max)` from the dominant eigenpair, weights summing to 1.

    A positive reciprocal matrix has a real positive dominant eigenvalue and a
    positive eigenvector (Perron-Frobenius), but `numpy.linalg.eig` returns
    complex arrays and an arbitrary sign and scale, so the result is made real,
    positive and normalised here rather than at each call site.
    """
    values, vectors = np.linalg.eig(matrix)
    dominant = int(np.argmax(values.real))
    lambda_max = float(values[dominant].real)
    vector = np.abs(vectors[:, dominant].real)
    total = vector.sum()
    if total <= 0 or not math.isfinite(total):
        raise ValueError("the comparison matrix has no usable principal eigenvector")
    return np.asarray(vector / total, dtype=np.float64), lambda_max


def derive_weights(
    criteria: tuple[str, ...] | list[str],
    matrix: npt.NDArray[np.float64] | list[list[float]],
    *,
    require_consistent: bool = True,
) -> WeightDerivation:
    """Weights and the consistency audit for one pairwise comparison matrix.

    Raises `InconsistentMatrixError` when CR >= 0.10 unless `require_consistent`
    is False, which exists so a caller can *report* how inconsistent a matrix is
    instead of only being told that it is.
    """
    names = tuple(criteria)
    if len(set(names)) != len(names):
        raise ValueError("criterion names must be unique")
    a = np.asarray(matrix, dtype=np.float64)
    validate_matrix(a, n=len(names))

    weights, lambda_max = principal_eigenvector(a)
    n = len(names)

    # lambda_max >= n always, with equality only for a perfectly consistent
    # matrix. Floating point can put it a hair below, which would make CI
    # negative and read as "better than perfect".
    consistency_index = max(0.0, (lambda_max - n) / (n - 1))
    random_index = RANDOM_INDEX[n]
    # n <= 2 cannot be inconsistent: there is only one comparison to make, so RI
    # is 0 and the ratio is undefined rather than infinite.
    consistency_ratio = 0.0 if random_index == 0.0 else consistency_index / random_index
    consistent = consistency_ratio < MAX_CONSISTENCY_RATIO

    if require_consistent and not consistent:
        raise InconsistentMatrixError(consistency_ratio, n)

    agreement = float(np.max(np.abs(weights - row_mean_weights(a))))
    return WeightDerivation(
        criteria=names,
        weights={name: float(w) for name, w in zip(names, weights, strict=True)},
        lambda_max=lambda_max,
        consistency_index=consistency_index,
        consistency_ratio=consistency_ratio,
        random_index=random_index,
        method_agreement=agreement,
        is_consistent=consistent,
    )


def nearest_saaty(ratio: float) -> float:
    """The Saaty-scale value closest to a raw ratio, in log space.

    Log space because the scale is multiplicative: 9 is as far from 4.5 as 1/9 is
    from 1/4.5, and a linear nearest-value search would round almost everything
    below 1 to 1/9.
    """
    if not math.isfinite(ratio) or ratio <= 0:
        raise ValueError(f"ratio must be finite and positive; got {ratio}")
    target = math.log(ratio)
    return min(SAATY_VALUES, key=lambda v: abs(math.log(v) - target))


def matrix_from_weights(weights: dict[str, float]) -> npt.NDArray[np.float64]:
    """Reconstruct the pairwise matrix a set of weights implies, on the Saaty scale.

    This is what makes a hardcoded weight vector auditable. `a_ij = w_i / w_j`
    alone is perfectly consistent and therefore proves nothing -- it is the
    weights restated. Rounding each entry to the nearest scale value is the
    honest reconstruction: it is a matrix an expert could actually have typed,
    and re-deriving weights from it recovers the original vector only if that
    vector was coherent to begin with. The rounding is what gives the exercise
    teeth, and it is why the CR that comes back is greater than zero.
    """
    names = list(weights)
    values = np.array([weights[k] for k in names], dtype=np.float64)
    if np.any(values <= 0):
        raise ValueError("every weight must be positive")
    n = len(names)
    a = np.ones((n, n), dtype=np.float64)
    for i in range(n):
        for j in range(i + 1, n):
            snapped = nearest_saaty(float(values[i] / values[j]))
            a[i, j] = snapped
            a[j, i] = 1.0 / snapped
    return a
