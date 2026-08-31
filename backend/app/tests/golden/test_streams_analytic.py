"""Stream extraction and Strahler ordering, on networks built by hand (M3-3).

The flow grids here are written out cell by cell, so the answer is not a matter
of opinion: a Y junction of two headwaters is second order, a tributary joining a
larger channel does not promote it, and the trunk length is a countable number of
cell steps. Getting Strahler wrong produces a network that looks entirely
plausible and mis-ranks every site on it.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from app.services.hydrology import FlowGrids
from app.services.streams import (
    StreamExtractionError,
    extract,
    threshold_cells_for,
)

CELL = 10.0
#: 10 m cells, north-up, origin at (0, 100) so row 0 is the northern edge.
TRANSFORM = (CELL, 0.0, 0.0, 0.0, -CELL, 100.0)

# ESRI D8: E=1 SE=2 S=4 SW=8 W=16 NW=32 N=64 NE=128
E, SE, S, SW, W, NW, N, NE = 1, 2, 4, 8, 16, 32, 64, 128


def grid(paths: dict[tuple[int, int], int], shape: tuple[int, int]) -> FlowGrids:
    """Build flow grids from an explicit cell -> direction map.

    Accumulation is derived by walking each cell downstream, so it is consistent
    with the directions by construction rather than by hand-counting -- an
    inconsistent fixture would test the fixture.
    """
    direction = np.zeros(shape, dtype=np.uint8)
    valid = np.zeros(shape, dtype=bool)
    for (row, col), code in paths.items():
        direction[row, col] = code
        valid[row, col] = True

    offsets = {
        E: (0, 1),
        SE: (1, 1),
        S: (1, 0),
        SW: (1, -1),
        W: (0, -1),
        NW: (-1, -1),
        N: (-1, 0),
        NE: (-1, 1),
    }
    accumulation = np.zeros(shape, dtype=np.int32)
    for cell in paths:
        cursor = cell
        seen = set()
        while cursor is not None and cursor not in seen:
            seen.add(cursor)
            accumulation[cursor] += 1
            code = int(direction[cursor])
            if code == 0:
                break
            dr, dc = offsets[code]
            nxt = (cursor[0] + dr, cursor[1] + dc)
            cursor = nxt if nxt in paths else None
    return FlowGrids(direction=direction, accumulation=accumulation, valid=valid)


def net(flow: FlowGrids, **kwargs: object):
    return extract(flow, transform=TRANSFORM, cell_size_m=CELL, **kwargs)  # type: ignore[arg-type]


#: Small enough to include every cell that carries any flow at all.
EVERYTHING = {"threshold_ha": 0.0001}


class TestAStraightChannel:
    FLOW = grid({(r, 3): S for r in range(6)} | {(6, 3): 0}, (8, 7))

    def test_it_is_one_first_order_stream(self) -> None:
        result = net(self.FLOW, **EVERYTHING)
        assert len(result.reaches) == 1
        assert result.reaches[0].order == 1
        assert result.max_order == 1

    def test_its_length_is_the_number_of_steps(self) -> None:
        """Seven cells, six steps, 10 m each."""
        result = net(self.FLOW, **EVERYTHING)
        assert result.reaches[0].length_m == pytest.approx(6 * CELL)

    def test_the_coordinates_are_cell_centres_running_south(self) -> None:
        result = net(self.FLOW, **EVERYTHING)
        ys = [y for _, y in result.reaches[0].coordinates]
        assert ys == sorted(ys, reverse=True), "the line should run north to south"
        assert ys[0] == pytest.approx(TRANSFORM[5] - CELL * 0.5)


class TestAYJunction:
    """Two headwaters meeting. The classic Strahler case."""

    FLOW = grid(
        {(0, 1): S, (1, 1): S, (2, 1): SE, (3, 2): E}
        | {(0, 5): S, (1, 5): S, (2, 5): SW, (3, 4): W}
        | {(3, 3): S, (4, 3): S, (5, 3): S, (6, 3): S, (7, 3): 0},
        (8, 7),
    )

    def test_two_equal_orders_promote(self) -> None:
        result = net(self.FLOW, **EVERYTHING)
        assert result.max_order == 2

    def test_there_are_two_headwaters_and_one_trunk(self) -> None:
        result = net(self.FLOW, **EVERYTHING)
        by_order = {}
        for reach in result.reaches:
            by_order.setdefault(reach.order, []).append(reach)
        assert len(by_order[1]) == 2
        assert len(by_order[2]) == 1

    def test_the_arms_include_the_diagonal_step(self) -> None:
        """Three orthogonal steps and one diagonal: 30 + 14.14 m."""
        result = net(self.FLOW, **EVERYTHING)
        arms = [r for r in result.reaches if r.order == 1]
        expected = 3 * CELL + CELL * math.sqrt(2.0)
        for arm in arms:
            assert arm.length_m == pytest.approx(expected, rel=1e-6)

    def test_the_junction_cell_belongs_to_both(self) -> None:
        """Otherwise the map shows a one-cell gap at every confluence."""
        result = net(self.FLOW, **EVERYTHING)
        arms = [r for r in result.reaches if r.order == 1]
        (trunk,) = [r for r in result.reaches if r.order == 2]
        for arm in arms:
            assert arm.cells[-1] == trunk.cells[0] == (3, 3)


class TestAnUnequalConfluence:
    """A first-order tributary joining a second-order channel.

    Strahler does not promote here, and that is the entire point of the measure:
    it tracks branching structure, not how many tributaries happen to arrive.
    """

    FLOW = grid(
        # Two headwaters make a second-order trunk at (3, 3).
        {(0, 1): S, (1, 1): S, (2, 1): SE, (3, 2): E}
        | {(0, 5): S, (1, 5): S, (2, 5): SW, (3, 4): W}
        | {(3, 3): S, (4, 3): S, (5, 3): S}
        # A lone first-order tributary joins it at (5, 3).
        | {(4, 1): SE, (5, 2): E} | {(6, 3): S, (7, 3): 0},
        (9, 7),
    )

    def test_the_trunk_stays_second_order(self) -> None:
        result = net(self.FLOW, **EVERYTHING)
        assert (
            result.max_order == 2
        ), "a first-order tributary must not promote a second-order channel"

    def test_the_tributary_is_first_order(self) -> None:
        result = net(self.FLOW, **EVERYTHING)
        tributary = [r for r in result.reaches if r.cells[0] == (4, 1)]
        assert len(tributary) == 1
        assert tributary[0].order == 1

    def test_the_trunk_is_one_stream_not_two(self) -> None:
        """It runs from where it became second order to the outlet, through the
        junction. Cutting there would break Horton's law of stream numbers."""
        result = net(self.FLOW, **EVERYTHING)
        trunks = [r for r in result.reaches if r.order == 2]
        assert len(trunks) == 1, f"the trunk was split into {len(trunks)} pieces"
        assert trunks[0].cells[0] == (3, 3)
        assert trunks[0].cells[-1] == (7, 3)


