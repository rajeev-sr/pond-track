"""Select an area on the map, get the three results back (final submission).

The final brief asks for land to be selected on a map, and for the suggested
pond location, its catchment area and the water it can collect to be generated
for that land. These pin the HTTP side of that: `POST /analyzeArea` and its
job form `POST /analysis/area`, with Copernicus replaced by synthetic terrain
placed exactly where the real fetch would put it. No network.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.providers.base import ProviderUnavailableError
from app.providers.elevation import copernicus_aws
from app.services.jobs import AREA_STEPS
from app.tests.area_fakes import DURG_BBOX, FakeFetch

SYNC = "/api/v1/analyzeArea"
ASYNC = "/api/v1/analysis/area"


@pytest.fixture
def copernicus(monkeypatch: pytest.MonkeyPatch) -> FakeFetch:
    fake = FakeFetch()
    monkeypatch.setattr(copernicus_aws, "fetch_dem", fake)
    return fake


def analyse(client: Any, bbox: Any = None, **options: Any) -> Any:
    body = {"bbox": DURG_BBOX if bbox is None else bbox, "enrich": False, **options}
    return client.post(SYNC, json=body)


@pytest.fixture
def result(client: Any, copernicus: FakeFetch) -> dict[str, Any]:
    response = analyse(client, include_contours=True)
    assert response.status_code == 200, response.text
    return response.json()


def inside(lon: float, lat: float, bbox: list[float] = DURG_BBOX) -> bool:
    return bbox[0] <= lon <= bbox[2] and bbox[1] <= lat <= bbox[3]


class TestTheThreeResultsForADrawnArea:
    def test_there_is_a_recommended_site(self, result: dict[str, Any]) -> None:
        assert result["candidate_sites"], result["warnings"]
        assert result["recommended_site"] == result["candidate_sites"][0]

    def test_the_summary_names_all_three(self, result: dict[str, Any]) -> None:
        summary = result["summary"]
        assert summary["available"] is True
        assert summary["pond_location"]["lat"] and summary["pond_location"]["lon"]
        assert summary["catchment_area_ha"] > 0
        assert summary["expected_water_volume_m3"] > 0

    def test_the_summary_agrees_with_the_site_it_summarises(self, result: dict[str, Any]) -> None:
        summary, site = result["summary"], result["recommended_site"]
        assert summary["pond_location"] == {
            "lat": site["location"]["lat"],
            "lon": site["location"]["lon"],
        }
        assert summary["catchment_area_ha"] == site["catchment"]["metrics"]["area_ha"]
        assert summary["pond_capacity_m3"]["live"] == site["pond"]["recommended"]["live_storage_m3"]

    def test_with_enrichment_off_the_volume_is_the_ponds_storage(
        self, result: dict[str, Any]
    ) -> None:
        """No rainfall was fetched, so the inflow cannot cap it -- and it says so."""
        summary = result["summary"]
        assert summary["expected_water_volume_limited_by"] == "storage"
        assert "rainfall was unavailable" in summary["expected_water_volume_basis"]

    def test_every_site_says_what_it_would_collect(self, result: dict[str, Any]) -> None:
        """Per site, so every marker on the map can carry its own volume."""
        for site in result["candidate_sites"]:
            water = site["expected_water"]
            assert water["volume_m3"] > 0
            assert water["limited_by"] in ("storage", "inflow")
            assert water["basis"]

    def test_the_summary_volume_is_the_recommended_sites_own(self, result: dict[str, Any]) -> None:
        water = result["recommended_site"]["expected_water"]
        assert result["summary"]["expected_water_volume_m3"] == water["volume_m3"]

    def test_every_site_lies_inside_the_drawn_rectangle(self, result: dict[str, Any]) -> None:
        """The buffer is there for catchments, not sites."""
        for site in result["candidate_sites"]:
            loc = site["location"]
            assert inside(loc["lon"], loc["lat"]), f"site {site['rank']} at {loc} is outside"

    def test_each_site_carries_its_catchment_polygon(self, result: dict[str, Any]) -> None:
        for site in result["candidate_sites"]:
            geometry = site["catchment"]["geometry"]
            assert geometry["type"] in ("Polygon", "MultiPolygon")


class TestTheResponseSaysWhereTheTerrainCameFrom:
    def test_same_shape_as_a_contour_upload_with_no_contour_map(
        self, result: dict[str, Any]
    ) -> None:
        assert result["contour_map"] is None
        for key in ("interpolated_terrain", "suitability", "environment", "explanation"):
            assert key in result

    def test_terrain_source_is_copernicus_at_30_m(self, result: dict[str, Any]) -> None:
        src = result["terrain_source"]
        assert src["kind"] == "copernicus_glo30"
        assert src["resolution_m"] == 30.0
        assert src["bounds_4326"] == DURG_BBOX
        assert src["buffer_m"] == 500.0
        assert src["cells_inside_area"] > 0
        assert "30 m" in src["note"]

    def test_the_input_echoes_the_rectangle_and_its_area(self, result: dict[str, Any]) -> None:
        assert result["input"]["bbox"] == DURG_BBOX
        assert result["input"]["area_km2"] == pytest.approx(8.53, rel=0.01)
        assert "filename" not in result["input"]

    def test_the_area_of_interest_is_the_rectangle_not_the_buffer(
        self, result: dict[str, Any]
    ) -> None:
        ring = result["area_of_interest"]["coordinates"][0]
        lons, lats = {p[0] for p in ring}, {p[1] for p in ring}
        assert lons == {DURG_BBOX[0], DURG_BBOX[2]}
        assert lats == {DURG_BBOX[1], DURG_BBOX[3]}

    def test_the_grid_report_describes_the_fetched_grid(self, result: dict[str, Any]) -> None:
        grid = result["interpolated_terrain"]
        assert grid["grid_resolution_m"] == 30.0
        assert grid["grid_resolution_derived"] is False
        assert "Copernicus" in grid["interpolation_method"]

    def test_the_timings_name_a_terrain_stage_not_parse_and_interpolate(
        self, result: dict[str, Any]
    ) -> None:
        stages = set(result["stage_timings_s"])
        assert "terrain" in stages
        assert not stages & {"parse", "interpolate"}

    def test_display_contours_are_traced_from_the_grid(self, result: dict[str, Any]) -> None:
        contours = result["contours"]
        assert contours["type"] == "FeatureCollection"
        assert contours["features"]

    def test_inside_india_there_is_no_location_warning(self, result: dict[str, Any]) -> None:
        assert not any("outside India" in w for w in result["warnings"])


class TestFollowUpsOnTheSameTerrain:
    """The `dem_id` works exactly as it does after a contour upload."""

    def test_a_dem_id_is_returned(self, result: dict[str, Any]) -> None:
        assert result["dem_id"]

    def test_the_contours_route_traces_from_the_grid(
        self, client: Any, result: dict[str, Any]
    ) -> None:
        response = client.get(f"/api/v1/terrain/contour-map/{result['dem_id']}/contours")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["traced_from_grid"] is True
        assert body["geojson"]["features"]

    def test_the_drainage_network(self, client: Any, result: dict[str, Any]) -> None:
        response = client.post("/api/v1/hydrology/streams", data={"dem_id": result["dem_id"]})
        assert response.status_code == 200, response.text

    def test_the_catchment_above_the_recommended_site(
        self, client: Any, result: dict[str, Any]
    ) -> None:
        loc = result["recommended_site"]["location"]
        response = client.post(
            "/api/v1/hydrology/catchment",
            data={"dem_id": result["dem_id"], "lon": loc["lon"], "lat": loc["lat"]},
        )
        assert response.status_code == 200, response.text


class TestTheTerrainIsFetchedOnce:
    def test_a_second_run_over_the_same_box_reads_the_disk_cache(
        self, client: Any, copernicus: FakeFetch
    ) -> None:
        first = analyse(client).json()
        second = analyse(client).json()
        assert first["terrain_source"]["cached"] is False
        assert second["terrain_source"]["cached"] is True
        assert copernicus.calls == 1
        assert first["summary"] == second["summary"]


class TestRejectionsCostNoFetch:
    def test_an_inverted_box_is_a_400_naming_bbox(self, client: Any, copernicus: FakeFetch) -> None:
        response = analyse(client, [DURG_BBOX[2], DURG_BBOX[1], DURG_BBOX[0], DURG_BBOX[3]])
        assert response.status_code == 400
        body = response.json()
        assert body["type"] == "/errors/validation"
        assert body["errors"][0]["field"] == "bbox"
        assert "inverted" in body["detail"]
        assert copernicus.calls == 0

    def test_a_box_over_the_cap_is_a_413_with_both_figures(
        self, client: Any, copernicus: FakeFetch
    ) -> None:
        response = analyse(client, [81.0, 21.0, 81.2, 21.2])  # ~460 km²
        assert response.status_code == 413
        body = response.json()
        assert body["type"] == "/errors/aoi-too-large"
        assert body["max_km2"] == 100.0
        assert body["area_km2"] > 100.0
        assert copernicus.calls == 0

    def test_a_box_too_small_is_a_400(self, client: Any, copernicus: FakeFetch) -> None:
        response = analyse(client, [81.29, 21.25, 81.291, 21.251])
        assert response.status_code == 400
        assert "minimum" in response.json()["detail"]

    @pytest.mark.parametrize(
        "bbox",
        [
            pytest.param([81.28, 21.24, 81.31], id="three_values"),
            pytest.param("81.28,21.24,81.31,21.26", id="a_string"),
        ],
    )
    def test_a_malformed_bbox_is_a_400(self, client: Any, copernicus: FakeFetch, bbox: Any) -> None:
        assert analyse(client, bbox).status_code == 400
        assert copernicus.calls == 0

    def test_no_bbox_at_all_is_a_400(self, client: Any, copernicus: FakeFetch) -> None:
        assert client.post(SYNC, json={"enrich": False}).status_code == 400

    def test_a_copernicus_outage_is_a_503_that_offers_the_upload_instead(
        self, client: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def down(*_args: Any, **_kwargs: Any) -> Any:
            raise ProviderUnavailableError("copernicus_dem_glo30", "connection timed out")

        monkeypatch.setattr(copernicus_aws, "fetch_dem", down)
        response = analyse(client)
        assert response.status_code == 503
        body = response.json()
        assert body["type"] == "/errors/provider-unavailable"
        assert "upload a contour map" in body["detail"]


class TestSitesStayInsideWhenMoreAreAskedFor:
    """Reported from the browser: eleven sites asked for over a 15.7 km² box,
    and two natural depressions in the 500 m margin came back outside it.
    Depressions whose every cell was vetoed fell back to *any* of their cells."""

    def test_a_hollow_in_the_margin_is_not_proposed(
        self, client: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from dataclasses import replace

        import numpy as np

        from app.tests.area_fakes import fake_dem

        def hollow_in_the_margin(bounds: Any, **kwargs: Any) -> Any:
            dem = fake_dem(bounds, **kwargs)
            rows, cols = dem.shape
            rr, cc = np.mgrid[0:rows, 0:cols]
            # Rows 3-9 are inside the 500 m margin north of the box (~17 rows).
            bowl = 3.0 * np.clip(1.0 - np.hypot(rr - 6, cc - cols * 0.3) / 5.0, 0.0, None)
            return replace(dem, elevation=(dem.elevation - bowl).astype(np.float32))

        monkeypatch.setattr(copernicus_aws, "fetch_dem", hollow_in_the_margin)
        body = client.post(
            "/api/v1/analyzeArea", json={"bbox": DURG_BBOX, "enrich": False, "max_sites": 11}
        ).json()
        assert body["candidate_sites"], body.get("warnings")
        for site in body["candidate_sites"]:
            loc = site["location"]
            assert inside(loc["lon"], loc["lat"]), f"site {site['rank']} at {loc} is outside"


class TestNoSiteInsideTheArea:
    def test_a_hillside_too_steep_to_dig_is_a_200_with_no_sites_and_the_reason(
        self, client: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Not an error: the terrain was analysed, it just holds nowhere to dig.
        A uniform 20 % grade, against the 8 % a pond may be built on."""
        from dataclasses import replace

        import numpy as np

        from app.tests.area_fakes import fake_dem

        def steep(bounds: Any, **kwargs: Any) -> Any:
            dem = fake_dem(bounds, **kwargs)
            rows, _cols = dem.shape
            grade = 0.20 * dem.cell_size_m * np.arange(rows, dtype=np.float32)[::-1, None]
            return replace(dem, elevation=(280.0 + grade + 0 * dem.elevation).astype(np.float32))

        monkeypatch.setattr(copernicus_aws, "fetch_dem", steep)
        response = analyse(client)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["candidate_sites"] == []
        assert body["summary"]["available"] is False
        assert body["summary"]["reason"]
        assert any("inside the selected area" in w for w in body["warnings"])


