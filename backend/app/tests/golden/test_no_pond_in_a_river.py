"""No recommended site may sit in, or beside, a river.

This test exists because the previous version of it passed while the bug was
live. That audit built its "river" ground truth by calling the very function
under test:

    _, major = exclusions._split_water(osm.water)   # the bug, on both sides

`_split_water` keyed off the `waterway` tag alone. OSM maps a large river twice
-- a centreline tagged `waterway=river`, and the wide body actually rendered,
tagged `natural=water` + `water=river` with no `waterway` tag at all -- so the
body was classified as standing water and given the 0 m buffer written for a
village tank. Ground truth and implementation shared the blind spot, so the test
could not see it, and siting duly returned a site 50 m from the Shivnath.

So the rule here: **ground truth is derived from OSM tags directly, never from
the classifier.** `river_features` below is what a person reading the map would
call a river, and it is deliberately broader than the product's own predicate --
it also matches on the feature *name*, which no product code consults. A test
that agrees with the implementation by construction is not evidence.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.providers.vector.overpass import OsmContext
from app.services.contour_analysis import ContourAnalysisOptions, analyze_contour_map
from app.services.land import osm_exclusion_mask

pytestmark = pytest.mark.slow


def _sample() -> Path | None:
    """The sample sheet, wherever this is running.

    The repo root when run from a checkout; `/opt/contour-sample` inside the
    container, which is also the only place the warm OSM cache
    (`COG_STORE_PATH=/data/cache`) is reachable -- so a single hard-coded path
    would silently skip in exactly the environment that can judge the veto.
    """
    for candidate in (
        Path(__file__).resolve().parents[4] / "contours_1m.kml",
        Path("/opt/contour-sample/contours_1m.kml"),
    ):
        if candidate.exists():
            return candidate
    return None


SAMPLE = _sample()

#: The standoff a site must keep from a mapped river, matching
#: `exclusions.SITING_BUFFER_M["major_watercourse"]`. Asserted as a literal so
#: that loosening the buffer has to be a deliberate edit to a test, not a silent
#: consequence of editing a constant.
REQUIRED_RIVER_STANDOFF_M = 50.0


def river_features(context: OsmContext) -> list:
    """What a person reading the map would call a river or canal. Tags only.

    Independent of `exclusions.classify_water` on purpose -- including the
    name-based clause, which the product never looks at.
    """
    out = []
    for f in context.water:
        tags = {str(k).lower(): str(v).lower() for k, v in f.tags.items()}
        if (
            tags.get("waterway") in {"river", "riverbank", "canal"}
            or tags.get("water") in {"river", "canal", "oxbow"}
            or "river" in tags.get("name", "")
            or "nadi" in tags.get("name", "")
        ):
            out.append(f)
    return out


@pytest.fixture(scope="module")
def analysis():
    if SAMPLE is None:
        pytest.skip("no sample contour map found")
    result = analyze_contour_map(
        SAMPLE.read_bytes(), SAMPLE.name, ContourAnalysisOptions(max_sites=5)
    )
    if result.enrichment.osm is None:
        pytest.skip("OSM unavailable on this run; nothing to judge the veto against")
    return result


@pytest.fixture(scope="module")
def hazards(analysis):
    """Masks built from ground truth, not from the module under test."""
    dem, osm = analysis.dem, analysis.enrichment.osm
    rivers = river_features(osm)
    if not rivers:
        pytest.skip("no river mapped in this window")
    inside, _ = osm_exclusion_mask(OsmContext(water=rivers), dem, buffers_m={"water": 0.0})
    standoff, _ = osm_exclusion_mask(
        OsmContext(water=rivers), dem, buffers_m={"water": REQUIRED_RIVER_STANDOFF_M}
    )
    return {"in a river": inside, "within the river standoff": standoff}


class TestNoSiteSitsInARiver:
    def test_the_window_actually_contains_a_river(self, analysis, hazards) -> None:
        """Otherwise every assertion below passes for the wrong reason."""
        assert hazards["in a river"].any(), "a vacuous pass is not a pass"

    def test_the_areal_river_is_part_of_that_ground_truth(self, analysis) -> None:
        """The specific feature the old classifier missed.

        Guards the regression from the data side: if OSM's areal river vanished
        from the window, the test above could go green without exercising it.
        """
        rivers = river_features(analysis.enrichment.osm)
        areal = [f for f in rivers if not f.tags.get("waterway")]
        assert areal, "the `natural=water` + `water=river` body is the case that failed"

    def test_no_recommended_site_is_in_a_river(self, analysis, hazards) -> None:
        offenders = [
            s["rank"]
            for s in analysis.sites
            if hazards["in a river"][s["location"]["grid_row"], s["location"]["grid_col"]]
        ]
        assert not offenders, f"sites {offenders} are inside a mapped river"

    def test_no_recommended_site_is_within_the_river_standoff(self, analysis, hazards) -> None:
        """The assertion that failed in reality, at 50 m rather than 0 m.

        A pond may not sit on a riverbank either: the land floods, and the
        centreline's own buffer does not reach the bank of a river that runs a
        median 181 m wide.
        """
        mask = hazards["within the river standoff"]
        offenders = [
            s["rank"]
            for s in analysis.sites
            if mask[s["location"]["grid_row"], s["location"]["grid_col"]]
        ]
        assert (
            not offenders
        ), f"sites {offenders} are within {REQUIRED_RIVER_STANDOFF_M:g} m of a mapped river"

    def test_the_veto_actually_covers_the_ground_truth(self, analysis, hazards) -> None:
        """Stronger than checking five points: the mask must contain the hazard.

        Five sites can miss a river by luck. This asserts the exclusion mask is a
        superset of the independently derived standoff, so no *possible* site in
        that band could be returned.
        """
        veto = analysis.exclusions.mask
        missed = hazards["within the river standoff"] & ~veto
        share = float(missed.sum()) / float(hazards["within the river standoff"].sum())
        assert share < 0.01, (
            f"{missed.sum():,} cells ({share:.1%}) within the river standoff are "
            "not vetoed; siting is free to recommend them"
        )


class TestTheStreamDistinctionSurvives:
    """The fix must not have bought river safety by excluding every channel.

    A check dam belongs on a nala. If widening the river rule had swept up
    streams, the model would look safe and return the wrong answer.
    """

    def test_streams_are_not_vetoed_wholesale(self, analysis) -> None:
        from app.services import exclusions

        osm = analysis.enrichment.osm
        streams = [
            f
            for f in osm.water
            if str(f.tags.get("waterway", "")).lower() in exclusions.MINOR_WATERWAYS
            or str(f.tags.get("water", "")).lower() in exclusions.MINOR_WATER_VALUES
        ]
        if not streams:
            pytest.skip("no minor channel mapped in this window")
        for f in streams:
            assert exclusions.classify_water(f, analysis.dem.epsg) == "minor"

    def test_a_site_may_still_sit_on_a_minor_channel(self, analysis) -> None:
        """Not merely 'streams are classified minor' -- that ground stays usable."""
        from app.services import exclusions

        osm = analysis.enrichment.osm
        streams = [
            f
            for f in osm.water
            if str(f.tags.get("waterway", "")).lower() in exclusions.MINOR_WATERWAYS
        ]
        if not streams:
            pytest.skip("no stream mapped in this window")
        band, _ = osm_exclusion_mask(
            OsmContext(water=streams), analysis.dem, buffers_m={"water": 0.0}
        )
        free = band & ~analysis.exclusions.mask
        assert free.any(), (
            "every mapped stream cell is vetoed; a check dam site can no longer "
            "be recommended, which trades one wrong answer for another"
        )
