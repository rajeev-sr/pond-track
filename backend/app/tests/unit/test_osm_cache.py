"""The OSM window cache.

Nothing here touches Overpass. The cases that matter are the ones that would
make the cache worse than no cache: a half-written file parsing as an empty
window (which would report a town as having no buildings), a stale entry served
forever, and a bbox that differs in the seventh decimal fetching all over again.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from app.providers.vector import osm_cache
from app.providers.vector.overpass import OsmContext, OsmFeature

BOUNDS = (81.2814044952393, 21.2398224433387, 81.3126468658447, 21.2635806472203)
RING = ((81.29, 21.25), (81.291, 21.25), (81.291, 21.251), (81.29, 21.251), (81.29, 21.25))


def feature(kind: str = "building", osm_id: int = 1) -> OsmFeature:
    return OsmFeature(
        kind=kind,  # type: ignore[arg-type]
        osm_type="way",
        osm_id=osm_id,
        tags={"building": "house"},
        rings=(RING,),
    )


def context() -> OsmContext:
    return OsmContext(
        buildings=[feature("building", 1)],
        roads=[feature("road", 2)],
        tracks=[feature("track", 3)],
        water=[feature("water", 4)],
        landuse=[feature("landuse", 5)],
        endpoint="https://overpass-api.de/api/interpreter",
    )


class TestARoundTrip:
    def test_what_goes_in_comes_back_out(self, tmp_path: Path) -> None:
        osm_cache.write(tmp_path, BOUNDS, context())
        back = osm_cache.read(tmp_path, BOUNDS)
        assert back is not None
        assert back.counts() == context().counts()
        assert back.endpoint == "https://overpass-api.de/api/interpreter"

    def test_geometry_survives(self, tmp_path: Path) -> None:
        osm_cache.write(tmp_path, BOUNDS, context())
        back = osm_cache.read(tmp_path, BOUNDS)
        assert back is not None
        assert back.buildings[0].rings[0][0] == pytest.approx(RING[0])
        assert back.buildings[0].is_area, "a closed ring must still read as an area"

    def test_tags_survive(self, tmp_path: Path) -> None:
        osm_cache.write(tmp_path, BOUNDS, context())
        back = osm_cache.read(tmp_path, BOUNDS)
        assert back is not None
        assert back.buildings[0].tags == {"building": "house"}

    def test_a_miss_is_none_not_an_error(self, tmp_path: Path) -> None:
        assert osm_cache.read(tmp_path, BOUNDS) is None


class TestTheKey:
    def test_a_centimetre_of_difference_shares_an_entry(self, tmp_path: Path) -> None:
        """Otherwise every re-upload re-fetches for a difference no one can see."""
        nudged = (BOUNDS[0] + 1e-7, BOUNDS[1], BOUNDS[2], BOUNDS[3])
        osm_cache.write(tmp_path, BOUNDS, context())
        assert osm_cache.read(tmp_path, nudged) is not None

    def test_a_genuinely_different_window_does_not(self, tmp_path: Path) -> None:
        elsewhere = (77.0, 28.0, 77.05, 28.05)
        osm_cache.write(tmp_path, BOUNDS, context())
        assert osm_cache.read(tmp_path, elsewhere) is None

    def test_the_path_fans_out_by_the_first_two_hex(self, tmp_path: Path) -> None:
        path = osm_cache.path_for(tmp_path, BOUNDS)
        key = osm_cache.cache_key(BOUNDS)
        assert path.parent.name == key[:2]
        assert path.name == f"{key}.json"


class TestFreshness:
    def test_a_stale_entry_is_a_miss(self, tmp_path: Path) -> None:
        osm_cache.write(tmp_path, BOUNDS, context())
        path = osm_cache.path_for(tmp_path, BOUNDS)
        payload = json.loads(path.read_text())
        payload["fetched_at"] = time.time() - (osm_cache.DEFAULT_TTL_S + 60)
        path.write_text(json.dumps(payload))
        assert osm_cache.read(tmp_path, BOUNDS) is None

    def test_an_entry_inside_the_ttl_is_served(self, tmp_path: Path) -> None:
        osm_cache.write(tmp_path, BOUNDS, context())
        path = osm_cache.path_for(tmp_path, BOUNDS)
        payload = json.loads(path.read_text())
        payload["fetched_at"] = time.time() - (osm_cache.DEFAULT_TTL_S / 2)
        path.write_text(json.dumps(payload))
        assert osm_cache.read(tmp_path, BOUNDS) is not None

    def test_an_unrecognised_cache_version_is_a_miss(self, tmp_path: Path) -> None:
        """A schema this parser does not know must not be guessed at.

        Note the rule is `READABLE_VERSIONS`, not "the current one": v1 windows
        are read on purpose, because their ways are still good and refusing them
        offline would lose working protection to gain none. See
        `TestTheRelationSupplementSurvivesTheCache`.
        """
        osm_cache.write(tmp_path, BOUNDS, context())
        path = osm_cache.path_for(tmp_path, BOUNDS)
        payload = json.loads(path.read_text())
        payload["version"] = max(osm_cache.READABLE_VERSIONS) + 1
        path.write_text(json.dumps(payload))
        assert osm_cache.read(tmp_path, BOUNDS) is None


class TestItFailsSafe:
    def test_a_truncated_file_is_a_miss_not_an_empty_window(self, tmp_path: Path) -> None:
        """An empty window would report a town as having no buildings at all."""
        path = osm_cache.path_for(tmp_path, BOUNDS)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"version": 1, "fetched_at": 99999999999, "features": [{"kind"')
        assert osm_cache.read(tmp_path, BOUNDS) is None

    def test_a_write_is_atomic(self, tmp_path: Path) -> None:
        """No .tmp is left behind, so a later read cannot pick up a partial file."""
        osm_cache.write(tmp_path, BOUNDS, context())
        assert list(osm_cache.path_for(tmp_path, BOUNDS).parent.glob("*.tmp")) == []

    def test_an_unwritable_store_does_not_raise(self, tmp_path: Path) -> None:
        blocked = tmp_path / "file-not-a-dir"
        blocked.write_text("")
        # Never raises: a cache failure must not turn into a 500 on the endpoint.
        osm_cache.write(blocked, BOUNDS, context())

    def test_an_unknown_feature_kind_is_skipped_not_fatal(self, tmp_path: Path) -> None:
        osm_cache.write(tmp_path, BOUNDS, context())
        path = osm_cache.path_for(tmp_path, BOUNDS)
        payload = json.loads(path.read_text())
        payload["features"].append({"kind": "spaceport", "rings": [[[81.0, 21.0], [81.1, 21.1]]]})
        path.write_text(json.dumps(payload))
        back = osm_cache.read(tmp_path, BOUNDS)
        assert back is not None
        assert back.total == 5, "the known features should still be returned"


class TestFetchCached:
    def test_the_first_call_fetches_and_the_second_does_not(self, tmp_path: Path) -> None:
        calls: list[tuple] = []

        def fake_fetch(bounds):  # type: ignore[no-untyped-def]
            calls.append(bounds)
            return context()

        first, cached = osm_cache.fetch_cached(BOUNDS, tmp_path, fetch=fake_fetch)
        assert not cached and len(calls) == 1
        second, cached = osm_cache.fetch_cached(BOUNDS, tmp_path, fetch=fake_fetch)
        assert cached and len(calls) == 1, "the second call went to the network"
        assert second.counts() == first.counts()

    def test_a_provider_failure_propagates_rather_than_caching_nothing(
        self, tmp_path: Path
    ) -> None:
        """Caching an empty result on failure would poison the entry for a fortnight."""

        def boom(bounds):  # type: ignore[no-untyped-def]
            raise RuntimeError("overpass down")

        with pytest.raises(RuntimeError):
            osm_cache.fetch_cached(BOUNDS, tmp_path, fetch=boom)
        assert not osm_cache.path_for(tmp_path, BOUNDS).exists()


class TestTheRelationSupplementSurvivesTheCache:
    """v2 windows carry water relations; v1 windows predate them.

    Two traps met here, both found by a live run rather than by reasoning:

    1. The schema version was also the *key* namespace, so bumping it did not
       upgrade the warm window -- it made it unreachable, and an offline analysis
       silently lost OSM protection altogether. Path and schema are now separate.
    2. Refusing to read v1 is not the safe choice it looks like. A v1 entry has
       every way; only the relation supplement is missing. Discarding it removes
       protection that works, so it is read and reports `water_relations=False` --
       "the ways are here, the supplement was never fetched", which is the truth.
    """

    def test_the_flag_round_trips(self, tmp_path: Path) -> None:
        ctx = context()
        ctx.water_relations = True
        osm_cache.write(tmp_path, BOUNDS, ctx)
        back = osm_cache.read(tmp_path, BOUNDS)
        assert back is not None and back.water_relations is True

    def test_a_window_fetched_without_relations_says_so(self, tmp_path: Path) -> None:
        osm_cache.write(tmp_path, BOUNDS, context())  # default False
        back = osm_cache.read(tmp_path, BOUNDS)
        assert back is not None and back.water_relations is False

    def test_bumping_the_schema_does_not_move_the_key(self) -> None:
        """The bug that made a warm window unreachable instead of upgraded."""
        key = osm_cache.cache_key(BOUNDS)
        original = osm_cache.CACHE_VERSION
        try:
            osm_cache.CACHE_VERSION = original + 99
            assert osm_cache.cache_key(BOUNDS) == key
        finally:
            osm_cache.CACHE_VERSION = original

    def test_a_v1_entry_is_still_read(self, tmp_path: Path) -> None:
        """Its ways are good; only the supplement is missing."""
        osm_cache.write(tmp_path, BOUNDS, context())
        target = osm_cache.path_for(tmp_path, BOUNDS)
        payload = json.loads(target.read_text())
        payload["version"] = 1
        payload.pop("water_relations", None)
        target.write_text(json.dumps(payload))

        back = osm_cache.read(tmp_path, BOUNDS)
        assert back is not None, "discarding a v1 window loses protection that works"
        assert back.counts() == context().counts()
        assert back.water_relations is False, "and the gap must be reported, not hidden"

    def test_an_unknown_future_version_is_still_refused(self, tmp_path: Path) -> None:
        osm_cache.write(tmp_path, BOUNDS, context())
        target = osm_cache.path_for(tmp_path, BOUNDS)
        payload = json.loads(target.read_text())
        payload["version"] = 999
        target.write_text(json.dumps(payload))
        assert osm_cache.read(tmp_path, BOUNDS) is None
