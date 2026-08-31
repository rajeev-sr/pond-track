"""HTTP-level tests for the contour endpoints (MC-10, MC-11).

Built on *synthetic* contour KMLs rather than the supplied sample, so the suite
runs anywhere and proves the endpoint derives its answer from whatever arrives.
`TestGeneralisationOverHttp` is the end-to-end form of the guarantee: the same
terrain, with its elevation stored four different ways, must give the same
catchment through the API.
"""

from __future__ import annotations

import io
from typing import Any

import pytest

from app.tests.synthetic_kml import build_kml, build_kmz, concentric_rings, tilted_plane

pytestmark = pytest.mark.integration

ANALYZE = "/api/v1/analyzeContour"
FIND = "/api/v1/findCatchment"
UPLOAD = "/api/v1/terrain/contour-map"


def valley(depth: float = 4.0) -> list[tuple[float, list[tuple[float, float]]]]:
    """Nested rings wide enough to survive the feasibility masks at 5 m cells."""
    return concentric_rings(
        center=(77.0, 21.0),
        levels=tuple(100.0 + i for i in range(12)),
        step_deg=0.0009,
        vertices=72,
    )


def post(client: Any, path: str, kml: bytes, name: str = "c.kml", **opts: Any) -> Any:
    """POST a contour map, with enrichment off unless a test asks for it.

    The suite must run with no network: enrichment reaches SoilGrids, the ESA
    WorldCover bucket and Open-Meteo, which would make every test slow, flaky and
    dependent on someone else's uptime. The enriched path has its own
    `network`-marked tests below, deselected by default.
    """
    opts.setdefault("enrich", False)
    data = {k: str(v) for k, v in opts.items()}
    return client.post(path, files={"file": (name, io.BytesIO(kml), "application/xml")}, data=data)


