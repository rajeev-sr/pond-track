"""The village endpoints, against a seeded database.

Every case is one a user would hit. The spellings are the ones people actually
type -- `kutelabhata` without the `h`, `कुटेलाभाठा` in Devanagari -- and the
namesake case is real: three villages called Khapri sit in Durg district.

Skipped unless the village index has been seeded, because that is a 250 MB
download and a deliberate step (`make seed`), not something a test should do.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration

SEARCH = "/api/v1/villages/search"


@pytest.fixture(scope="module")
def client() -> Iterator[object]:
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c


@pytest.fixture(scope="module")
def database() -> None:
    """A reachable database. Separate from `seeded` on purpose.

    Some of these tests only need the query to reach postgres and come back
    empty -- looking up an unknown id, for instance. Requiring seeded data for
    those would skip them needlessly; requiring *nothing* let one of them run
    with no database at all, where the endpoint raised a connection error instead
    of the 404 it was asserting.
    """
    from sqlalchemy.exc import OperationalError

    from app.db.session import get_sessionmaker

    try:
        with get_sessionmaker()() as session:
            session.execute(text("SELECT 1"))
    except OperationalError as exc:
        pytest.skip(f"no database reachable: {exc.__class__.__name__}")


@pytest.fixture(scope="module")
def seeded(database) -> None:  # type: ignore[no-untyped-def]
    """A seeded village index, and how to get one if it is missing."""
    from app.db.session import get_sessionmaker

    with get_sessionmaker()() as session:
        count = session.execute(
            text("SELECT count(*) FROM villages WHERE state = 'chhattisgarh'")
        ).scalar_one()
    if not count:
        pytest.skip("village index not seeded; run `make seed STATE=chhattisgarh`")


def search(client, q: str, **params: object) -> dict:  # type: ignore[no-untyped-def]
    response = client.get(SEARCH, params={"q": q, **params})
    assert response.status_code == 200, response.text
    return response.json()


class TestFindingAVillageHoweverItIsSpelled:
    """HLD CH-24, end to end."""

    @pytest.mark.parametrize(
        ("typed", "why"),
        [
            ("kutelabhatha", "the register's own spelling"),
            ("kutelabhata", "the aspirated h is often dropped"),
            ("Kutelabhaata", "vowel length is written inconsistently"),
            ("KUTELABHATA", "case is irrelevant"),
            ("कुटेलाभाठा", "Devanagari input (HLD NFR-15)"),
        ],
    )
    def test_it_is_the_top_result(self, client, seeded, typed: str, why: str) -> None:  # type: ignore[no-untyped-def]
        body = search(client, typed, district="durg", limit=3)
        assert body["results"], f"nothing found for {typed!r} ({why})"
        top = body["results"][0]
        assert top["name"] == "Kutelabhatha", why
        assert top["similarity"] == 1.0, why

    def test_the_response_shows_what_was_actually_searched_on(self, client, seeded) -> None:  # type: ignore[no-untyped-def]
        """The caller can see the fold, rather than guessing why a match happened."""
        body = search(client, "Kutelabhaata", district="durg")
        assert body["query"] == "Kutelabhaata"
        assert body["query_folded"] == "kutelabat"

    def test_it_distinguishes_an_exact_spelling_from_a_folded_match(
        self, client, seeded
    ) -> None:  # type: ignore[no-untyped-def]
        exact = search(client, "kutelabhatha", district="durg")["results"][0]
        folded = search(client, "kutelabhata", district="durg")["results"][0]
        assert exact["matched_by"] == "exact"
        assert folded["matched_by"] == "folded"
        assert exact["id"] == folded["id"], "both should reach the same village"

    def test_the_canonical_key_is_a_code(self, client, seeded) -> None:  # type: ignore[no-untyped-def]
        """CH-24: identify a village by its code, never by its name."""
        top = search(client, "kutelabhatha", district="durg")["results"][0]
        assert top["identifiers"]["shrid"] == "11-22-409-03317-442569"
        assert top["identifiers"]["census_2011_id"] == "442569"


class TestNamesakes:
    """Three villages called Khapri in one district. Picking one would be wrong."""

    def test_every_one_is_returned(self, client, seeded) -> None:  # type: ignore[no-untyped-def]
        body = search(client, "khapri", district="durg", limit=10)
        khapris = [r for r in body["results"] if r["name"] == "Khapri"]
        assert len(khapris) >= 3, "namesakes were collapsed"

    def test_each_carries_its_hierarchy(self, client, seeded) -> None:  # type: ignore[no-untyped-def]
        body = search(client, "khapri", district="durg", limit=10)
        khapris = [r for r in body["results"] if r["name"] == "Khapri"]
        for r in khapris:
            assert r["display"].startswith("Khapri, ")
            assert r["hierarchy"]["subdistrict"], "no sub-district to disambiguate by"

    def test_where_the_hierarchy_is_not_enough_it_says_so(self, client, seeded) -> None:  # type: ignore[no-untyped-def]
        """Durg holds two villages called Khapri *in the same sub-district*.

        Name plus place genuinely cannot separate those, which is precisely why
        HLD CH-24 makes the code the canonical key. The response flags the pairs
        so a caller knows to show the identifier rather than presenting two
        identical-looking rows.
        """
        body = search(client, "khapri", district="durg", limit=10)
        khapris = [r for r in body["results"] if r["name"] == "Khapri"]

        by_display: dict[str, list[dict]] = {}
        for r in khapris:
            by_display.setdefault(r["display"], []).append(r)
        collisions = {d: rs for d, rs in by_display.items() if len(rs) > 1}
        assert collisions, "expected same-name same-place villages in Durg"

        for display, rows in collisions.items():
            for r in rows:
                assert r["hierarchy_is_ambiguous"] is True, display
            codes = {r["identifiers"]["census_2011_id"] for r in rows}
            assert len(codes) == len(rows), f"{display} has rows the code cannot separate either"

        for display, rows in by_display.items():
            if len(rows) == 1:
                assert rows[0]["hierarchy_is_ambiguous"] is False, display

    def test_the_order_is_stable_across_identical_requests(self, client, seeded) -> None:  # type: ignore[no-untyped-def]
        """Equal-scoring results must not shuffle between calls."""
        first = [r["id"] for r in search(client, "khapri", district="durg", limit=10)["results"]]
        second = [r["id"] for r in search(client, "khapri", district="durg", limit=10)["results"]]
        assert first == second


class TestFilters:
    def test_a_filter_is_resolved_to_the_stored_spelling(self, client, seeded) -> None:  # type: ignore[no-untyped-def]
        """`district=DURG` must reach rows stored as `durg`."""
        for spelling in ("durg", "Durg", "DURG"):
            body = search(client, "kutelabhatha", district=spelling)
            assert body["filters"]["district"] == "durg", spelling
            assert body["count"] >= 1, spelling

    def test_an_unknown_filter_narrows_to_nothing_rather_than_being_ignored(
        self, client, seeded
    ) -> None:  # type: ignore[no-untyped-def]
        """Silently dropping a filter the caller set would be worse than zero results."""
        body = search(client, "kutelabhatha", district="atlantis")
        assert body["count"] == 0

    def test_a_filtered_miss_says_where_the_village_actually_is(
        self, client, seeded
    ) -> None:  # type: ignore[no-untyped-def]
        """The wrong filter is the commonest cause of "my village is missing".

        Searching Raipur for a Durg village does not come back empty -- trigram
        matching finds five vaguely similar names there. Without a note the
        caller would reasonably conclude the village is not in the register.
        """
        body = search(client, "kutelabhatha", district="raipur")
        assert not any(r["name"] == "Kutelabhatha" for r in body["results"])
        assert body["note"], "weak matches were returned with no explanation"
        assert "durg" in body["note"].lower(), body["note"]
        assert "filter" in body["note"].lower(), body["note"]

    def test_a_strong_match_needs_no_note(self, client, seeded) -> None:  # type: ignore[no-untyped-def]
        """Explaining a result that speaks for itself is just noise."""
        assert search(client, "kutelabhatha", district="durg")["note"] is None


class TestBoundariesDoNotLie:
    """The failure that would quietly corrupt every downstream area figure."""

    @pytest.fixture
    def village_id(self, client, seeded) -> str:  # type: ignore[no-untyped-def]
        return search(client, "kutelabhatha", district="durg")["results"][0]["id"]

    def test_the_boundary_states_what_it_outlines(self, client, village_id: str) -> None:  # type: ignore[no-untyped-def]
        body = client.get(f"/api/v1/villages/{village_id}/boundary").json()
        assert body["available"] is True
        assert body["represents"] == "subdistrict"
        assert body["is_village_boundary"] is False
        assert body["caveat"], "a coarser-than-asked-for polygon came with no warning"
        assert "not the village boundary" in body["caveat"]

    def test_the_geometry_is_geojson_in_4326(self, client, village_id: str) -> None:  # type: ignore[no-untyped-def]
        geometry = client.get(f"/api/v1/villages/{village_id}/boundary").json()["geometry"]
        assert geometry["type"] in ("Polygon", "MultiPolygon")
        lon, lat = geometry["coordinates"][0][0][0]
        assert 68 < lon < 98, "longitude is outside India — wrong axis order?"
        assert 6 < lat < 38, "latitude is outside India"

    def test_the_focus_point_admits_it_is_approximate(self, client, village_id: str) -> None:  # type: ignore[no-untyped-def]
        """A tehsil centroid is fine for framing a map and wrong as a location."""
        focus = client.get(f"/api/v1/villages/{village_id}").json()["focus"]
        assert focus["approximate"] is True
        assert focus["is_centre_of"] == "subdistrict"

    def test_the_detail_view_agrees_with_the_boundary_view(
        self, client, village_id: str
    ) -> None:  # type: ignore[no-untyped-def]
        detail = client.get(f"/api/v1/villages/{village_id}").json()["boundary"]
        boundary = client.get(f"/api/v1/villages/{village_id}/boundary").json()
        assert detail["is_village_boundary"] == boundary["is_village_boundary"]
        assert detail["level"] == boundary["represents"]
        assert detail["area_ha"] == boundary["area_ha"]


class TestImagery:
    @pytest.fixture
    def village_id(self, client, seeded) -> str:  # type: ignore[no-untyped-def]
        return search(client, "kutelabhatha", district="durg")["results"][0]["id"]

    def test_it_returns_a_usable_tile_template(self, client, village_id: str) -> None:  # type: ignore[no-untyped-def]
        imagery = client.get(f"/api/v1/villages/{village_id}/imagery").json()["imagery"]
        for placeholder in ("{z}", "{x}", "{y}"):
            assert placeholder in imagery["tile_url_template"]
        assert imagery["attribution"], "attribution is a licence condition"

    def test_the_bounds_enclose_the_focus_point(self, client, village_id: str) -> None:  # type: ignore[no-untyped-def]
        body = client.get(f"/api/v1/villages/{village_id}/imagery").json()
        min_lon, min_lat, max_lon, max_lat = body["bounds_4326"]
        assert min_lon < body["focus"]["lon"] < max_lon
        assert min_lat < body["focus"]["lat"] < max_lat

    def test_the_bounds_say_what_they_enclose(self, client, village_id: str) -> None:  # type: ignore[no-untyped-def]
        body = client.get(f"/api/v1/villages/{village_id}/imagery").json()
        assert body["bounds_of"] == "subdistrict"


class TestReverseGeocode:
    def test_the_sample_map_centroid_resolves_to_durg(self, client, seeded) -> None:  # type: ignore[no-untyped-def]
        """The supplied contour map's own centre, which is Khapri in Durg tehsil."""
        body = client.get(
            "/api/v1/villages/resolve", params={"lon": 81.29703, "lat": 21.25170}
        ).json()
        assert body["matched"]["level"] == "subdistrict"
        assert body["matched"]["name"] == "Durg"
        assert body["matched"]["district"] == "Durg"
        assert body["villages_recorded_here"] > 0

    def test_it_states_its_own_precision(self, client, seeded) -> None:  # type: ignore[no-untyped-def]
        body = client.get(
            "/api/v1/villages/resolve", params={"lon": 81.29703, "lat": 21.25170}
        ).json()
        assert "sub-district" in body["precision"]

    def test_a_point_outside_every_seeded_area_is_unanswerable(self, client, seeded) -> None:  # type: ignore[no-untyped-def]
        response = client.get("/api/v1/villages/resolve", params={"lon": 0, "lat": 0})
        assert response.status_code == 422
        assert "outside" in response.json()["detail"]

    def test_place_names_are_spelled_the_same_way_everywhere(self, client, seeded) -> None:  # type: ignore[no-untyped-def]
        """Two sources, two spellings of one state, was a real inconsistency.

        The register writes `chhattisgarh`; geoBoundaries writes `Chhattīsgarh`.
        A caller comparing the two endpoints saw a mismatch that meant nothing.
        """
        resolved = client.get(
            "/api/v1/villages/resolve", params={"lon": 81.29703, "lat": 21.25170}
        ).json()["matched"]["state"]
        village_id = search(client, "kutelabhatha", district="durg")["results"][0]["id"]
        detailed = client.get(f"/api/v1/villages/{village_id}").json()["hierarchy"]["state"]
        assert resolved == detailed == "Chhattisgarh"


