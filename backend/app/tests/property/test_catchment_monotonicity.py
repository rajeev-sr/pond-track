"""Catchment area can only grow as the pour point moves downstream (M3-10).

An invariant of D8 routing rather than a property of any particular terrain: the
contributing area of a cell is the union of its own and every cell upstream of
it, so stepping one cell downhill can add area but never remove it. If it ever
shrinks, the flow network is not a tree -- a cycle, a mis-signed neighbour
offset, or an accumulation that disagrees with the directions it was built from.

That failure is invisible in a single catchment: one wrong area looks exactly
like a correct one. It only shows as a violated relationship, which is why this
is a property test and not a value test.
"""

from __future__ import annotations

import numpy as np
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from app.providers.elevation.base import DemGrid
from app.services import hydrology as hyd
from app.services.hydrology import NEIGHBOURS

CELL = 10.0
SIZE = 48
TRANSFORM = (CELL, 0.0, 530000.0, 0.0, -CELL, 2352000.0)

#: ESRI code -> (d_row, d_col), for walking the flow network downstream.
STEP = {code: (dr, dc) for dr, dc, code, _ in NEIGHBOURS}


def dem_from(surface: np.ndarray) -> DemGrid:
    return DemGrid(
        elevation=surface.astype(np.float32),
        transform=TRANSFORM,
        epsg=32644,
        cell_size_m=CELL,
    )


def rolling_terrain(seed: int, roughness: float) -> np.ndarray:
    """A smooth random surface with a general fall, so water has somewhere to go.

    Smoothed rather than white noise: an unsmoothed random field is almost all
    pits, so the conditioning dominates and the test stops exercising routing.
    """
    from scipy import ndimage

    rng = np.random.default_rng(seed)
    rows, cols = np.mgrid[0:SIZE, 0:SIZE].astype(float)
    trend = 100.0 - 0.08 * rows * CELL / 10.0 - 0.03 * cols * CELL / 10.0
    noise = ndimage.gaussian_filter(rng.normal(0.0, roughness, (SIZE, SIZE)), sigma=3.0)
    return trend + noise


def walk_downstream(
    flow: hyd.FlowGrids, start: tuple[int, int], steps: int
) -> list[tuple[int, int]]:
    """The cells visited following D8 from `start`, at most `steps` of them."""
    rows, cols = flow.direction.shape
    path = [start]
    cursor = start
    seen = {start}
    for _ in range(steps):
        offset = STEP.get(int(flow.direction[cursor]))
        if offset is None:
            break
        nxt = (cursor[0] + offset[0], cursor[1] + offset[1])
        if not (0 <= nxt[0] < rows and 0 <= nxt[1] < cols) or nxt in seen:
            break
        path.append(nxt)
        seen.add(nxt)
        cursor = nxt
    return path


@settings(
    max_examples=12,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    seed=st.integers(min_value=0, max_value=10_000),
    roughness=st.floats(min_value=0.5, max_value=6.0),
)
def test_area_never_shrinks_moving_downstream(seed: int, roughness: float) -> None:
    dem = dem_from(rolling_terrain(seed, roughness))
    conditioned = hyd.fill_depressions(dem)
    flow = hyd.build_flow(dem, conditioned)

    # Try several starts rather than one. A single `pytest.skip` inside a
    # Hypothesis test aborts the whole property run, so a test that skips on its
    # first awkward example quietly stops testing anything -- which is what
    # happened here on the first attempt.
    upper = flow.accumulation.copy()
    upper[int(SIZE * 0.6) :, :] = -1
    candidates = np.argsort(upper.ravel())[::-1][:12]

    path: list[tuple[int, int]] = []
    for index in candidates:
        start = (int(index) // SIZE, int(index) % SIZE)
        candidate_path = walk_downstream(flow, start, steps=12)
        if len(candidate_path) >= 4:
            path = candidate_path
            break
    assume(len(path) >= 4)

    areas = [
        hyd.delineate_catchment(dem, flow, row, col, snap_radius_cells=0).area_m2
        for row, col in path
    ]
    for index in range(len(path) - 1):
        upstream_area, downstream_area = areas[index], areas[index + 1]
        assert downstream_area >= upstream_area - 1e-6, (
            f"area shrank from {upstream_area:.0f} m2 at {path[index]} to "
            f"{downstream_area:.0f} m2 at {path[index + 1]} -- the flow network "
            "is not a tree"
        )


@settings(max_examples=8, deadline=None)
@given(seed=st.integers(min_value=0, max_value=10_000))
def test_accumulation_agrees_with_the_delineated_area(seed: int) -> None:
    """Two independent counts of the same thing.

    `flow_accumulation` counts contributing cells by topological sweep;
    `delineate_catchment` counts them by reverse traversal from the outlet. They
    are different algorithms over the same graph, so agreement is evidence about
    both -- and a mis-signed neighbour offset breaks them differently.
    """
    dem = dem_from(rolling_terrain(seed, 3.0))
    conditioned = hyd.fill_depressions(dem)
    flow = hyd.build_flow(dem, conditioned)

    index = int(np.argmax(flow.accumulation))
    row, col = index // SIZE, index % SIZE
    catchment = hyd.delineate_catchment(dem, flow, row, col, snap_radius_cells=0)

    assert catchment.cell_count == int(flow.accumulation[row, col]), (
        f"accumulation says {int(flow.accumulation[row, col])} cells drain to "
        f"({row}, {col}); delineation found {catchment.cell_count}"
    )
