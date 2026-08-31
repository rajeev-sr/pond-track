"""Never recommend a pond where one already exists.

The failure this guards against is subtle and was real. Siting weights flow
accumulation highest and scores depression depth alongside it, and an existing
tank maximises both -- it is, after all, a place where water already collects.
Measured on the sample sheet with no land cover, **three of five recommended
sites landed inside permanent water**. The model was right that the spot was
hydrologically ideal and wrong that anything should be built there.

Two independent sources now rule it out, so one provider being down no longer
removes the protection. When neither answers, the result must say so loudly:
terrain alone cannot tell a good pond site from a pond.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.providers.landcover.worldcover import LandCover
from app.services.enrichment import Enrichment

SHAPE = (8, 8)


def cover(codes: np.ndarray) -> LandCover:
    return LandCover(
        codes=codes.astype(np.uint8),
        fractions={},
        dominant_class="grassland",
        tiles_used=[],
    )


def grassland() -> np.ndarray:
    return np.full(SHAPE, 30, dtype=np.uint8)


# The availability-grid combination tests that used to live here are gone. They
# tested an earlier, narrower fix in which `availability_grid()` merged land
# cover with an OSM water mask. That veto now lives in `services/exclusions.py`
# -- covering rivers, buildings and roads as well as standing water -- and is
# tested in `test_exclusions.py`. `availability_grid()` is land-cover-only again.


class TestItReportsWhichProtectionWasInForce:
    def test_both_sources_is_high_confidence(self) -> None:
        report = Enrichment(land_cover=cover(grassland()), osm=object()).water_exclusion
        assert report["confidence"] == "high"
        assert len(report["sources"]) == 2

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"land_cover": "cover"},
            {"osm": "water"},
        ],
    )
    def test_one_source_is_partial_and_says_to_check(self, kwargs: dict) -> None:
        built = {
            k: (cover(grassland()) if v == "cover" else np.zeros(SHAPE, dtype=bool))
            for k, v in kwargs.items()
        }
        report = Enrichment(**built).water_exclusion  # type: ignore[arg-type]
        assert report["confidence"] == "partial"
        assert "not exhaustive" in report["note"]

    def test_no_source_is_called_out_in_the_strongest_terms(self) -> None:
        report = Enrichment().water_exclusion
        assert report["confidence"] == "none"
        assert report["sources"] == []
        assert "could not be excluded" in report["note"]
        assert "already there" in report["note"]

    def test_the_report_travels_in_the_response(self) -> None:
        block = Enrichment().as_dict()
        assert block["water_exclusion"]["confidence"] == "none"


class TestTheExplanationWarnsTheReader:
    def test_no_water_source_leads_the_caveats(self) -> None:
        """A site that is already a tank is a wrong answer, not a caveated one."""
        from app.services import explain

        site = {
            "rank": 1,
            "suitability_score": 80.0,
            "site_kind": "channel_position",
            "catchment": {"metrics": {"area_ha": 100.0}, "quality": {}},
            "criteria_breakdown": [],
            "runoff": {"available": False},
            "pond": {"available": False, "reason": "n/a"},
        }
        caveats = explain.explain_site(
            site,
            {
                "analysis_tier": "terrain_only",
                "water_exclusion": {"confidence": "none", "sources": []},
            },
        ).caveats
        assert "dry ground" in caveats[0], "the water warning must come first"

    def test_partial_cover_is_mentioned_but_not_alarming(self) -> None:
        from app.services import explain

        site = {
            "rank": 1,
            "suitability_score": 80.0,
            "site_kind": "channel_position",
            "catchment": {"metrics": {"area_ha": 100.0}, "quality": {}},
            "criteria_breakdown": [],
            "runoff": {"available": False},
            "pond": {"available": False, "reason": "n/a"},
        }
        caveats = explain.explain_site(
            site,
            {
                "analysis_tier": "full",
                "water_exclusion": {"confidence": "partial", "sources": ["land cover"]},
            },
        ).caveats
        assert any("existing tank" in c for c in caveats)
        assert "dry ground" not in caveats[0]