class TestBadInput:
    def test_an_unknown_id_is_a_404(self, client, database) -> None:  # type: ignore[no-untyped-def]
        r = client.get("/api/v1/villages/00000000-0000-0000-0000-000000000000")
        assert r.status_code == 404

    def test_a_malformed_id_is_a_400(self, client) -> None:  # type: ignore[no-untyped-def]
        assert client.get("/api/v1/villages/not-a-uuid").status_code == 400

    def test_punctuation_only_is_refused_with_the_right_reason(self, client) -> None:  # type: ignore[no-untyped-def]
        r = client.get(SEARCH, params={"q": "!!!"})
        assert r.status_code == 400
        assert "no letters or digits" in r.json()["detail"]

    def test_a_query_that_folds_away_says_so_instead(self, client) -> None:  # type: ignore[no-untyped-def]
        """`a` is a letter; the fold's trailing-vowel rule removes it.

        Reporting that as "contains no letters" would tell the user something
        they can plainly see is false.
        """
        r = client.get(SEARCH, params={"q": "a"})
        assert r.status_code == 400
        assert "folded away" in r.json()["detail"]
        assert "no letters" not in r.json()["detail"]

    def test_the_limit_is_capped(self, client, seeded) -> None:  # type: ignore[no-untyped-def]
        assert client.get(SEARCH, params={"q": "ram", "limit": 10_000}).status_code == 400


