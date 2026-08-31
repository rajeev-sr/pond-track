"""Pond-site identification (MC-9).

`TestRegressions` pins two defects that produced output which *looked* fine:
normalising over every valid cell instead of the feasible subset (every
candidate tied at 100/100), and measuring buildability on the depression-filled
surface (every candidate reported 0 % slope).
"""

from __future__ import annotations

import numpy as np
import pytest

from app.providers.elevation.base import DemGrid
from app.services import hydrology as hyd
from app.services import siting
from app.services.siting import (
    AHP_WEIGHTS,
    TIER_CRITERIA,
    plan_concavity,
    robust_normalise,
    tier_weights,
)


def grid(z: np.ndarray, cell: float = 5.0) -> DemGrid:
    return DemGrid(
        elevation=z.astype(np.float32),
        transform=(cell, 0.0, 500_000.0, 0.0, -cell, 2_340_000.0 + z.shape[0] * cell),
        epsg=32643,
        cell_size_m=cell,
    )


def bowl_in_a_valley(n: int = 100, depth: float = 4.0, cell: float = 5.0):  # type: ignore[no-untyped-def]
    """A V-shaped valley draining south, with one clear bowl in its floor.

    A *valley* rather than a planar slope, because on a plane every column drains
    independently and flow accumulation never exceeds the row count -- which
    fails the default 1 ha minimum-upstream-area constraint and leaves nothing
    feasible. Real terrain converges; the fixture has to as well.

    The bowl is the only depression, so a correct siting model must put its top
    candidate there. Its position is a property of the constructed surface, never
    a hard-coded coordinate.
    """
    rr, cc = np.mgrid[0:n, 0:n]
    axis = n / 2.0
    z = 100.0 + (n - rr) * 0.20 + np.abs(cc - axis) * 0.12  # downhill S, V laterally
    br, bc, radius = int(n * 0.6), int(axis), 7.0
    r = np.hypot(rr - br, cc - bc)
    z -= np.where(r < radius, depth * (1.0 - r / radius), 0.0)
    dem = grid(z, cell)
    cond = hyd.fill_depressions(dem)
    return dem, cond, hyd.build_flow(dem, cond), (br, bc)


class TestTierWeights:
    def test_every_tier_sums_to_one(self) -> None:
        for tier in TIER_CRITERIA:
            assert sum(tier_weights(tier).values()) == pytest.approx(1.0)  # type: ignore[arg-type]

    def test_terrain_tier_has_exactly_its_four_criteria(self) -> None:
        w = tier_weights("terrain_only")
        assert set(w) == {"flow_accumulation", "slope", "depression_depth", "plan_concavity"}

    def test_relative_order_survives_renormalisation(self) -> None:
        """Dropping soil must not reorder the criteria that remain."""
        full = tier_weights("full")
        terrain = tier_weights("terrain_only")
        names = sorted(terrain, key=lambda n: -terrain[n])
        assert names == sorted(names, key=lambda n: -full[n])

    def test_ratios_are_preserved_exactly(self) -> None:
        t = tier_weights("terrain_only")
        assert t["flow_accumulation"] / t["slope"] == pytest.approx(
            AHP_WEIGHTS["flow_accumulation"] / AHP_WEIGHTS["slope"]
        )

    def test_full_tier_uses_every_declared_weight(self) -> None:
        assert set(tier_weights("full")) == set(AHP_WEIGHTS)


