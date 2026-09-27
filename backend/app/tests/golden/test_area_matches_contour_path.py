"""★ The drawn area and the contour upload are one pipeline, not two.

Given the *same* terrain, both entry points must recommend the same site, with
the same catchment and the same pond. The only permitted differences are the
blocks that describe the input. If this fails, the area path has grown its own
downstream behaviour -- which is exactly what ADR-7 exists to prevent.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.providers.elevation.base import Bounds, DemGrid
from app.services import area as area_service
from app.services.contour_analysis import (
    ContourAnalysisOptions,
    analyze_area,
    analyze_contour_map,
)
from app.tests.golden.test_source_interchangeability import valley_kml

#: Blocks that describe the input rather than the answer.
INPUT_BLOCKS = {
    "analysis_id",
    "generated_at",
    "elapsed_s",
    "stage_timings_s",
    "input",
    "terrain_source",
    "contour_map",
    "interpolated_terrain",
    "area_of_interest",
    "warnings",
    "explanation",
}


def options() -> ContourAnalysisOptions:
    return ContourAnalysisOptions(enrich=False, max_sites=3, include_catchment_geometry=True)


@pytest.fixture(scope="module")
def both(tmp_path_factory: pytest.TempPathFactory) -> tuple[dict[str, Any], dict[str, Any]]:
    upload = analyze_contour_map(valley_kml(), "valley.kml", options())
    dem = upload.dem

    def same_terrain(_bounds: Bounds, **_kwargs: Any) -> DemGrid:
        return dem

    # A box a little larger than the grid, so every cell is inside it and the
    # rectangle mask removes nothing: the comparison is then of the pipelines
    # alone.
    g = area_service.grid_bounds_4326(dem)
    pad = 0.001
    bbox = [g.min_lon - pad, g.min_lat - pad, g.max_lon + pad, g.max_lat + pad]
    drawn = analyze_area(
        bbox, options(), fetch=same_terrain, store=Path(tmp_path_factory.mktemp("dem"))
    )
    return upload.as_dict(), drawn.as_dict()


class TestSameTerrainSameAnswer:
    def test_there_is_something_to_compare(self, both) -> None:  # type: ignore[no-untyped-def]
        upload, _ = both
        assert upload["candidate_sites"], "the valley produced no site; the test is vacuous"

    def test_the_same_sites_in_the_same_order(self, both) -> None:  # type: ignore[no-untyped-def]
        upload, drawn = both
        assert [s["location"] for s in drawn["candidate_sites"]] == [
            s["location"] for s in upload["candidate_sites"]
        ]

    def test_the_same_catchment_and_pond(self, both) -> None:  # type: ignore[no-untyped-def]
        upload, drawn = both
        a, b = upload["recommended_site"], drawn["recommended_site"]
        assert b["catchment"]["metrics"] == a["catchment"]["metrics"]
        assert b["catchment"]["geometry"] == a["catchment"]["geometry"]
        assert b["pond"] == a["pond"]

    def test_the_same_summary(self, both) -> None:  # type: ignore[no-untyped-def]
        upload, drawn = both
        assert drawn["summary"] == upload["summary"]

    def test_everything_but_the_input_description_is_identical(self, both) -> None:  # type: ignore[no-untyped-def]
        upload, drawn = both
        assert set(drawn) == set(upload)
        for key in set(upload) - INPUT_BLOCKS:
            assert drawn[key] == upload[key], f"`{key}` differs between the two entry points"

    def test_and_the_input_description_does_differ(self, both) -> None:  # type: ignore[no-untyped-def]
        """Sanity on the test: the two runs really did come in different doors."""
        upload, drawn = both
        assert upload["terrain_source"]["kind"] == "uploaded_contour_map"
        assert drawn["terrain_source"]["kind"] == "copernicus_glo30"
        assert upload["contour_map"] is not None and drawn["contour_map"] is None