class TestAnalyzeContour:
    def test_returns_a_complete_analysis(self, client) -> None:  # type: ignore[no-untyped-def]
        r = post(client, ANALYZE, build_kml(valley()), max_sites=3)
        assert r.status_code == 200, r.text
        d = r.json()
        for key in (
            "analysis_id",
            "generated_at",
            "elapsed_s",
            "stage_timings_s",
            "input",
            "contour_map",
            "interpolated_terrain",
            "area_of_interest",
            "suitability",
            "recommended_site",
            "candidate_sites",
            "warnings",
        ):
            assert key in d, f"missing {key}"

    def test_reports_what_it_read_from_the_file(self, client) -> None:  # type: ignore[no-untyped-def]
        d = post(client, ANALYZE, build_kml(valley())).json()
        cm = d["contour_map"]
        assert cm["elevation_source"] == "uploaded_contour_map"
        assert cm["elevation_strategy"] == "placemark_name"
        assert cm["lines_parsed"] == 12
        assert cm["contour_interval_m"] == pytest.approx(1.0)
        assert cm["levels"] == 12
        assert 32601 <= cm["working_crs_epsg"] <= 32760

    def test_grid_resolution_is_derived_and_flagged(self, client) -> None:  # type: ignore[no-untyped-def]
        d = post(client, ANALYZE, build_kml(valley())).json()
        it = d["interpolated_terrain"]
        assert it["grid_resolution_derived"] is True
        assert it["grid_resolution_m"] > 0
        assert it["mean_contour_spacing_m"] > 0

    def test_explicit_cell_size_is_honoured(self, client) -> None:  # type: ignore[no-untyped-def]
        d = post(client, ANALYZE, build_kml(valley()), cell_size_m=8).json()
        assert d["interpolated_terrain"]["grid_resolution_m"] == 8.0
        assert d["interpolated_terrain"]["grid_resolution_derived"] is False

    def test_tier_and_missing_layers_are_declared(self, client) -> None:  # type: ignore[no-untyped-def]
        d = post(client, ANALYZE, build_kml(valley())).json()
        s = d["suitability"]
        assert s["analysis_tier"] == "terrain_only"
        assert "rainfall" in s["layers_unavailable"]
        assert sum(s["criteria_weights"].values()) == pytest.approx(1.0, abs=1e-3)
        assert s["tier_meaning"]
        assert d["environment"]["enrichment_skipped"] is True

    def test_recommended_site_carries_a_catchment(self, client) -> None:  # type: ignore[no-untyped-def]
        d = post(client, ANALYZE, build_kml(valley())).json()
        site = d["recommended_site"]
        assert site is not None, d["warnings"]
        m = site["catchment"]["metrics"]
        assert m["area_ha"] > 0
        assert m["cell_count"] > 0
        assert m["outlet_accumulation_cells"] == m["cell_count"]  # the invariant
        assert site["catchment"]["geometry"]["type"] in ("Polygon", "MultiPolygon")

    def test_every_criterion_is_explained(self, client) -> None:  # type: ignore[no-untyped-def]
        site = post(client, ANALYZE, build_kml(valley())).json()["recommended_site"]
        names = {c["criterion"] for c in site["criteria_breakdown"]}
        assert names == {"flow_accumulation", "slope", "depression_depth", "plan_concavity"}
        total = sum(c["contribution"] for c in site["criteria_breakdown"])
        assert total * 100 == pytest.approx(site["suitability_score"], abs=0.2)

    def test_max_sites_is_honoured_as_a_form_field(self, client) -> None:  # type: ignore[no-untyped-def]
        """Regression: options were bound to the query string, so `-F max_sites=2`
        was silently ignored on a multipart endpoint."""
        d = post(client, ANALYZE, build_kml(valley()), max_sites=2, min_separation_m=50)
        assert len(d.json()["candidate_sites"]) <= 2
        assert d.json()["input"]["options"]["max_sites"] == 2

    def test_geometry_can_be_omitted(self, client) -> None:  # type: ignore[no-untyped-def]
        d = post(client, ANALYZE, build_kml(valley()), include_catchment_geometry=False).json()
        assert d["recommended_site"]["catchment"].get("geometry") is None

    def test_contours_can_be_echoed(self, client) -> None:  # type: ignore[no-untyped-def]
        d = post(client, ANALYZE, build_kml(valley()), include_contours=True).json()
        assert d["contours"]["type"] == "FeatureCollection"
        assert len(d["contours"]["features"]) == 12

    def test_kmz_is_accepted(self, client) -> None:  # type: ignore[no-untyped-def]
        r = post(client, ANALYZE, build_kmz(valley()), name="c.kmz")
        assert r.status_code == 200, r.text
        assert r.json()["contour_map"]["lines_parsed"] == 12

    def test_stage_timings_cover_the_pipeline(self, client) -> None:  # type: ignore[no-untyped-def]
        t = post(client, ANALYZE, build_kml(valley())).json()["stage_timings_s"]
        assert set(t) == {
            "parse",
            "interpolate",
            "condition",
            "flow_routing",
            "enrichment",
            "siting",
            "catchments",
        }


class TestGeneralisationOverHttp:
    """★ Same terrain, four elevation conventions, one answer -- through the API."""

    @pytest.mark.parametrize(
        "strategy",
        ["coordinate_z", "extended_data", "placemark_name", "folder_name"],
    )
    def test_each_strategy_is_detected(self, client, strategy: str) -> None:  # type: ignore[no-untyped-def]
        d = post(client, ANALYZE, build_kml(valley(), strategy)).json()
        assert d["contour_map"]["elevation_strategy"] == strategy

    def test_all_four_give_the_same_catchment(self, client) -> None:  # type: ignore[no-untyped-def]
        areas = {}
        for strategy in ("coordinate_z", "extended_data", "placemark_name", "folder_name"):
            d = post(
                client,
                ANALYZE,
                build_kml(valley(), strategy),
                include_catchment_geometry=False,
                cell_size_m=6,
            ).json()
            site = d["recommended_site"]
            assert site is not None, f"{strategy}: {d['warnings']}"
            areas[strategy] = site["catchment"]["metrics"]["area_ha"]
        assert len(set(areas.values())) == 1, f"strategies disagree: {areas}"

    def test_a_different_landform_also_works(self, client) -> None:  # type: ignore[no-untyped-def]
        """Geometry the sample does not contain: a uniform planar slope."""
        lines = tilted_plane(
            levels=tuple(10.0 * i for i in range(1, 9)),
            step_deg=0.0012,
            span_deg=0.012,
            vertices=40,
        )
        r = post(client, ANALYZE, build_kml(lines), cell_size_m=8, min_upstream_ha=0.1)
        assert r.status_code == 200, r.text
        assert r.json()["contour_map"]["lines_parsed"] == 8