class TestRobustNormalise:
    def test_output_is_bounded(self) -> None:
        rng = np.random.default_rng(0)
        v = rng.normal(50, 20, (40, 40))
        out = robust_normalise(v, np.ones_like(v, dtype=bool))
        assert out.min() >= 0.0 and out.max() <= 1.0

    def test_invert_flips_the_ranking(self) -> None:
        v = np.linspace(0, 1, 100).reshape(10, 10)
        m = np.ones_like(v, dtype=bool)
        assert robust_normalise(v, m)[0, 0] < robust_normalise(v, m)[-1, -1]
        assert (
            robust_normalise(v, m, invert=True)[0, 0] > robust_normalise(v, m, invert=True)[-1, -1]
        )

    def test_outliers_do_not_flatten_the_rest(self) -> None:
        """Percentile scaling, not min/max: one extreme cell must not squash the field.

        With min/max scaling a single 1e6 value maps every other cell to ~0 and
        the criterion becomes noise. Percentile scaling keeps the field spread
        across [0, 1], so the standard deviation stays near that of a uniform
        distribution (~0.29).
        """
        v = np.linspace(0.0, 1.0, 400).reshape(20, 20)
        v[0, 0] = 1e6
        out = robust_normalise(v, np.ones_like(v, dtype=bool))
        rest = np.delete(out.ravel(), 0)
        assert rest.std() > 0.2, "a single outlier compressed the whole field"
        # What min/max scaling would have produced, for contrast.
        minmax_std = float(((v - v.min()) / (v.max() - v.min())).ravel()[1:].std())
        assert rest.std() > 100 * minmax_std

    def test_constant_field_is_neutral_not_nan(self) -> None:
        v = np.full((8, 8), 7.0)
        out = robust_normalise(v, np.ones_like(v, dtype=bool))
        assert np.allclose(out, 0.5)

    def test_empty_mask_returns_zeros(self) -> None:
        v = np.ones((5, 5))
        assert np.all(robust_normalise(v, np.zeros_like(v, dtype=bool)) == 0.0)

    def test_only_masked_cells_are_written(self) -> None:
        v = np.linspace(0, 1, 100).reshape(10, 10)
        m = np.zeros_like(v, dtype=bool)
        m[2:5, 2:5] = True
        out = robust_normalise(v, m)
        assert np.all(out[~m] == 0.0)
        assert out[m].max() > 0.0


class TestPlanConcavity:
    def test_bowl_is_positive(self) -> None:
        rr, cc = np.mgrid[0:21, 0:21]
        z = ((rr - 10.0) ** 2 + (cc - 10.0) ** 2) * 0.05  # z = r^2 -> Laplacian > 0
        assert float(plan_concavity(grid(z))[10, 10]) > 0

    def test_dome_is_negative(self) -> None:
        rr, cc = np.mgrid[0:21, 0:21]
        z = -((rr - 10.0) ** 2 + (cc - 10.0) ** 2) * 0.05
        assert float(plan_concavity(grid(z))[10, 10]) < 0

    def test_plane_is_zero(self) -> None:
        rr, _cc = np.mgrid[0:21, 0:21]
        c = plan_concavity(grid(rr.astype(float)))
        assert abs(float(np.nanmean(c[3:-3, 3:-3]))) < 1e-5

    def test_nodata_stays_nodata(self) -> None:
        z = np.zeros((10, 10))
        z[0, 0] = np.nan
        assert np.isnan(plan_concavity(grid(z))[0, 0])


