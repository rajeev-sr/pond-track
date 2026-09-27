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


class TestARefusalIsRememberedBriefly:
    """Public Overpass refuses large windows for minutes at a time. Without a
    memory of that, every run over the window spent its whole 20 s enrichment
    budget asking again -- warm or not -- and added to the servers' load."""

    @staticmethod
    def refusing() -> tuple[list[int], object]:
        from app.providers.base import ProviderUnavailableError

        calls: list[int] = []

        def fetch(_bounds: object) -> OsmContext:
            calls.append(1)
            raise ProviderUnavailableError("overpass", "HTTP 504 from every mirror")

        return calls, fetch

    def test_a_second_run_does_not_ask_again(self, tmp_path: Path) -> None:
        from app.providers.base import ProviderUnavailableError

        calls, fetch = self.refusing()
        for _ in range(3):
            with pytest.raises(ProviderUnavailableError):
                osm_cache.fetch_cached(BOUNDS, tmp_path, fetch=fetch)
        assert len(calls) == 1

    def test_the_answer_says_it_is_remembered_not_fresh(self, tmp_path: Path) -> None:
        from app.providers.base import ProviderUnavailableError

        _, fetch = self.refusing()
        with pytest.raises(ProviderUnavailableError):
            osm_cache.fetch_cached(BOUNDS, tmp_path, fetch=fetch)
        with pytest.raises(ProviderUnavailableError, match="asked again after"):
            osm_cache.fetch_cached(BOUNDS, tmp_path, fetch=fetch)

    def test_it_is_asked_again_once_the_memory_expires(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.providers.base import ProviderUnavailableError

        calls, fetch = self.refusing()
        with pytest.raises(ProviderUnavailableError):
            osm_cache.fetch_cached(BOUNDS, tmp_path, fetch=fetch)
        monkeypatch.setattr(osm_cache, "FAILURE_TTL_S", -1)
        with pytest.raises(ProviderUnavailableError):
            osm_cache.fetch_cached(BOUNDS, tmp_path, fetch=fetch)
        assert len(calls) == 2

    def test_a_success_clears_the_memory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.providers.base import ProviderUnavailableError

        _, refuse = self.refusing()
        with pytest.raises(ProviderUnavailableError):
            osm_cache.fetch_cached(BOUNDS, tmp_path, fetch=refuse)
        monkeypatch.setattr(osm_cache, "FAILURE_TTL_S", -1)
        got, cached = osm_cache.fetch_cached(BOUNDS, tmp_path, fetch=lambda _b: context())
        assert not cached and got.total > 0
        assert not osm_cache._refusal_path(tmp_path, BOUNDS).exists()

    def test_other_windows_are_unaffected(self, tmp_path: Path) -> None:
        from app.providers.base import ProviderUnavailableError

        _, refuse = self.refusing()
        with pytest.raises(ProviderUnavailableError):
            osm_cache.fetch_cached(BOUNDS, tmp_path, fetch=refuse)
        elsewhere = (BOUNDS[0] + 0.1, BOUNDS[1], BOUNDS[2] + 0.1, BOUNDS[3])
        got, _ = osm_cache.fetch_cached(elsewhere, tmp_path, fetch=lambda _b: context())
        assert got.total > 0


class TestOneRequestPerWindow:
    def test_concurrent_runs_share_one_request(self, tmp_path: Path) -> None:
        import threading

        calls: list[int] = []

        def slow(_bounds: object) -> OsmContext:
            calls.append(1)
            time.sleep(0.3)
            return context()

        results: list[tuple[OsmContext, bool]] = []
        threads = [
            threading.Thread(
                target=lambda: results.append(osm_cache.fetch_cached(BOUNDS, tmp_path, fetch=slow))
            )
            for _ in range(4)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        assert len(calls) == 1
        assert len(results) == 4
        assert all(ctx.total > 0 for ctx, _ in results)
        assert sum(1 for _, cached in results if not cached) == 1

    def test_waiters_learn_of_a_failure_too(self, tmp_path: Path) -> None:
        import threading

        from app.providers.base import ProviderUnavailableError

        def slow_refusal(_bounds: object) -> OsmContext:
            time.sleep(0.3)
            raise ProviderUnavailableError("overpass", "HTTP 504")

        errors: list[Exception] = []

        def run() -> None:
            try:
                osm_cache.fetch_cached(BOUNDS, tmp_path, fetch=slow_refusal)
            except ProviderUnavailableError as exc:
                errors.append(exc)

        threads = [threading.Thread(target=run) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        assert len(errors) == 3