class TestFindCatchmentAlias:
    def test_alias_matches_analyze(self, client) -> None:  # type: ignore[no-untyped-def]
        kml = build_kml(valley())
        a = post(client, ANALYZE, kml, include_catchment_geometry=False, cell_size_m=6).json()
        b = post(client, FIND, kml, include_catchment_geometry=False, cell_size_m=6).json()
        assert a["recommended_site"]["catchment"]["metrics"] == (
            b["recommended_site"]["catchment"]["metrics"]
        )


class TestErrorHandling:
    def test_points_only_kml_is_422_with_a_specific_reason(self, client) -> None:  # type: ignore[no-untyped-def]
        kml = (
            b'<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
            b"<Placemark><name>1</name><Point><coordinates>77,21</coordinates></Point>"
            b"</Placemark></Document></kml>"
        )
        r = post(client, ANALYZE, kml)
        assert r.status_code == 422
        body = r.json()
        assert body["type"] == "/errors/unanswerable"
        assert "no contour LineStrings" in body["detail"]

    def test_single_elevation_is_422(self, client) -> None:  # type: ignore[no-untyped-def]
        flat = [
            (100.0, [(77.0, 21.0), (77.001, 21.0)]),
            (100.0, [(77.0, 21.001), (77.001, 21.001)]),
        ]
        assert post(client, ANALYZE, build_kml(flat)).status_code == 422

    def test_wrong_extension_is_400(self, client) -> None:  # type: ignore[no-untyped-def]
        r = post(client, ANALYZE, build_kml(valley()), name="terrain.tif")
        assert r.status_code == 400
        assert "does not look like a contour map" in r.json()["detail"]

    def test_empty_file_is_400(self, client) -> None:  # type: ignore[no-untyped-def]
        r = post(client, ANALYZE, b"")
        assert r.status_code == 400
        assert "empty" in r.json()["detail"]

    def test_out_of_range_option_is_400_naming_the_field(self, client) -> None:  # type: ignore[no-untyped-def]
        r = post(client, ANALYZE, build_kml(valley()), max_sites=99)
        assert r.status_code == 400
        assert r.json()["errors"][0]["field"] == "max_sites"

    def test_missing_file_is_400(self, client) -> None:  # type: ignore[no-untyped-def]
        assert client.post(ANALYZE, data={"max_sites": "3"}).status_code == 400

    def test_every_error_is_problem_json(self, client) -> None:  # type: ignore[no-untyped-def]
        r = post(client, ANALYZE, b"not xml at all")
        for field in ("type", "title", "status", "detail", "instance", "trace_id"):
            assert field in r.json()


class TestTwoStepFlow:
    def test_upload_then_fetch_contours(self, client) -> None:  # type: ignore[no-untyped-def]
        up = post(client, UPLOAD, build_kml(valley()))
        assert up.status_code == 200, up.text
        body = up.json()
        assert body["contour_map"]["lines_parsed"] == 12
        assert body["area_of_interest"]["type"] == "Polygon"

        got = client.get(f"{UPLOAD}/{body['dem_id']}/contours")
        assert got.status_code == 200
        d = got.json()
        assert d["contour_interval_m"] == pytest.approx(1.0)
        assert len(d["geojson"]["features"]) == 12
        assert d["geojson"]["features"][0]["properties"]["elevation_m"] == 100.0

    def test_limit_returns_the_lowest_contours(self, client) -> None:  # type: ignore[no-untyped-def]
        dem_id = post(client, UPLOAD, build_kml(valley())).json()["dem_id"]
        d = client.get(f"{UPLOAD}/{dem_id}/contours?limit=3").json()
        elevs = [f["properties"]["elevation_m"] for f in d["geojson"]["features"]]
        assert elevs == sorted(elevs)
        assert len(elevs) == 3

    def test_unknown_dem_id_is_404_with_guidance(self, client) -> None:  # type: ignore[no-untyped-def]
        r = client.get(f"{UPLOAD}/does-not-exist/contours")
        assert r.status_code == 404
        assert "POST /api/v1/terrain/contour-map" in r.json()["detail"]


