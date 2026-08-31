"""The bundled GeoJSON export (M7-4).

The value of this endpoint is that someone can open one file in QGIS and see the
whole answer, so the tests are about whether the file is actually usable: are the
layers labelled, is the CRS stated, and does a caveat that only exists in the
JSON response survive into the export.
"""

from __future__ import annotations

import io
import json
from typing import Any

import pytest

from app.tests.synthetic_kml import build_kml, concentric_rings

pytestmark = pytest.mark.integration


def finished_job(client: Any, *, contours: bool = True) -> str:
    files = {
        "file": (
            "rings.kml",
            io.BytesIO(build_kml(concentric_rings())),
            "application/vnd.google-earth.kml+xml",
        )
    }
    started = client.post(
        "/api/v1/analysis",
        files=files,
        data={"enrich": "false", "include_contours": str(contours).lower()},
    )
    assert started.status_code == 202, started.text
    job_id = started.json()["job_id"]
    for _ in range(60):
        if client.get(f"/api/v1/analysis/{job_id}/status").json()["is_terminal"]:
            return job_id
    raise AssertionError("job never settled")


class TestTheExportedFile:
    def test_it_is_a_feature_collection(self, client: Any) -> None:
        body = client.get(f"/api/v1/export/{finished_job(client)}").json()
        assert body["type"] == "FeatureCollection"
        assert body["features"]

    def test_it_is_served_as_a_download(self, client: Any) -> None:
        """The point is a file, not a response body."""
        response = client.get(f"/api/v1/export/{finished_job(client)}")
        assert response.headers["content-type"].startswith("application/geo+json")
        assert "attachment" in response.headers["content-disposition"]
        assert ".geojson" in response.headers["content-disposition"]

    def test_the_crs_is_stated_rather_than_assumed(self, client: Any) -> None:
        """4326 is the GeoJSON default, but the reader months later was not told."""
        body = client.get(f"/api/v1/export/{finished_job(client)}").json()
        assert "CRS84" in body["crs"]["properties"]["name"]

    def test_every_feature_says_which_layer_it_is(self, client: Any) -> None:
        """It is what QGIS and geopandas group by."""
        body = client.get(f"/api/v1/export/{finished_job(client)}").json()
        for feature in body["features"]:
            assert feature["properties"]["layer"], feature["properties"]

    def test_it_carries_the_layers_the_analysis_produced(self, client: Any) -> None:
        body = client.get(f"/api/v1/export/{finished_job(client)}").json()
        layers = set(body["properties"]["layers"])
        assert {"survey_extent", "candidate_site", "catchment"} <= layers, layers

    def test_the_feature_count_matches_the_features(self, client: Any) -> None:
        body = client.get(f"/api/v1/export/{finished_job(client)}").json()
        assert body["properties"]["feature_count"] == len(body["features"])

    def test_the_tier_is_recorded(self, client: Any) -> None:
        """A ranking computed without soil data must not be mistaken for one
        that had it, and a file outlives the response that explained it."""
        body = client.get(f"/api/v1/export/{finished_job(client)}").json()
        assert body["properties"]["analysis_tier"] in (
            "full",
            "no_soil_lulc",
            "terrain_only",
        )

    def test_every_geometry_is_valid_geojson(self, client: Any) -> None:
        body = client.get(f"/api/v1/export/{finished_job(client)}").json()
        for feature in body["features"]:
            geometry = feature["geometry"]
            assert geometry["type"] in (
                "Point",
                "LineString",
                "MultiLineString",
                "Polygon",
                "MultiPolygon",
            ), geometry["type"]
            assert geometry["coordinates"]

    def test_it_round_trips_through_json(self, client: Any) -> None:
        raw = client.get(f"/api/v1/export/{finished_job(client)}").text
        assert json.loads(raw)["type"] == "FeatureCollection"


class TestTheContoursFollowTheOption:
    def test_they_are_included_when_asked_for(self, client: Any) -> None:
        body = client.get(f"/api/v1/export/{finished_job(client, contours=True)}").json()
        assert "contour" in body["properties"]["layers"]

    def test_they_are_absent_when_not(self, client: Any) -> None:
        body = client.get(f"/api/v1/export/{finished_job(client, contours=False)}").json()
        assert "contour" not in body["properties"]["layers"]


class TestThePondFootprintIsHonest:
    def test_it_says_the_orientation_is_indicative(self, client: Any) -> None:
        """A polygon in a GIS file looks surveyed whether or not it is.

        The design fixes plan dimensions; nothing in the model chooses a bearing.
        """
        body = client.get(f"/api/v1/export/{finished_job(client)}").json()
        ponds = [f for f in body["features"] if f["properties"]["layer"] == "pond_footprint"]
        if not ponds:
            pytest.skip("no pond was sized on this synthetic surface")
        for pond in ponds:
            assert "indicative" in pond["properties"]["orientation"]

    def test_it_carries_the_design_it_came_from(self, client: Any) -> None:
        body = client.get(f"/api/v1/export/{finished_job(client)}").json()
        ponds = [f for f in body["features"] if f["properties"]["layer"] == "pond_footprint"]
        if not ponds:
            pytest.skip("no pond was sized")
        for key in ("depth_m", "gross_capacity_m3", "binding_constraint"):
            assert key in ponds[0]["properties"], key


class TestRefusals:
    def test_an_unknown_job_is_a_404(self, client: Any) -> None:
        assert client.get("/api/v1/export/deadbeef").status_code == 404

    def test_an_unfinished_job_says_so(self, client: Any) -> None:
        from app.services.job_store import JobRecord, get_store
        from app.services.jobs import JobProgress

        get_store().put(JobRecord(job_id="pending9", progress=JobProgress().as_dict()))
        response = client.get("/api/v1/export/pending9")
        assert response.status_code == 422
        assert "nothing to export" in response.json()["detail"]

    def test_an_unsupported_format_names_what_is_supported(self, client: Any) -> None:
        response = client.get(f"/api/v1/export/{finished_job(client)}?format=shapefile")
        assert response.status_code == 422
        assert response.json()["supported"] == ["geojson"]