class TestGramPanchayats:
    """The Panchayat is what disambiguates namesakes the hierarchy cannot.

    It is also the only LGD code available in open data, and the body that
    actually plans and builds MGNREGA water works — so it belongs on a pond
    proposal even though it is not the village key HLD E2 asks for.
    """

    @pytest.fixture(scope="class")
    def panchayats_seeded(self, seeded) -> None:  # type: ignore[no-untyped-def]
        from app.db.session import get_sessionmaker

        with get_sessionmaker()() as session:
            count = session.execute(text("SELECT count(*) FROM gram_panchayats")).scalar_one()
        if not count:
            pytest.skip("panchayats not seeded; re-run `make seed`")

    def test_the_village_key_is_not_filled_with_a_panchayat_code(
        self, client, panchayats_seeded
    ) -> None:  # type: ignore[no-untyped-def]
        """The failure this whole model exists to prevent.

        A Panchayat covers a cluster of villages. Writing its LGD code into the
        field the design calls "the canonical village key" would make that field
        identify something coarser than a village, in a place nobody re-checks.
        """
        top = search(client, "kutelabhatha", district="durg")["results"][0]
        assert top["identifiers"]["lgd_code"] is None
        assert top["gram_panchayats"], "the panchayat is missing entirely"
        assert top["gram_panchayats"][0]["lgd_code"], "the panchayat has no code"

    def test_it_resolves_every_ambiguous_namesake(self, client, panchayats_seeded) -> None:  # type: ignore[no-untyped-def]
        """Where name plus hierarchy collides, the Panchayat must differ."""
        results = search(client, "khapri", district="durg", limit=10)["results"]
        ambiguous = [r for r in results if r["hierarchy_is_ambiguous"]]
        assert ambiguous, "expected same-name same-place villages in Durg"

        by_display: dict[str, list[dict]] = {}
        for r in ambiguous:
            by_display.setdefault(r["display"], []).append(r)
        for display, rows in by_display.items():
            names = [gp["name"] for r in rows for gp in r["gram_panchayats"]]
            assert len(names) == len(rows), f"{display}: a row has no panchayat"
            assert len(set(names)) == len(names), (
                f"{display}: the panchayats do not distinguish these villages either " f"({names})"
            )

    def test_the_census_2001_code_is_carried(self, client, panchayats_seeded) -> None:  # type: ignore[no-untyped-def]
        """It rides in the same file, so collecting it costs nothing (HLD E3)."""
        village_id = search(client, "kutelabhatha", district="durg")["results"][0]["id"]
        identifiers = client.get(f"/api/v1/villages/{village_id}").json()["identifiers"]
        assert identifiers["census_2001_id"], "the 2001 code was not stored"
        assert identifiers["census_2001_id"] != identifiers["census_2011_id"]

    def test_the_detail_and_search_views_agree(self, client, panchayats_seeded) -> None:  # type: ignore[no-untyped-def]
        top = search(client, "kutelabhatha", district="durg")["results"][0]
        detail = client.get(f"/api/v1/villages/{top['id']}").json()
        assert detail["gram_panchayats"] == top["gram_panchayats"]