class TestOpenApiDocumentation:
    def test_options_are_form_fields_not_query_params(self, client) -> None:  # type: ignore[no-untyped-def]
        """Regression guard for the binding bug: a multipart endpoint's options
        belong in the form body, or `curl -F` calls are silently ignored."""
        op = client.get("/openapi.json").json()["paths"][ANALYZE]["post"]
        assert not op.get("parameters"), "options leaked back into the query string"
        schema_ref = op["requestBody"]["content"]["multipart/form-data"]["schema"]["$ref"]
        name = schema_ref.rsplit("/", 1)[-1]
        props = client.get("/openapi.json").json()["components"]["schemas"][name]["properties"]
        for expected in ("file", "max_sites", "cell_size_m", "include_catchment_geometry"):
            assert expected in props

    def test_endpoints_are_documented(self, client) -> None:  # type: ignore[no-untyped-def]
        paths = client.get("/openapi.json").json()["paths"]
        for p in (ANALYZE, FIND, UPLOAD):
            assert p in paths
            assert paths[p]["post"]["summary"]
            assert paths[p]["post"]["description"]


class TestTierLadder:
    """Degradation is a defined ladder, not a failure mode (HLD §6.10.5)."""

    def test_enrichment_off_gives_terrain_only_and_says_why(self, client) -> None:  # type: ignore[no-untyped-def]
        d = post(client, ANALYZE, build_kml(valley()), enrich=False).json()
        env = d["environment"]
        assert env["analysis_tier"] == "terrain_only"
        assert env["enrichment_skipped"] is True
        assert set(env["layers_unavailable"]) == {
            "land_use_land_cover",
            "soil_hydrologic_group",
            "rainfall",
        }
        assert env["soil"] is None and env["land_cover"] is None and env["rainfall"] is None

    def test_terrain_only_still_answers_the_graded_question(self, client) -> None:  # type: ignore[no-untyped-def]
        """The floor of the ladder must still give a site and a catchment."""
        site = post(client, ANALYZE, build_kml(valley()), enrich=False).json()["recommended_site"]
        assert site is not None
        assert site["catchment"]["metrics"]["area_ha"] > 0
        assert site["pond"]["available"] is True
        assert site["pond"]["recommended"]["gross_capacity_m3"] > 0

    def test_runoff_absent_offline_with_a_stated_reason(self, client) -> None:  # type: ignore[no-untyped-def]
        site = post(client, ANALYZE, build_kml(valley()), enrich=False).json()["recommended_site"]
        assert site["runoff"]["available"] is False
        assert "rainfall" in site["runoff"]["reason"]

    def test_pond_names_the_binding_constraint(self, client) -> None:  # type: ignore[no-untyped-def]
        """The actionable part: *which* limit produced this size (HLD §6.9 Step 7)."""
        pond = post(client, ANALYZE, build_kml(valley()), enrich=False).json()["recommended_site"][
            "pond"
        ]
        assert pond["binding_constraint"] in pond["constraints_evaluated"]
        assert pond["footprint"]["usable_buildable_area_m2"] > 0
        assert len(pond["stage_storage_curve"]) > 1

    def test_stage_storage_curve_is_monotonic(self, client) -> None:  # type: ignore[no-untyped-def]
        curve = post(client, ANALYZE, build_kml(valley()), enrich=False).json()["recommended_site"][
            "pond"
        ]["stage_storage_curve"]
        vols = [p["storage_volume_m3"] for p in curve]
        assert vols == sorted(vols)


