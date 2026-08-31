"""The OSM context provider, parsed offline.

Overpass is rate-limited and slow, so nothing here touches the network. The
cases are the ones that would actually bite: the bbox axis order, which Overpass
takes back-to-front relative to every other provider here; ways clipped by the
window, which arrive with null nodes; relations, whose geometry hides one level
down; and the classification order, where a building inside a residential
polygon has to stay a building.
"""

from __future__ import annotations

import pytest

from app.providers.base import ProviderUnavailableError
from app.providers.vector import overpass

# A window around Durg, in this codebase's (min_lon, min_lat, max_lon, max_lat).
DURG = (81.20, 21.15, 81.30, 21.25)


def way(osm_id: int, tags: dict[str, str], coords: list[tuple[float, float]]) -> dict:
    """An `out geom` way element, coordinates given as (lon, lat)."""
    return {
        "type": "way",
        "id": osm_id,
        "tags": tags,
        "geometry": [{"lat": lat, "lon": lon} for lon, lat in coords],
    }


SQUARE = [(81.21, 21.16), (81.212, 21.16), (81.212, 21.162), (81.21, 21.162), (81.21, 21.16)]
LINE = [(81.22, 21.17), (81.24, 21.18)]


class TestTheQueryAddressesTheRightGround:
    def test_the_bbox_is_reordered_to_overpass_s_axis_order(self) -> None:
        """Overpass wants south,west,north,east; every other provider here is lon-first.

        Getting this backwards asks for a window off the coast of Somalia and
        returns an empty result rather than an error, so it is asserted directly.
        """
        query = overpass.build_query(DURG)
        assert "21.15,81.2,21.25,81.3" in query, query

    def test_it_asks_for_geometry_inline(self) -> None:
        # Without `out geom` every way is a list of node ids and the provider
        # would need a second round trip per feature.
        assert "out geom;" in overpass.build_query(DURG)

    def test_it_asks_for_all_five_classes(self) -> None:
        query = overpass.build_query(DURG)
        for clause in ('"building"', '"highway"', '"natural"="water"', '"waterway"', '"landuse"'):
            assert clause in query, f"{clause} missing from the query"

    def test_the_bbox_is_set_once_globally_not_per_clause(self) -> None:
        """A cost decision, measured: the per-clause form returned HTTP 504.

        overpass-api.de timed out in queue on a 3 x 2.6 km window when the bbox
        was repeated on every clause; the global form answers in about two
        seconds.
        """
        query = overpass.build_query(DURG)
        assert query.count("21.15,81.2,21.25,81.3") == 1, "the bbox is repeated per clause"
        assert "[bbox:" in query

    def test_it_does_not_ask_for_relations(self) -> None:
        """The relation clauses are what pushed the query into a 504."""
        assert "relation[" not in overpass.build_query(DURG)

    def test_every_endpoint_has_global_coverage(self) -> None:
        """A regional mirror answers an Indian query with 200 and zero elements.

        overpass.osm.ch carries Switzerland only, so it reported a town near
        Durg as having no buildings, roads or water at all -- an exclusion layer
        failing open.
        """
        assert not any("osm.ch" in e for e in overpass.ENDPOINTS)

    def test_a_degenerate_window_is_refused(self) -> None:
        with pytest.raises(ValueError, match="degenerate"):
            overpass.build_query((81.3, 21.15, 81.2, 21.25))

    def test_a_district_sized_window_is_refused(self) -> None:
        with pytest.raises(ValueError, match="limit"):
            overpass.build_query((80.0, 20.0, 81.0, 21.0))


