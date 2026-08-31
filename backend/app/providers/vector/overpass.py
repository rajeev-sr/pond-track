"""OpenStreetMap features for the analysis window, via the Overpass API (M5-1).

A contour sheet says nothing about what already occupies the ground. Before a
pond can be sited, the obvious blockers have to be read from somewhere: houses,
roads, existing tanks and canals, and land already committed to something else.
OSM is the only keyless source with that detail for rural India, and in
Chhattisgarh village mapping is good enough to be worth using -- with the caveat
that absence of a building in OSM is not evidence of absent buildings, which is
why the mask that consumes this treats OSM as *additive* exclusion on top of the
land-cover mask rather than as the authority on what is free.

This module only fetches and classifies. Buffer widths and the decision about
what blocks a pond live in `services/land.py`, so the policy can be argued with
without touching the transport.

Licence: OpenStreetMap contributors, ODbL 1.0 -- attribution is required and is
carried in `PROVENANCE`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from app.core.logging import get_logger
from app.providers.base import Provenance, ProviderUnavailableError, post_json

log = get_logger("providers.overpass")

#: Public Overpass instances, tried in order. The main endpoint rate-limits
#: aggressively and answers 429/504 under load, so a mirror list is the
#: difference between "usually works" and "works". Each is tried once: retrying
#: a rate-limited endpoint is what earns a longer ban.
#: Public Overpass mirrors, tried in order. More than two on purpose: a live run
#: had the first answer `ConnectError` and the second HTTP 502 within the same
#: minute, which left the analysis with no OSM at all -- and no OSM means no
#: river veto, the one exclusion this project cannot afford to lose.
#:
#: Every mirror here serves the whole planet. That is a correctness requirement,
#: not a preference: a region-limited mirror answers an Indian bbox with HTTP 200
#: and zero elements, which reads as "nothing is here" and fails *open*.
#: `overpass.osm.ch` was tried and does exactly that, so it is deliberately
#: absent -- as is any other national instance.
ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
)

#: Overpass asks every client to identify itself, and rate-limits anonymous
#: ones harder. Naming the project is both polite and practical.
USER_AGENT = "contour-village-pond-planner/1.0 (student project; contact via repo)"

#: Overpass counts this against the server's own budget, so it is part of the
#: query rather than only an HTTP timeout.
QUERY_TIMEOUT_S = 60
HTTP_TIMEOUT_S = 90.0

#: A village survey sheet is a few km across. A careless caller asking for a
#: whole district would be served a refusal by Overpass anyway, but failing here
#: is faster and says why.
MAX_SPAN_DEG = 0.5

FeatureKind = Literal["building", "road", "track", "water", "landuse"]

#: `highway` values that are a genuine obstruction, as against a field track.
#: Buffering a footpath by the same 20 m as a state highway would exclude most of
#: a village's farmland, so the two are separated here and buffered differently
#: downstream.
MAJOR_HIGHWAYS = frozenset(
    {
        "motorway",
        "trunk",
        "primary",
        "secondary",
        "tertiary",
        "unclassified",
        "residential",
        "living_street",
        "motorway_link",
        "trunk_link",
        "primary_link",
        "secondary_link",
        "tertiary_link",
    }
)

#: Tracks, paths and the like: present, but not a reason to reject a site.
MINOR_HIGHWAYS = frozenset(
    {"track", "path", "footway", "cycleway", "bridleway", "steps", "service"}
)

#: `waterway` values that carry water and so must be kept clear. `ditch` and
#: `drain` are included because a pond dug across a field drain destroys it.
WATERWAYS = frozenset({"river", "stream", "canal", "drain", "ditch", "riverbank"})

#: `landuse`/`natural` values that rule the ground out regardless of terrain.
BLOCKING_LANDUSE = frozenset(
    {
        "residential",
        "industrial",
        "commercial",
        "retail",
        "quarry",
        "cemetery",
        "railway",
        "military",
        "landfill",
        "construction",
    }
)

PROVENANCE = Provenance(
    provider="OpenStreetMap",
    dataset="OSM features via Overpass API",
    resolution="vector, community-surveyed",
    licence="ODbL 1.0 (attribution required)",
)


@dataclass(frozen=True)
class OsmFeature:
    """One OSM way or relation, reduced to what the mask step needs.

    `rings` holds one or more coordinate sequences in (lon, lat) order -- a
    closed ring for an area, an open line for a road or stream. A relation
    contributes each of its outer members as a separate ring: assembling a
    correct multipolygon is not worth it when the consumer only buffers and
    rasterises the result.
    """

    kind: FeatureKind
    osm_type: str
    osm_id: int
    tags: dict[str, str]
    rings: tuple[tuple[tuple[float, float], ...], ...]

    @property
    def is_area(self) -> bool:
        """True when the first ring closes, which is how OSM marks an area."""
        first = self.rings[0] if self.rings else ()
        return len(first) >= 4 and first[0] == first[-1]


@dataclass
class OsmContext:
    """Everything read from OSM for one analysis window."""

    buildings: list[OsmFeature] = field(default_factory=list)
    roads: list[OsmFeature] = field(default_factory=list)
    tracks: list[OsmFeature] = field(default_factory=list)
    water: list[OsmFeature] = field(default_factory=list)
    landuse: list[OsmFeature] = field(default_factory=list)
    #: Which mirror answered, for the provenance block.
    endpoint: str = ""
    #: Whether the water multipolygon-relation supplement was obtained. False
    #: means it was not fetched, which is not the same as "there are none":
    #: a river mapped as a relation would be absent rather than mis-buffered.
    water_relations: bool = False
    provenance: Provenance = PROVENANCE

    def __iter__(self) -> Any:
        yield from self.buildings
        yield from self.roads
        yield from self.tracks
        yield from self.water
        yield from self.landuse

    @property
    def total(self) -> int:
        return (
            len(self.buildings)
            + len(self.roads)
            + len(self.tracks)
            + len(self.water)
            + len(self.landuse)
        )

    def counts(self) -> dict[str, int]:
        return {
            "buildings": len(self.buildings),
            "roads": len(self.roads),
            "tracks": len(self.tracks),
            "water": len(self.water),
            "blocking_landuse": len(self.landuse),
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "counts": self.counts(),
            "endpoint": self.endpoint,
            "water_relations_fetched": self.water_relations,
            "source": self.provenance.as_dict(),
            "caveat": (
                "OSM completeness varies by village. A building absent from OSM "
                "is not evidence of open ground, so these features only add "
                "exclusions to the land-cover mask -- they never clear it."
            ),
        }


def build_query(bounds: tuple[float, float, float, float]) -> str:
    """The Overpass QL for one window.

    `bounds` is (min_lon, min_lat, max_lon, max_lat), matching every other
    provider here. Overpass wants (south, west, north, east), so the order is
    swapped exactly once -- here -- rather than at each call site.
    """
    min_lon, min_lat, max_lon, max_lat = bounds
    if not (min_lon < max_lon and min_lat < max_lat):
        raise ValueError(f"degenerate bounds: {bounds}")
    span = max(max_lon - min_lon, max_lat - min_lat)
    if span > MAX_SPAN_DEG:
        raise ValueError(f"window spans {span:.2f} deg; the limit is {MAX_SPAN_DEG} deg")

    bbox = f"{min_lat},{min_lon},{max_lat},{max_lon}"
    # `[bbox:]` set once globally rather than repeated on every clause, and ways
    # only. Both are cost decisions, measured rather than guessed: the
    # bbox-per-clause form with `relation[...]` clauses added answered a 3 x
    # 2.6 km window with HTTP 504 (timed out in queue), while this form returns
    # the same window in about two seconds. The cost is water multipolygons
    # mapped as relations; village tanks are almost always single closed ways,
    # and `parse` still handles relations if a caller supplies them.
    return (
        f"[out:json][timeout:{QUERY_TIMEOUT_S}][bbox:{bbox}];\n"
        "(\n"
        '  way["building"];\n'
        '  way["highway"];\n'
        '  way["natural"="water"];\n'
        '  way["waterway"];\n'
        '  way["landuse"];\n'
        ");\n"
        "out geom;\n"
    )


def build_water_relation_query(bounds: tuple[float, float, float, float]) -> str:
    """Water multipolygon *relations* only, as a second and optional request.

    `build_query` asks for ways alone because adding `relation[...]` clauses
    across every feature kind made a 3 x 2.6 km window time out in the Overpass
    queue (HTTP 504). But a large river's areal extent is very often a
    multipolygon relation, and missing it does not merely mis-buffer the river --
    it loses the river entirely, which is the worst direction for an exclusion
    layer to fail.

    So the relations are fetched separately and the caller treats failure as
    non-fatal. Two clauses over one bbox is a small fraction of the original
    query's cost, and if it times out anyway the analysis still has every way,
    exactly as before -- adding coverage must never introduce a new way to lose
    it.
    """
    min_lon, min_lat, max_lon, max_lat = bounds
    if not (min_lon < max_lon and min_lat < max_lat):
        raise ValueError(f"degenerate bounds: {bounds}")
    span = max(max_lon - min_lon, max_lat - min_lat)
    if span > MAX_SPAN_DEG:
        raise ValueError(f"window spans {span:.2f} deg; the limit is {MAX_SPAN_DEG} deg")
    bbox = f"{min_lat},{min_lon},{max_lat},{max_lon}"
    return (
        f"[out:json][timeout:{QUERY_TIMEOUT_S}][bbox:{bbox}];\n"
        "(\n"
        '  relation["natural"="water"];\n'
        '  relation["waterway"];\n'
        ");\n"
        "out geom;\n"
    )


def _rings_of(element: dict[str, Any]) -> tuple[tuple[tuple[float, float], ...], ...]:
    """Pull (lon, lat) sequences out of an `out geom` element."""
    if "geometry" in element:
        ring = tuple(
            (float(p["lon"]), float(p["lat"]))
            for p in element["geometry"]
            # A way clipped by the bbox has null placeholders where its nodes
            # fall outside; dropping them leaves a valid open line.
            if p is not None and p.get("lat") is not None and p.get("lon") is not None
        )
        return (ring,) if len(ring) >= 2 else ()

    rings: list[tuple[tuple[float, float], ...]] = []
    for member in element.get("members", []):
        if member.get("role") not in (None, "", "outer"):
            continue
        geom = member.get("geometry") or []
        ring = tuple(
            (float(p["lon"]), float(p["lat"]))
            for p in geom
            if p is not None and p.get("lat") is not None and p.get("lon") is not None
        )
        if len(ring) >= 2:
            rings.append(ring)
    return tuple(rings)


def classify(tags: dict[str, str]) -> FeatureKind | None:
    """Which exclusion class a feature belongs to, or None to ignore it.

    Order matters: a building inside a residential landuse polygon is a
    building, and a reservoir tagged `landuse=reservoir` is water rather than
    committed land.
    """
    if tags.get("building"):
        return "building"
    if tags.get("natural") == "water" or tags.get("landuse") in ("reservoir", "basin"):
        return "water"
    if tags.get("waterway") in WATERWAYS:
        return "water"
    highway = tags.get("highway")
    if highway in MAJOR_HIGHWAYS:
        return "road"
    if highway in MINOR_HIGHWAYS:
        return "track"
    if tags.get("landuse") in BLOCKING_LANDUSE:
        return "landuse"
    return None


def parse(payload: dict[str, Any]) -> OsmContext:
    """Turn an Overpass JSON response into a classified context."""
    context = OsmContext()
    bucket = {
        "building": context.buildings,
        "road": context.roads,
        "track": context.tracks,
        "water": context.water,
        "landuse": context.landuse,
    }
    for element in payload.get("elements", []):
        tags = {str(k): str(v) for k, v in (element.get("tags") or {}).items()}
        kind = classify(tags)
        if kind is None:
            continue
        rings = _rings_of(element)
        if not rings:
            continue
        bucket[kind].append(
            OsmFeature(
                kind=kind,
                osm_type=str(element.get("type", "way")),
                osm_id=int(element.get("id", 0)),
                tags=tags,
                rings=rings,
            )
        )
    return context


def fetch_osm_context(
    bounds: tuple[float, float, float, float],
    *,
    endpoints: tuple[str, ...] = ENDPOINTS,
    timeout: float = HTTP_TIMEOUT_S,
) -> OsmContext:
    """Read buildings, roads, water and committed land-use over `bounds`.

    Each mirror is tried once, in order. Raises `ProviderUnavailableError` only
    when every one of them fails, so a single rate-limited endpoint does not
    lose the layer.
    """
    query = build_query(bounds)
    failures: list[str] = []
    empty: OsmContext | None = None
    for endpoint in endpoints:
        try:
            payload = post_json(
                "overpass",
                endpoint,
                timeout=timeout,
                form={"data": query},
                headers={"User-Agent": USER_AGENT},
            )
        except ProviderUnavailableError as exc:
            log.warning("overpass mirror failed", endpoint=endpoint, detail=exc.detail)
            failures.append(f"{endpoint}: {exc.detail}")
            continue
        if not isinstance(payload, dict):
            failures.append(f"{endpoint}: response was not a JSON object")
            continue
        remark = str(payload.get("remark") or "")
        if remark and not payload.get("elements"):
            # Overpass reports timeouts and query errors this way, at HTTP 200.
            # Treating it as an empty area would silently drop every exclusion.
            log.warning("overpass remark", endpoint=endpoint, remark=remark[:200])
            failures.append(f"{endpoint}: {remark[:160]}")
            continue
        context = parse(payload)
        context.endpoint = endpoint

        if context.total == 0:
            # An empty window is accepted only once every mirror agrees on it.
            # A single mirror's "zero features" is not evidence of open ground:
            # a region-limited instance answers an out-of-area bbox with HTTP
            # 200 and no elements, and read as authoritative that silently
            # empties the whole exclusion layer. Genuinely empty windows exist,
            # so this is a corroboration rule, not a rejection.
            log.warning("overpass returned an empty window", endpoint=endpoint)
            failures.append(f"{endpoint}: zero features (unconfirmed)")
            if empty is None:
                empty = context
            continue

        _add_water_relations(context, bounds, endpoint=endpoint, timeout=timeout)
        log.info(
            "overpass answered",
            endpoint=endpoint,
            water_relations=context.water_relations,
            **context.counts(),
        )
        return context

    if empty is not None:
        # Every mirror that answered said the same thing, so believe it -- but
        # ask for water relations even so. A window whose only feature is a
        # multipolygon lake or river has no *ways* at all, so it looks empty to
        # the query above while still being the last place a pond should go.
        _add_water_relations(empty, bounds, endpoint=empty.endpoint, timeout=timeout)
        log.info(
            "overpass: window is genuinely empty",
            mirrors_agreeing=len(endpoints),
            water_relations=empty.water_relations,
            water=len(empty.water),
        )
        return empty

    raise ProviderUnavailableError("overpass", "; ".join(failures) or "no endpoint configured")


def _add_water_relations(
    context: OsmContext,
    bounds: tuple[float, float, float, float],
    *,
    endpoint: str,
    timeout: float,
) -> None:
    """Append water multipolygon relations to `context`, in place.

    Best effort by design: any failure is logged and left, because the ways are
    already in hand and losing them to a relation timeout would be a worse
    outcome than the gap this closes. `context.water_relations` records whether
    the supplement actually landed, so a reader can tell "no relations here" from
    "relations not fetched".
    """
    try:
        payload = post_json(
            "overpass",
            endpoint,
            timeout=timeout,
            form={"data": build_water_relation_query(bounds)},
            headers={"User-Agent": USER_AGENT},
        )
    except (ProviderUnavailableError, ValueError) as exc:
        log.warning("overpass water relations unavailable", endpoint=endpoint, detail=str(exc))
        return
    if not isinstance(payload, dict):
        return
    if str(payload.get("remark") or "") and not payload.get("elements"):
        log.warning("overpass water relations remark", endpoint=endpoint)
        return

    seen = {(f.osm_type, f.osm_id) for f in context.water}
    added = 0
    for feature in parse(payload).water:
        if (feature.osm_type, feature.osm_id) in seen:
            continue
        context.water.append(feature)
        added += 1
    context.water_relations = True
    if added:
        log.info("overpass water relations added", endpoint=endpoint, added=added)