@pytest.mark.network
class TestEnrichedPath:
    """The enriched path end to end. Deselected by default: hits SoilGrids, the
    ESA WorldCover bucket and Open-Meteo. Run with `-m network`.

    These are live third parties, and they do go down -- ISRIC returned 503 for
    the better part of an hour while this suite was being written. A test that
    demands tier `full` reports somebody else's outage as our defect, so the
    invariants that must hold *at whatever tier was reached* are asserted
    unconditionally, and only the assertions that need a specific layer are
    skipped when that layer is genuinely unavailable. A degraded run still fails
    the test if we mislabel the tier, lose a reason, or report a number we no
    longer have the inputs for.
    """

    @pytest.fixture(scope="class")
    def enriched(self):  # type: ignore[no-untyped-def]
        """One live analysis, shared by every assertion in this class.

        Class-scoped on purpose: each of these tests inspects a different part
        of the same response, and re-running the analysis per test would mean
        four rounds of live provider calls -- slow, and needlessly rude to
        services that are answering us for free. It builds its own client
        because the shared `client` fixture is function-scoped.
        """
        from fastapi.testclient import TestClient

        from app.main import create_app

        # A real Indian location, so the providers have data to return.
        lines = concentric_rings(
            center=(81.2966, 21.2519),
            levels=tuple(270.0 + i for i in range(12)),
            step_deg=0.0009,
            vertices=72,
        )
        with TestClient(create_app()) as c:
            return post(c, ANALYZE, build_kml(lines), enrich=True, max_sites=1).json()

    def test_the_tier_matches_the_layers_actually_obtained(self, enriched) -> None:  # type: ignore[no-untyped-def]
        env = enriched["environment"]
        assert env["analysis_tier"] in ("full", "no_soil_lulc", "terrain_only")

        has_soil = env["soil"] is not None
        has_lulc = env["land_cover"] is not None
        has_rain = env["rainfall"] is not None
        expected = (
            "full"
            if (has_soil and has_lulc and has_rain)
            else "no_soil_lulc" if has_rain else "terrain_only"
        )
        assert env["analysis_tier"] == expected, (
            f"tier {env['analysis_tier']!r} does not match the layers present "
            f"(soil={has_soil}, lulc={has_lulc}, rainfall={has_rain})"
        )

    def test_every_missing_layer_is_accounted_for(self, enriched) -> None:  # type: ignore[no-untyped-def]
        """Nothing vanishes silently: a layer is either used or explained."""
        env = enriched["environment"]
        explained = {f["layer"] for f in env["provider_failures"]}
        for layer in env["layers_unavailable"]:
            assert layer in explained, f"{layer} is unavailable with no reason given"
        for failure in env["provider_failures"]:
            assert failure["reason"], f"{failure['layer']} failed with an empty reason"
        assert not (set(env["layers_used"]) & set(env["layers_unavailable"]))

    def test_runoff_is_reported_only_when_it_can_be_computed(self, enriched) -> None:  # type: ignore[no-untyped-def]
        """Runoff needs rainfall. Without it the field says so instead of guessing."""
        runoff = enriched["recommended_site"]["runoff"]
        if enriched["environment"]["rainfall"] is None:
            assert runoff["available"] is False
            assert runoff["reason"]
            return
        assert runoff["available"] is True
        cn = runoff["curve_number"]["composite_cn_amc2"]
        assert 30 <= cn <= 100
        c = runoff["annual_mean"]["runoff_coefficient"]
        assert 0.05 <= c <= 0.7, f"implausible runoff coefficient {c}"

    def test_the_full_tier_carries_every_measured_layer(self, enriched) -> None:  # type: ignore[no-untyped-def]
        env = enriched["environment"]
        if env["analysis_tier"] != "full":
            pytest.skip(f"a provider was unavailable: {env['provider_failures']}")
        assert env["soil"]["hydrologic_soil_group"] in ("A", "B", "C", "D")
        assert env["rainfall"]["annual"]["mean_mm"] > 0
        assert env["land_cover"]["dominant_class"]
        site = enriched["recommended_site"]
        assert "land_availability" in {b["criterion"] for b in site["criteria_breakdown"]}