class TestItClassifiesWhatItReads:
    def test_a_building_in_a_residential_block_is_still_a_building(self) -> None:
        assert overpass.classify({"building": "house", "landuse": "residential"}) == "building"

    def test_a_reservoir_is_water_not_committed_land(self) -> None:
        assert overpass.classify({"landuse": "reservoir"}) == "water"

    def test_a_field_drain_is_water(self) -> None:
        assert overpass.classify({"waterway": "drain"}) == "water"

    def test_a_highway_and_a_field_track_are_not_the_same_class(self) -> None:
        """They get different buffers downstream, so they cannot share a class."""
        assert overpass.classify({"highway": "secondary"}) == "road"
        assert overpass.classify({"highway": "track"}) == "track"

    def test_farmland_is_not_an_exclusion(self) -> None:
        # Farmland is precisely where a village pond goes; excluding it would
        # leave nowhere to build.
        assert overpass.classify({"landuse": "farmland"}) is None
        assert overpass.classify({"landuse": "orchard"}) is None

    def test_an_untagged_way_is_ignored(self) -> None:
        assert overpass.classify({}) is None


class TestItParsesGeometry:
    def test_each_class_lands_in_its_own_bucket(self) -> None:
        ctx = overpass.parse(
            {
                "elements": [
                    way(1, {"building": "yes"}, SQUARE),
                    way(2, {"highway": "secondary"}, LINE),
                    way(3, {"highway": "track"}, LINE),
                    way(4, {"natural": "water"}, SQUARE),
                    way(5, {"landuse": "industrial"}, SQUARE),
                    way(6, {"landuse": "farmland"}, SQUARE),
                ]
            }
        )
        assert ctx.counts() == {
            "buildings": 1,
            "roads": 1,
            "tracks": 1,
            "water": 1,
            "blocking_landuse": 1,
        }
        assert ctx.total == 5, "farmland should not have been counted as an exclusion"

    def test_coordinates_come_back_lon_lat(self) -> None:
        ctx = overpass.parse({"elements": [way(1, {"building": "yes"}, SQUARE)]})
        assert ctx.buildings[0].rings[0][0] == (81.21, 21.16)

    def test_a_closed_ring_reads_as_an_area_and_a_line_does_not(self) -> None:
        ctx = overpass.parse(
            {
                "elements": [
                    way(1, {"building": "yes"}, SQUARE),
                    way(2, {"highway": "primary"}, LINE),
                ]
            }
        )
        assert ctx.buildings[0].is_area
        assert not ctx.roads[0].is_area

    def test_a_way_clipped_by_the_window_keeps_its_surviving_nodes(self) -> None:
        """Overpass pads a clipped way with nulls; they must not become (0, 0)."""
        element = way(1, {"highway": "primary"}, LINE)
        element["geometry"].insert(1, None)  # type: ignore[arg-type]
        ctx = overpass.parse({"elements": [element]})
        assert ctx.roads[0].rings[0] == ((81.22, 21.17), (81.24, 21.18))

    def test_a_relation_contributes_its_outer_members(self) -> None:
        ctx = overpass.parse(
            {
                "elements": [
                    {
                        "type": "relation",
                        "id": 99,
                        "tags": {"natural": "water"},
                        "members": [
                            {
                                "type": "way",
                                "role": "outer",
                                "geometry": [{"lat": lat, "lon": lon} for lon, lat in SQUARE],
                            },
                            {
                                "type": "way",
                                "role": "inner",
                                "geometry": [{"lat": lat, "lon": lon} for lon, lat in SQUARE],
                            },
                        ],
                    }
                ]
            }
        )
        assert len(ctx.water) == 1
        assert len(ctx.water[0].rings) == 1, "the inner ring should not be treated as an outer one"

    def test_a_feature_with_no_usable_geometry_is_dropped(self) -> None:
        ctx = overpass.parse({"elements": [{"type": "way", "id": 1, "tags": {"building": "yes"}}]})
        assert ctx.total == 0