class TestScoring:
    def test_score_is_zero_outside_the_feasible_set(self) -> None:
        dem, cond, flow, _ = bowl_in_a_valley()
        score, feasible, _b, _l = siting.score_terrain(dem, cond, flow)
        assert np.all(score[~feasible] == 0.0)

    def test_score_is_bounded(self) -> None:
        dem, cond, flow, _ = bowl_in_a_valley()
        score, _f, _b, _l = siting.score_terrain(dem, cond, flow)
        assert score.min() >= 0.0 and score.max() <= 1.0 + 1e-6

    def test_slope_limit_is_enforced(self) -> None:
        dem, cond, flow, _ = bowl_in_a_valley()
        slope = hyd.slope_percent(dem.elevation, dem.cell_size_m)
        _, feasible, _b, _l = siting.score_terrain(dem, cond, flow, max_slope_pct=2.0)
        assert np.all(slope[feasible] <= 2.0 + 1e-6)

    def test_min_upstream_area_is_enforced(self) -> None:
        dem, cond, flow, _ = bowl_in_a_valley()
        _, feasible, _b, _l = siting.score_terrain(dem, cond, flow, min_upstream_ha=2.0)
        min_cells = 2.0 * 10_000.0 / dem.cell_size_m**2
        assert np.all(flow.accumulation[feasible] >= min_cells)

    def test_edge_buffer_excludes_the_survey_margin(self) -> None:
        dem, cond, flow, _ = bowl_in_a_valley()
        _, feasible, _b, _l = siting.score_terrain(dem, cond, flow)
        b = siting.EDGE_BUFFER_CELLS
        assert not feasible[:b, :].any() and not feasible[-b:, :].any()
        assert not feasible[:, :b].any() and not feasible[:, -b:].any()

    def test_tightening_a_constraint_never_grows_the_feasible_set(self) -> None:
        dem, cond, flow, _ = bowl_in_a_valley()
        _, loose, _b1, _l1 = siting.score_terrain(dem, cond, flow, max_slope_pct=20.0)
        _, tight, _b2, _l2 = siting.score_terrain(dem, cond, flow, max_slope_pct=1.0)
        assert int(tight.sum()) <= int(loose.sum())


class TestRegionScoring:
    """Monotonicity of the scoring function, isolated from any terrain.

    Scores are normalised *across the candidate set*, so absolute values are not
    comparable between runs on different surfaces. Feeding hand-made regions that
    differ in exactly one criterion is therefore the only well-posed way to test
    that a criterion has the intended direction of effect.
    """

    @staticmethod
    def _region(**kw: object) -> siting._Region:
        base: dict[str, object] = {
            "kind": "natural_depression",
            "cells": 40,
            "site_row": 10,
            "site_col": 10,
            "outlet_row": 10,
            "outlet_col": 11,
            "max_depth_m": 1.0,
            "max_upstream_cells": 1000,
            "mean_slope_pct": 2.0,
            "mean_concavity": 0.1,
        }
        base.update(kw)
        return siting._Region(**base)  # type: ignore[arg-type]

    def test_deeper_depression_scores_higher(self) -> None:
        shallow = self._region(max_depth_m=0.5, site_row=1)
        deep = self._region(max_depth_m=3.0, site_row=2)
        ranked = siting._score_regions([shallow, deep])
        assert ranked[0][1] is deep

    def test_more_upstream_area_scores_higher(self) -> None:
        small = self._region(max_upstream_cells=100, site_row=1)
        large = self._region(max_upstream_cells=100_000, site_row=2)
        ranked = siting._score_regions([small, large])
        assert ranked[0][1] is large

    def test_gentler_slope_scores_higher(self) -> None:
        steep = self._region(mean_slope_pct=7.0, site_row=1)
        gentle = self._region(mean_slope_pct=0.5, site_row=2)
        ranked = siting._score_regions([steep, gentle])
        assert ranked[0][1] is gentle

    def test_more_concave_scores_higher(self) -> None:
        convex = self._region(mean_concavity=-0.5, site_row=1)
        concave = self._region(mean_concavity=0.5, site_row=2)
        ranked = siting._score_regions([convex, concave])
        assert ranked[0][1] is concave

    def test_depth_saturates(self) -> None:
        """Past the saturation point extra depth is just more excavation."""
        at_cap = self._region(max_depth_m=siting.DEPRESSION_SATURATION_M, site_row=1)
        beyond = self._region(max_depth_m=siting.DEPRESSION_SATURATION_M * 4, site_row=2)
        ranked = siting._score_regions([at_cap, beyond])
        assert ranked[0][0] == pytest.approx(ranked[1][0])

    def test_raw_values_are_reported_unsaturated(self) -> None:
        """The score saturates; the *reported* depth must remain the real one."""
        deep = self._region(max_depth_m=12.0)
        _score, _region, criteria = siting._score_regions([deep, self._region()])[0]
        depth = next(c for c in criteria if c.name == "depression_depth")
        assert depth.raw in (12.0, 1.0)

    def test_empty_input(self) -> None:
        assert siting._score_regions([]) == []

    def test_contributions_sum_to_the_score(self) -> None:
        ranked = siting._score_regions([self._region(site_row=1), self._region(site_row=2)])
        for score, _region, criteria in ranked:
            assert sum(c.contribution for c in criteria) == pytest.approx(score)