class TestOverOpenWater:
    def test_no_terrain_is_a_422_that_says_to_draw_over_land(
        self, client: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def sea(*_args: Any, **_kwargs: Any) -> Any:
            raise copernicus_aws.NoTerrainError("copernicus_dem_glo30", "open water")

        monkeypatch.setattr(copernicus_aws, "fetch_dem", sea)
        response = analyse(client, [65.50, 10.50, 65.53, 10.53])
        assert response.status_code == 422
        body = response.json()
        assert body["type"] == "/errors/unanswerable"
        assert "over land" in body["detail"]


class TestOutsideIndia:
    def test_it_is_analysed_with_a_warning(self, client: Any, copernicus: FakeFetch) -> None:
        # A village-sized box in Kenya.
        response = analyse(client, [36.80, -1.30, 36.83, -1.27])
        assert response.status_code == 200, response.text
        assert any("outside India" in w for w in response.json()["warnings"])


class TestTheJobForm:
    """`POST /analysis/area`: the same analysis, polled, as the browser runs it."""

    def start(self, client: Any, bbox: Any = None) -> Any:
        return client.post(ASYNC, json={"bbox": bbox or DURG_BBOX, "enrich": False})

    def run(self, client: Any) -> tuple[str, dict[str, Any]]:
        started = self.start(client)
        assert started.status_code == 202, started.text
        job_id = started.json()["job_id"]
        for _ in range(60):
            status = client.get(f"/api/v1/analysis/{job_id}/status").json()
            if status["is_terminal"]:
                return job_id, status
        raise AssertionError(f"job {job_id} never settled: {status}")

    def test_it_is_accepted_with_somewhere_to_poll(
        self, client: Any, copernicus: FakeFetch
    ) -> None:
        body = self.start(client).json()
        assert body["status_url"] == f"/api/v1/analysis/{body['job_id']}/status"
        assert body["result_url"] == f"/api/v1/analysis/{body['job_id']}/result"

    def test_its_steps_are_the_area_steps(self, client: Any, copernicus: FakeFetch) -> None:
        _, status = self.run(client)
        assert status["state"] in ("done", "partial")
        assert [s["name"] for s in status["steps"]] == [s.name for s in AREA_STEPS]
        assert sum(s["weight"] for s in status["steps"]) == pytest.approx(1.0, abs=1e-6)

    def test_its_result_is_the_synchronous_result(self, client: Any, copernicus: FakeFetch) -> None:
        job_id, _ = self.run(client)
        body = client.get(f"/api/v1/analysis/{job_id}/result").json()["result"]
        assert body["terrain_source"]["kind"] == "copernicus_glo30"
        assert body["summary"]["available"] is True
        assert body["dem_id"]

    def test_a_bad_box_is_refused_before_a_job_exists(
        self, client: Any, copernicus: FakeFetch
    ) -> None:
        response = self.start(client, [81.0, 21.0, 81.2, 21.2])
        assert response.status_code == 413
        assert copernicus.calls == 0

    def test_a_copernicus_outage_fails_the_terrain_step_by_name(
        self, client: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def down(*_args: Any, **_kwargs: Any) -> Any:
            raise ProviderUnavailableError("copernicus_dem_glo30", "connection timed out")

        monkeypatch.setattr(copernicus_aws, "fetch_dem", down)
        _, status = self.run(client)
        assert status["state"] == "failed"
        outcomes = {s["name"]: s["outcome"] for s in status["steps"]}
        assert outcomes["terrain"] == "failed"
        # The same words the synchronous route uses, not an exception name.
        assert status["error"]["type"] == "/errors/provider-unavailable"
        assert "upload a contour map" in status["error"]["detail"]
        assert status["error"]["trace_id"]

    def test_open_sea_fails_the_job_with_draw_over_land(
        self, client: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def sea(*_args: Any, **_kwargs: Any) -> Any:
            raise copernicus_aws.NoTerrainError("copernicus_dem_glo30", "open water")

        monkeypatch.setattr(copernicus_aws, "fetch_dem", sea)
        _, status = self.run(client)
        assert status["state"] == "failed"
        assert status["error"]["type"] == "/errors/unanswerable"
        assert "over land" in status["error"]["detail"]


class TestTheReportAndExportOfADrawnArea:
    """A drawn area has no file, so nothing downstream may assume one."""

    def test_the_report_names_the_area_and_its_terrain(self, result: dict[str, Any]) -> None:
        from app.services import report

        context = report.build_context(result)
        assert context["site_label"].startswith("selected area 21.2398")
        assert "8.53 km²" in context["site_label"]
        terrain = context["source_rows"][0]
        assert "GLO-30" in terrain["provider"] and "30 m" in terrain["provider"]
        assert "contour map" not in terrain["provider"]

    def test_the_report_states_the_30_m_limitation_not_the_contour_one(
        self, result: dict[str, Any]
    ) -> None:
        from app.services import report

        html = report.render_html(result)
        assert "global elevation model" in html
        assert "interpolated from contour lines" not in html

    def test_a_contour_upload_still_gets_the_contour_wording(self) -> None:
        """The branch must not flip the other way."""
        from app.services import report
        from app.services.contour_analysis import ContourAnalysisOptions, analyze_contour_map
        from app.tests.golden.test_source_interchangeability import valley_kml

        body = analyze_contour_map(
            valley_kml(), "valley.kml", ContourAnalysisOptions(enrich=False)
        ).as_dict()
        context = report.build_context(body)
        assert context["site_label"] == "valley.kml"
        assert "Uploaded contour map" in context["source_rows"][0]["provider"]

    def test_the_geojson_export_of_an_area_job(self, client: Any, copernicus: FakeFetch) -> None:
        job_id, _ = TestTheJobForm().run(client)
        response = client.get(f"/api/v1/export/{job_id}", params={"format": "geojson"})
        assert response.status_code == 200, response.text
        assert response.json()["features"]


@pytest.mark.network
class TestAgainstTheRealBucket:
    """The live Copernicus read, over the sample sheet's own extent.

    Measured once by `scripts/compare_area_vs_contour.py`: 30.3 m of relief at
    30 m against 31.0 m on the 5 m contour surface. Bounds here are loose on
    purpose -- this checks the read works and lands on the right terrain, not
    the third decimal of a global model.
    """

    def test_the_sample_extent_reads_one_tile_and_finds_sites(self, client: Any) -> None:
        response = client.post(SYNC, json={"bbox": DURG_BBOX, "enrich": False})
        assert response.status_code == 200, response.text
        body = response.json()
        src = body["terrain_source"]
        assert src["tiles_used"] == ["Copernicus_DSM_COG_10_N21_00_E081_00_DEM"]
        assert src["coverage_pct"] == 100.0
        assert 20.0 <= src["relief_m"] <= 45.0
        assert body["summary"]["available"] is True

    def test_the_arabian_sea_has_no_tile_and_is_a_422(self, client: Any) -> None:
        response = client.post(SYNC, json={"bbox": [65.50, 10.50, 65.53, 10.53], "enrich": False})
        assert response.status_code == 422, response.text


class TestTheResultKnowsItsJob:
    """Export and the report are keyed by job id. The UI built its export link
    from `analysis_id` instead and got a 404 on every run, contour or area."""

    def test_the_result_carries_its_job_id(self, client: Any, copernicus: FakeFetch) -> None:
        job_id, _ = TestTheJobForm().run(client)
        body = client.get(f"/api/v1/analysis/{job_id}/result").json()["result"]
        assert body["job_id"] == job_id
        assert body["job_id"] != body["analysis_id"]

    def test_the_export_link_built_from_the_result_works(
        self, client: Any, copernicus: FakeFetch
    ) -> None:
        job_id, _ = TestTheJobForm().run(client)
        body = client.get(f"/api/v1/analysis/{job_id}/result").json()["result"]
        assert client.get(f"/api/v1/export/{body['job_id']}").status_code == 200
        assert client.get(f"/api/v1/export/{body['analysis_id']}").status_code == 404