class TestItSurvivesOneMirrorBeingDown:
    def test_it_falls_through_to_the_next_endpoint(self, monkeypatch) -> None:
        # Only the main query counts: a successful fetch also issues the
        # water-relation supplement to the same mirror, which is not an attempt
        # at a *different* endpoint and must not be read as one.
        seen: list[str] = []

        def fake_post(provider, url, body=None, timeout=0.0, form=None, headers=None):
            assert form and "data" in form, "the query must be form-encoded as data="
            assert headers and "User-Agent" in headers, "Overpass wants a named client"
            if "relation[" in form["data"]:
                return {"elements": []}
            seen.append(url)
            if len(seen) == 1:
                raise ProviderUnavailableError("overpass", "HTTP 429 from overpass-api.de")
            return {"elements": [way(1, {"building": "yes"}, SQUARE)]}

        monkeypatch.setattr(overpass, "post_json", fake_post)
        ctx = overpass.fetch_osm_context(DURG)
        assert len(seen) == 2, "a rate-limited mirror should not end the attempt"
        assert ctx.endpoint == overpass.ENDPOINTS[1]
        assert len(ctx.buildings) == 1

    def test_every_mirror_failing_names_them_all(self, monkeypatch) -> None:
        def always_fail(provider, url, body=None, timeout=0.0, form=None, headers=None):
            raise ProviderUnavailableError("overpass", "HTTP 504")

        monkeypatch.setattr(overpass, "post_json", always_fail)
        with pytest.raises(ProviderUnavailableError) as exc:
            overpass.fetch_osm_context(DURG)
        assert exc.value.detail.count("HTTP 504") == len(overpass.ENDPOINTS)

    def test_the_caveat_travels_with_the_data(self, monkeypatch) -> None:
        """OSM silence is not evidence of open ground, and the response must say so."""
        monkeypatch.setattr(overpass, "post_json", lambda *a, **k: {"elements": []})
        block = overpass.fetch_osm_context(DURG).as_dict()
        assert "not evidence of open ground" in block["caveat"]
        assert block["source"]["licence"].startswith("ODbL")


class TestAnOverpassRemarkIsNotAnEmptyArea:
    """Overpass reports query errors and timeouts at HTTP 200 with a `remark`.

    Read as a success, that becomes "this village has no buildings" -- which
    silently drops every exclusion and reports the whole sheet as available.
    This is what actually happened against Durg: the raw-body POST earned a 406
    from one mirror and an empty remark payload from another, and the endpoint
    cheerfully returned zero buildings for a town.
    """

    def test_a_remark_with_no_elements_is_a_failure(self, monkeypatch) -> None:
        monkeypatch.setattr(
            overpass,
            "post_json",
            lambda *a, **k: {"remark": "runtime error: Query timed out in queue", "elements": []},
        )
        with pytest.raises(ProviderUnavailableError, match="timed out"):
            overpass.fetch_osm_context(DURG)

    def test_a_remark_alongside_real_elements_is_still_used(self, monkeypatch) -> None:
        """A partial answer with a warning is better than no answer."""
        monkeypatch.setattr(
            overpass,
            "post_json",
            lambda *a, **k: {
                "remark": "some tiles were not available",
                "elements": [way(1, {"building": "yes"}, SQUARE)],
            },
        )
        assert len(overpass.fetch_osm_context(DURG).buildings) == 1

    def test_a_genuinely_empty_area_is_still_an_empty_success(self, monkeypatch) -> None:
        monkeypatch.setattr(overpass, "post_json", lambda *a, **k: {"elements": []})
        assert overpass.fetch_osm_context(DURG).total == 0


def water_relation(osm_id: int, tags: dict[str, str], coords: list[tuple[float, float]]) -> dict:
    """An `out geom` multipolygon relation, geometry one level down in members."""
    return {
        "type": "relation",
        "id": osm_id,
        "tags": tags,
        "members": [
            {
                "type": "way",
                "role": "outer",
                "geometry": [{"lat": lat, "lon": lon} for lon, lat in coords],
            }
        ],
    }