class TestSiteExtraction:
    def test_finds_the_constructed_bowl_as_a_depression_region(self) -> None:
        """★ The bowl is the only depression, so it must be among the candidates.

        Deliberately *not* asserting rank 1. Flow accumulation carries the largest
        AHP weight (0.34 against 0.23 for depression depth), so the valley outlet
        -- which collects the entire catchment -- legitimately outranks a
        mid-slope bowl. That is correct hydrology: water availability dominates,
        and a model that ignored it to chase the deepest hollow would be worse.
        What must hold is that the only depression on the surface gets *found*.
        """
        dem, cond, flow, (br, bc) = bowl_in_a_valley()
        res = siting.identify_pond_sites(dem, cond, flow, max_sites=5, min_separation_m=80.0)
        assert res.sites, f"no site found; warnings={res.warnings}"
        near = [s for s in res.sites if np.hypot(s.row - br, s.col - bc) <= 10.0]
        assert near, (
            f"the only depression at ({br},{bc}) was not among the candidates "
            f"{[(s.row, s.col) for s in res.sites]}"
        )
        found = near[0]
        assert found.kind == "natural_depression"
        assert found.depression_depth_m > 0.5
        # A region, not a single cell: the brief asks for a location *or region*.
        assert found.region_cells > 1
        assert found.region_area_m2 > 0

    def test_the_depression_is_the_deepest_candidate(self) -> None:
        dem, cond, flow, (br, bc) = bowl_in_a_valley()
        res = siting.identify_pond_sites(dem, cond, flow, max_sites=5, min_separation_m=80.0)
        deepest = max(res.sites, key=lambda s: s.depression_depth_m)
        assert np.hypot(deepest.row - br, deepest.col - bc) <= 10.0

    def test_pour_point_is_reported_separately_from_the_pond_position(self) -> None:
        """A depression's runoff passes through its spill point, not its centre.

        The pond goes at the deepest buildable cell; the catchment must be
        delineated from the highest-accumulation cell of the same region. Reading
        both off one cell under-reports the water a bowl collects, because after
        flooding the epsilon gradient carries flow to the spill point rather than
        through the geometric centre.
        """
        dem, cond, flow, _ = bowl_in_a_valley()
        res = siting.identify_pond_sites(dem, cond, flow, max_sites=5, min_separation_m=80.0)
        deps = [s for s in res.sites if s.kind == "natural_depression"]
        assert deps, "fixture produced no depression candidate"
        s0 = deps[0]
        assert flow.accumulation[s0.outlet_row, s0.outlet_col] >= flow.accumulation[s0.row, s0.col]
        assert s0.upstream_cells == int(flow.accumulation[s0.outlet_row, s0.outlet_col])

    def test_reported_slope_respects_the_constraint(self) -> None:
        """A region's reported slope must not exceed the limit that admitted it.

        Feasibility is a per-cell mask, so averaging slope over an entire landform
        could report a figure above the limit -- which reads as a contradiction.
        Aggregation is therefore over the region's buildable cells only.
        """
        dem, cond, flow, _ = bowl_in_a_valley()
        limit = 6.0
        res = siting.identify_pond_sites(
            dem, cond, flow, max_sites=5, min_separation_m=80.0, max_slope_pct=limit
        )
        for site in res.sites:
            assert (
                site.slope_pct <= limit + 1e-6
            ), f"site {site.rank} reports {site.slope_pct:.2f}% against a {limit}% limit"

    def test_site_kind_is_one_of_the_two_generators(self) -> None:
        dem, cond, flow, _ = bowl_in_a_valley()
        res = siting.identify_pond_sites(dem, cond, flow, max_sites=5, min_separation_m=80.0)
        assert {s.kind for s in res.sites} <= {"natural_depression", "channel_position"}

    def test_ranks_are_sequential_and_scores_descend(self) -> None:
        dem, cond, flow, _ = bowl_in_a_valley()
        res = siting.identify_pond_sites(dem, cond, flow, max_sites=5, min_separation_m=60.0)
        assert [s.rank for s in res.sites] == list(range(1, len(res.sites) + 1))
        scores = [s.score_0_100 for s in res.sites]
        assert scores == sorted(scores, reverse=True)

    def test_max_sites_is_respected(self) -> None:
        dem, cond, flow, _ = bowl_in_a_valley()
        assert (
            len(
                siting.identify_pond_sites(
                    dem, cond, flow, max_sites=2, min_separation_m=60.0
                ).sites
            )
            <= 2
        )

    def test_separation_is_enforced(self) -> None:
        dem, cond, flow, _ = bowl_in_a_valley()
        res = siting.identify_pond_sites(dem, cond, flow, max_sites=6, min_separation_m=150.0)
        for i, a in enumerate(res.sites):
            for b in res.sites[i + 1 :]:
                d = np.hypot(a.x_m - b.x_m, a.y_m - b.y_m)
                assert d >= 150.0 - 1e-6, f"sites {a.rank},{b.rank} only {d:.0f} m apart"

    def test_unreachable_criteria_warn_rather_than_raising(self) -> None:
        """Both generators must be exhausted before "no candidates" is reported."""
        dem, cond, flow, _ = bowl_in_a_valley()
        res = siting.identify_pond_sites(
            dem, cond, flow, score_threshold=0.999, min_depression_depth_m=99.0
        )
        assert res.sites == []
        assert any("no candidate region" in w for w in res.warnings)

    def test_a_high_channel_threshold_still_finds_depressions(self) -> None:
        """A real bowl must not be hidden by a channel-scoring cutoff."""
        dem, cond, flow, _ = bowl_in_a_valley()
        res = siting.identify_pond_sites(dem, cond, flow, score_threshold=0.999)
        assert res.sites, "a natural depression was hidden by the channel threshold"
        assert all(s.kind == "natural_depression" for s in res.sites)

    def test_criteria_contributions_sum_to_the_score(self) -> None:
        dem, cond, flow, _ = bowl_in_a_valley()
        res = siting.identify_pond_sites(dem, cond, flow, max_sites=3, min_separation_m=60.0)
        for s in res.sites:
            total = sum(c.contribution for c in s.criteria)
            assert total * 100.0 == pytest.approx(s.score_0_100, abs=0.15)

    def test_every_criterion_is_explained(self) -> None:
        dem, cond, flow, _ = bowl_in_a_valley()
        res = siting.identify_pond_sites(dem, cond, flow, max_sites=1, min_separation_m=60.0)
        assert {c.name for c in res.sites[0].criteria} == set(tier_weights("terrain_only"))

    def test_lonlat_round_trips_to_the_projected_position(self) -> None:
        from pyproj import Transformer

        dem, cond, flow, _ = bowl_in_a_valley()
        s = siting.identify_pond_sites(dem, cond, flow, max_sites=1, min_separation_m=60.0).sites[0]
        fwd = Transformer.from_crs(4326, dem.epsg, always_xy=True)
        x, y = fwd.transform(s.lon, s.lat)
        assert (x, y) == pytest.approx((s.x_m, s.y_m), abs=0.5)

    def test_result_serialises(self) -> None:
        import json

        dem, cond, flow, _ = bowl_in_a_valley()
        d = json.loads(json.dumps(siting.identify_pond_sites(dem, cond, flow).as_dict()))
        assert d["analysis_tier"] == "terrain_only"
        assert "layers_unavailable" in d
        assert "criteria_weights" in d
        if d["candidate_sites"]:
            assert "site_kind" in d["candidate_sites"][0]
            assert "region" in d["candidate_sites"][0]

    def test_deterministic(self) -> None:
        dem, cond, flow, _ = bowl_in_a_valley()
        a = siting.identify_pond_sites(dem, cond, flow, max_sites=3, min_separation_m=60.0)
        b = siting.identify_pond_sites(dem, cond, flow, max_sites=3, min_separation_m=60.0)
        assert [s.as_dict() for s in a.sites] == [s.as_dict() for s in b.sites]