class TestThreeOrders:
    r"""Four headwaters pairing twice: 1+1 -> 2 on each side, then 2+2 -> 3.

    Laid out as a symmetric double-Y so the geometry is readable::

        (0,0)\        /(0,2)      (0,4)\        /(0,6)
              (1,1)                     (1,5)
                |                          |
             (2,1)\                    /(2,5)
                    (3,2)\      /(3,4)
                           (4,3)
                             |
                          (5,3)  outlet
    """

    FLOW = grid(
        # West pair: two headwaters into (1, 1), which becomes second order.
        {(0, 0): SE, (0, 2): SW, (1, 1): S, (2, 1): SE}
        # East pair: mirrored.
        | {(0, 4): SE, (0, 6): SW, (1, 5): S, (2, 5): SW}
        # The two second-order channels meet at (4, 3), making third order.
        | {(3, 2): SE, (3, 4): SW, (4, 3): S, (5, 3): 0},
        (7, 8),
    )

    def test_the_trunk_is_third_order(self) -> None:
        result = net(self.FLOW, **EVERYTHING)
        assert result.max_order == 3

    def test_there_are_four_headwaters_two_second_and_one_third(self) -> None:
        result = net(self.FLOW, **EVERYTHING)
        counts = {order: sum(1 for r in result.reaches if r.order == order) for order in (1, 2, 3)}
        assert counts == {1: 4, 2: 2, 3: 1}, counts

    def test_stream_numbers_decrease_with_order(self) -> None:
        """Horton's law of stream numbers. More high-order streams than low is
        impossible, and was the symptom of cutting reaches at every junction --
        on the real sample it produced 16 fourth-order against 10 third."""
        result = net(self.FLOW, **EVERYTHING)
        counts = [
            sum(1 for r in result.reaches if r.order == order)
            for order in range(1, result.max_order + 1)
        ]
        assert counts == sorted(counts, reverse=True), counts


class TestThresholds:
    def test_a_threshold_is_an_area_not_a_cell_count(self) -> None:
        """So the same value means the same thing at any resolution."""
        assert threshold_cells_for(1.0, 5.0) == 400
        assert threshold_cells_for(1.0, 30.0) == 11
        assert threshold_cells_for(0.25, 5.0) == 100

    def test_a_sub_cell_threshold_still_means_one_cell(self) -> None:
        """Rounding it to zero would mean the opposite of what was asked."""
        assert threshold_cells_for(0.000001, 30.0) == 1

    @pytest.mark.parametrize("bad", [0.0, -1.0])
    def test_a_non_positive_threshold_is_refused(self, bad: float) -> None:
        with pytest.raises(StreamExtractionError, match="threshold must be positive"):
            threshold_cells_for(bad, 5.0)

    def test_raising_it_shortens_the_network(self) -> None:
        flow = TestAYJunction.FLOW
        fine = net(flow, threshold_ha=0.0001)
        # 0.09 ha is 900 m2, which is 9 cells at 10 m. (0.0009 ha would be
        # 0.09 of a cell, which the floor of one cell makes identical to `fine`.)
        coarse = net(flow, threshold_ha=0.09)
        assert coarse.stream_cell_count < fine.stream_cell_count
        assert coarse.total_length_m < fine.total_length_m

    def test_a_threshold_nothing_reaches_gives_an_empty_network(self) -> None:
        result = net(TestAYJunction.FLOW, threshold_ha=100.0)
        assert result.reaches == []
        assert result.max_order == 0
        assert result.total_length_m == 0.0


class TestTheReport:
    def test_lengths_sum_across_orders(self) -> None:
        result = net(TestAYJunction.FLOW, **EVERYTHING)
        report = result.report()
        summed = sum(entry["length_m"] for entry in report["by_order"].values())
        assert summed == pytest.approx(report["total_length_m"], abs=0.2)

    def test_reach_counts_sum_across_orders(self) -> None:
        result = net(TestAYJunction.FLOW, **EVERYTHING)
        report = result.report()
        assert sum(e["reaches"] for e in report["by_order"].values()) == report["reach_count"]

    def test_drainage_density_needs_a_catchment(self) -> None:
        """Density over an arbitrary rectangle is a property of the rectangle."""
        assert net(TestAYJunction.FLOW, **EVERYTHING).drainage_density_km_per_km2 is None