class TestWaterRelationsAreFetchedSeparately:
    """A big river's areal extent is very often a multipolygon relation.

    `build_query` asks for ways only, because adding `relation[...]` across every
    feature kind timed out a 3 x 2.6 km window (HTTP 504). But missing a relation
    does not mis-buffer the river -- it loses the river altogether, which is the
    worst direction for an exclusion layer to fail. So the relations are a second
    request whose failure is non-fatal: the point is that adding coverage must
    never introduce a new way to lose what already worked.
    """

    def test_the_relation_query_asks_only_for_water(self) -> None:
        q = overpass.build_water_relation_query(DURG)
        assert 'relation["natural"="water"]' in q
        assert 'relation["waterway"]' in q
        assert '"building"' not in q and '"highway"' not in q, "cost is the whole reason"

    def test_the_relation_query_keeps_the_bbox_axis_order(self) -> None:
        """Overpass wants south,west,north,east -- swapped exactly once, as elsewhere."""
        assert "[bbox:21.15,81.2,21.25,81.3]" in overpass.build_water_relation_query(DURG)

    def test_a_relation_river_reaches_the_context(self, monkeypatch) -> None:
        def fake_post(provider, url, body=None, timeout=0.0, form=None, headers=None):
            q = (form or {}).get("data", "")
            if "relation[" in q:
                return {
                    "elements": [water_relation(99, {"natural": "water", "water": "river"}, SQUARE)]
                }
            return {"elements": [way(1, {"building": "yes"}, SQUARE)]}

        monkeypatch.setattr(overpass, "post_json", fake_post)
        ctx = overpass.fetch_osm_context(DURG)
        assert len(ctx.water) == 1, "the relation river must be present"
        assert ctx.water[0].osm_type == "relation"
        assert ctx.water_relations is True

    def test_a_relation_already_present_as_a_way_is_not_duplicated(self, monkeypatch) -> None:
        dup = water_relation(99, {"natural": "water"}, SQUARE)

        def fake_post(provider, url, body=None, timeout=0.0, form=None, headers=None):
            return {"elements": [dup]}

        monkeypatch.setattr(overpass, "post_json", fake_post)
        ctx = overpass.fetch_osm_context(DURG)
        assert len(ctx.water) == 1

    def test_the_relation_query_failing_does_not_lose_the_ways(self, monkeypatch) -> None:
        """The whole reason it is a separate request."""

        def fake_post(provider, url, body=None, timeout=0.0, form=None, headers=None):
            if "relation[" in (form or {}).get("data", ""):
                raise ProviderUnavailableError("overpass", "HTTP 504 timed out in queue")
            return {"elements": [way(1, {"building": "yes"}, SQUARE)]}

        monkeypatch.setattr(overpass, "post_json", fake_post)
        ctx = overpass.fetch_osm_context(DURG)
        assert len(ctx.buildings) == 1, "a relation timeout must not cost us the ways"
        assert ctx.water_relations is False, "and it must be reported, not assumed"

    def test_a_relation_remark_is_not_read_as_no_relations(self, monkeypatch) -> None:
        def fake_post(provider, url, body=None, timeout=0.0, form=None, headers=None):
            if "relation[" in (form or {}).get("data", ""):
                return {"remark": "runtime error: Query timed out", "elements": []}
            return {"elements": [way(1, {"building": "yes"}, SQUARE)]}

        monkeypatch.setattr(overpass, "post_json", fake_post)
        ctx = overpass.fetch_osm_context(DURG)
        assert ctx.water_relations is False

    def test_the_flag_travels_in_the_provenance_block(self, monkeypatch) -> None:
        monkeypatch.setattr(overpass, "post_json", lambda *a, **k: {"elements": []})
        assert overpass.fetch_osm_context(DURG).as_dict()["water_relations_fetched"] is True

    def test_a_window_whose_only_feature_is_a_relation_lake_is_not_read_as_empty(
        self, monkeypatch
    ) -> None:
        """No ways at all, so the main query looks empty -- but the water is there.

        The empty-window corroboration rule must not skip the relation
        supplement, or the one place a pond must not go would be the one place
        nothing is fetched.
        """

        def fake_post(provider, url, body=None, timeout=0.0, form=None, headers=None):
            if "relation[" in (form or {}).get("data", ""):
                return {"elements": [water_relation(5, {"natural": "water"}, SQUARE)]}
            return {"elements": []}

        monkeypatch.setattr(overpass, "post_json", fake_post)
        ctx = overpass.fetch_osm_context(DURG)
        assert len(ctx.water) == 1, "a relation-only water body must still arrive"
        assert ctx.water_relations is True