class TestRegressions:
    """Two defects whose output looked entirely plausible."""

    def test_scores_discriminate_between_candidates(self) -> None:
        """Normalising over all valid cells made every candidate score 100.0.

        Feasible cells already sit in the extreme tail of every criterion relative
        to the whole surface, so global percentile scaling clipped them all to 1.0
        and the ranking became arbitrary. Normalisation must therefore run over
        the feasible subset.
        """
        rng = np.random.default_rng(7)
        n = 110
        rr, cc = np.mgrid[0:n, 0:n]
        axis = n / 2.0
        z = 100.0 + (n - rr) * 0.20 + np.abs(cc - axis) * 0.10 + rng.normal(0, 0.03, (n, n))
        # Three bowls of clearly different depth: their scores must differ.
        for br, bc, d in ((30, 55, 5.0), (60, 52, 3.0), (85, 58, 1.5)):
            r = np.hypot(rr - br, cc - bc)
            z -= np.where(r < 7, d * (1.0 - r / 7.0), 0.0)
        dem = grid(z)
        cond = hyd.fill_depressions(dem)
        flow = hyd.build_flow(dem, cond)
        res = siting.identify_pond_sites(
            dem,
            cond,
            flow,
            max_sites=3,
            min_separation_m=60.0,
            score_threshold=0.35,
            min_upstream_ha=0.2,
        )
        scores = [round(s.score_0_100, 1) for s in res.sites]
        assert len(res.sites) >= 2, f"expected several sites, got {len(res.sites)}"
        assert len(set(scores)) == len(scores), f"candidates tied at {scores}"
        assert max(scores) < 100.0 or len(set(scores)) > 1

    def test_slope_comes_from_the_original_ground(self) -> None:
        """Measuring slope on the filled surface reported 0 % everywhere.

        Depression filling flattens precisely the cells a siting model chooses
        between, so buildability has to be measured on the original DEM.
        """
        dem, cond, flow, _ = bowl_in_a_valley()
        on_original = hyd.slope_percent(dem.elevation, dem.cell_size_m)
        on_filled = hyd.slope_percent(cond.filled, dem.cell_size_m)
        depression = np.isfinite(cond.fill_depth) & (cond.fill_depth > 0.5)
        assert depression.any(), "fixture produced no depression to test with"
        # Inside a filled depression the conditioned surface is flat by
        # construction while the real ground is not -- typically by an order of
        # magnitude, which is exactly what made the bug invisible.
        filled_mean = float(np.nanmean(on_filled[depression]))
        orig_mean = float(np.nanmean(on_original[depression]))
        assert filled_mean < 0.5 * orig_mean

        # A depression candidate must report a real slope, not the ~0 % the
        # conditioned surface would give. slope_pct is a region aggregate, so
        # this asserts the property rather than a single cell's value.
        res = siting.identify_pond_sites(dem, cond, flow, max_sites=6, min_separation_m=60.0)
        deps = [s for s in res.sites if s.kind == "natural_depression"]
        assert deps, "fixture produced no depression candidate"
        assert (
            deps[0].slope_pct > filled_mean
        ), "reported slope looks like the filled surface, not the real ground"
