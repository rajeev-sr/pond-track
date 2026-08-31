"""Village lookup: search, metadata, boundary, reverse geocode (M2-1, M2-2).

The hard requirement is HLD CH-24. A user types the name they say out loud; the
register holds what a Census enumerator wrote down. `app.core.names` folds both
to one form, and `pg_trgm` handles what folding cannot -- typos, partial input,
word order. This module puts the two together and, crucially, **reports which of
them produced each match**, because "we found your village" and "we found
something 40 % similar to what you typed" deserve different confidence from the
person about to plan a pond on the answer.

Two things this module refuses to do:

* **Guess a boundary.** `villages.boundary_level` records whether a polygon is
  the village's own or the containing sub-district's, and every response carries
  it. A 662 km² tehsil outline returned as a village boundary would silently
  corrupt every area figure computed from it.
* **Return one result when several match.** Three villages named Khapri sit in
  one district. Picking the first would be wrong far more often than it looks;
  the caller gets all three, each with its sub-district, and chooses.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.names import normalise_query, strip_diacritics

#: pg_trgm similarity floor. Below this the matches are noise -- at 0.2 a
#: three-letter query matches half the state. 0.3 is postgres's own default for
#: the `%` operator and it holds up on Indian place names: `kutelabhata` scores
#: 1.00 against the register's spelling, while unrelated names in the same
#: district land at 0.31 and below.
MIN_SIMILARITY = 0.3

#: Trigram similarity is symmetric and short strings score badly against long
#: ones, so a query that is a clean prefix of a name gets a separate route in.
#: Typing `kutel` should find `kutelabhatha` even though they share few trigrams.
MIN_PREFIX_CHARS = 3

DEFAULT_LIMIT = 10
MAX_LIMIT = 50

#: Esri World Imagery, the same basemap the UI uses. No credentials, and the
#: attribution is a condition of use rather than a courtesy.
IMAGERY = {
    "provider": "Esri World Imagery",
    "tile_url_template": (
        "https://server.arcgisonline.com/ArcGIS/rest/services/"
        "World_Imagery/MapServer/tile/{z}/{y}/{x}"
    ),
    "tile_size": 256,
    "min_zoom": 0,
    "max_zoom": 19,
    "attribution": "Imagery © Esri, Maxar, Earthstar Geographics",
    "scheme": "xyz",
    "note": (
        "Tile rows are {y} before columns {x} in this service, unlike the more "
        "common {x}/{y} ordering -- transposing them returns the wrong part of "
        "the world rather than an error."
    ),
}


@dataclass(frozen=True, slots=True)
class VillageMatch:
    """One search hit, with enough context to tell it from its namesakes."""

    id: uuid.UUID
    name: str
    state: str | None
    district: str | None
    subdistrict: str | None
    shrid: str | None
    lgd_code: str | None
    census_2011_id: str | None
    is_town: bool
    #: 0..1 trigram similarity between the folded query and the folded name.
    similarity: float
    #: Which rule produced the hit: exact | folded | prefix | trigram.
    matched_by: str
    #: What geometry this village can offer: village | subdistrict | None.
    boundary_level: str | None
    #: A point to centre a map on, and what it is actually the centre of.
    focus_lon: float | None
    focus_lat: float | None
    focus_level: str | None
    #: The Gram Panchayat(s) this village belongs to: [(name, lgd_code), …].
    #:
    #: Usually one, occasionally several -- 12,045 Indian villages sit in two or
    #: more. It matters here beyond provenance: where two villages share a name
    #: *and* a hierarchy, the Panchayat is what tells them apart. Durg's two
    #: Khapris are in `Khapri K` and `Khapri`.
    panchayats: tuple[tuple[str, str], ...] = ()

    @property
    def display_hierarchy(self) -> str:
        """`Khapri, Durg, Durg, Chhattisgarh` -- what disambiguates namesakes."""
        parts = [self.name, self.subdistrict, self.district, self.state]
        seen: list[str] = []
        for part in parts:
            # Durg the village sits in Durg the tehsil in Durg the district;
            # printing it three times helps nobody.
            if part and (not seen or part != seen[-1]):
                seen.append(part)
        return ", ".join(p.title() for p in seen)


_SEARCH_SQL = text(
    """
    WITH q AS (SELECT CAST(:folded AS text) AS folded)
    SELECT v.id,
           v.name,
           v.state,
           v.district,
           v.subdistrict,
           v.shrid,
           v.lgd_code,
           v.census_2011_id,
           v.boundary_level,
           v.source,
           similarity(v.name_normalised, q.folded)          AS sim,
           v.name_normalised = q.folded                     AS is_exact,
           v.name_normalised LIKE q.folded || '%'           AS is_prefix,
           ST_X(COALESCE(v.centroid, a.centroid))           AS focus_lon,
           ST_Y(COALESCE(v.centroid, a.centroid))           AS focus_lat,
           CASE WHEN v.centroid IS NOT NULL THEN 'village'
                WHEN a.centroid IS NOT NULL THEN a.level
                ELSE NULL END                               AS focus_level,
           -- Aggregated in the same query rather than fetched per result: a
           -- lookup per row would turn one search into eleven round-trips.
           COALESCE(gp.names, '{}')                         AS gp_names,
           COALESCE(gp.codes, '{}')                         AS gp_codes
      FROM villages v
      CROSS JOIN q
      LEFT JOIN admin_areas a ON a.id = v.admin_area_id
      LEFT JOIN (
            SELECT vg.village_id,
                   array_agg(g.name ORDER BY g.name)     AS names,
                   array_agg(g.lgd_code ORDER BY g.name) AS codes
              FROM village_gram_panchayats vg
              JOIN gram_panchayats g ON g.id = vg.gram_panchayat_id
             GROUP BY vg.village_id
      ) gp ON gp.village_id = v.id
     -- The casts are required, not cosmetic. A NULL bound to a bare parameter
     -- that appears only in an "is it null, or does it equal the column" test
     -- gives postgres nothing to infer a type from, and it rejects the whole
     -- statement. (Writing that test out with a colon-prefixed name here would
     -- also break: SQLAlchemy's text() scans comments for bind parameters too,
     -- so a placeholder mentioned in prose becomes one it demands a value for.)
     WHERE (CAST(:state AS text)    IS NULL OR v.state    = CAST(:state AS text))
       AND (CAST(:district AS text) IS NULL OR v.district = CAST(:district AS text))
       AND (
             v.name_normalised = q.folded
             -- The `%` operator is what the GIN trigram index answers, so it
             -- carries the bulk of the work; the explicit similarity below is
             -- only for ranking what it returned.
             OR v.name_normalised % q.folded
             OR (length(q.folded) >= :min_prefix
                 AND v.name_normalised LIKE q.folded || '%')
           )
     ORDER BY is_exact DESC,
              is_prefix DESC,
              sim DESC,
              -- A stable tiebreak, so equal-scoring namesakes come back in the
              -- same order every time rather than however the scan happened to
              -- reach them. Paging a search whose order shifts is worse than
              -- no paging.
              v.state NULLS LAST,
              v.district NULLS LAST,
              v.subdistrict NULLS LAST,
              v.shrid
     LIMIT :limit
    """
)


def resolve_filters(
    session: Session, *, state: str | None = None, district: str | None = None
) -> dict[str, str | None]:
    """Resolve caller-supplied filters to the spellings the register holds.

    Public because the API echoes the resolved filters back: a caller who asked
    for `State=CHHATTISGARH` should be able to see that it was understood as
    `chhattisgarh`, and a caller whose filter was not recognised should see it
    unchanged next to a count of zero.
    """
    return {
        "state": _fold_filter(session, state, column="state"),
        "district": _fold_filter(session, district, column="district"),
    }


def seeded_states(session: Session) -> list[str]:
    """Which states have actually been loaded, capitalised for display.

    A search that finds nothing is far more often a state that was never seeded
    than a village that does not exist, so the API says which ones are present.
    """
    rows = session.execute(
        text("SELECT DISTINCT state FROM villages WHERE state IS NOT NULL ORDER BY state")
    ).scalars()
    return [str(r).title() for r in rows]


def search(
    session: Session,
    query: str,
    *,
    state: str | None = None,
    district: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[VillageMatch]:
    """Find villages whose name matches `query`, best first.

    `query` may be Latin or Devanagari, and may be spelled however the user
    spells it. State and district filters are folded the same way, so
    `state=Chhattisgarh` matches the register's `chhattisgarh`.

    Returns an empty list rather than raising: no match is an ordinary answer to
    a search, not an error.
    """
    folded = normalise_query(query)
    if not folded:
        return []

    limit = max(1, min(int(limit), MAX_LIMIT))
    rows = session.execute(
        _SEARCH_SQL,
        {
            "folded": folded,
            "state": _fold_filter(session, state, column="state"),
            "district": _fold_filter(session, district, column="district"),
            "min_prefix": MIN_PREFIX_CHARS,
            "limit": limit,
        },
    ).mappings()

    return [_to_match(row, raw=query) for row in rows]


#: The only columns a caller may narrow a search by. Whitelisted rather than
#: interpolated, so no caller-supplied string ever reaches the SQL text.
_FILTERABLE = {
    "state": text("SELECT DISTINCT state AS value FROM villages WHERE state IS NOT NULL"),
    "district": text("SELECT DISTINCT district AS value FROM villages WHERE district IS NOT NULL"),
}


def _fold_filter(session: Session, value: str | None, *, column: str) -> str | None:
    """Resolve a state or district filter to the spelling the rows actually hold.

    The filter compares against the raw column, but the caller may capitalise or
    spell it differently from the register -- `Chhattisgarh` against the stored
    `chhattisgarh`. So the folded input is matched against the folded distinct
    values and the stored spelling is returned.

    An unrecognised filter passes through unchanged. That correctly yields no
    results: a caller who asked to be narrowed to a district that does not exist
    should get nothing, not everything.

    There are 36 states and 735 districts, so scanning the distinct values costs
    nothing and keeps the fold in the one place it lives.
    """
    if not value:
        return None
    folded = normalise_query(value)
    if not folded:
        return None
    statement = _FILTERABLE.get(column)
    if statement is None:
        raise ValueError(f"cannot filter on {column!r}")

    for candidate in session.execute(statement).scalars():
        if normalise_query(candidate) == folded:
            return str(candidate)
    return value


def _to_match(row: Any, *, raw: str) -> VillageMatch:
    """Shape a row, recording *how* it matched.

    The distinction is worth reporting. `exact` means the caller typed the name
    as the register holds it; `folded` means the two agreed only after
    transliteration folding, which is a slightly weaker claim and the one that
    covers `kutelabhata` finding `kutelabhatha`. Comparing the stored name to
    the *folded* query -- as an earlier version did -- reported `exact` only for
    names that happen to be their own canonical form, which is an accident of
    spelling rather than anything the caller did.
    """
    matched_by: str
    if row["is_exact"]:
        matched_by = "exact" if raw.strip().lower() == str(row["name"]).lower() else "folded"
    elif row["is_prefix"]:
        matched_by = "prefix"
    else:
        matched_by = "trigram"
    return VillageMatch(
        id=row["id"],
        name=row["name"],
        state=row["state"],
        district=row["district"],
        subdistrict=row["subdistrict"],
        shrid=row["shrid"],
        lgd_code=row["lgd_code"],
        census_2011_id=row["census_2011_id"],
        is_town=False,
        similarity=round(float(row["sim"] or 0.0), 4),
        matched_by=matched_by,
        boundary_level=row["boundary_level"],
        focus_lon=_as_float(row["focus_lon"]),
        focus_lat=_as_float(row["focus_lat"]),
        focus_level=row["focus_level"],
        panchayats=tuple(zip(row["gp_names"] or (), row["gp_codes"] or (), strict=True)),
    )


def _as_float(value: Any) -> float | None:
    return None if value is None else round(float(value), 6)


# ---------------------------------------------------------------------------
# Single-village lookups
# ---------------------------------------------------------------------------

_DETAIL_SQL = text(
    """
    SELECT v.id, v.name, v.state, v.district, v.subdistrict, v.block,
           v.shrid, v.lgd_code, v.census_2011_id, v.census_2001_id, v.source,
           v.boundary_level, v.area_ha AS village_area_ha,
           a.id       AS area_id,
           a.name     AS area_name,
           a.level    AS area_level,
           a.area_ha  AS area_ha,
           a.source   AS area_source,
           ST_X(COALESCE(v.centroid, a.centroid)) AS focus_lon,
           ST_Y(COALESCE(v.centroid, a.centroid)) AS focus_lat,
           CASE WHEN v.centroid IS NOT NULL THEN 'village'
                WHEN a.centroid IS NOT NULL THEN a.level
                ELSE NULL END AS focus_level
      FROM villages v
      LEFT JOIN admin_areas a ON a.id = v.admin_area_id
     WHERE v.id = :village_id
    """
)


def get(session: Session, village_id: uuid.UUID) -> dict[str, Any] | None:
    """Metadata for one village, or None if the id is unknown."""
    row = session.execute(_DETAIL_SQL, {"village_id": str(village_id)}).mappings().first()
    if row is None:
        return None

    return {
        "id": str(row["id"]),
        "name": row["name"].title(),
        "name_as_recorded": row["name"],
        "identifiers": {
            # HLD CH-24: the code is the canonical key, never the name. LGD is
            # what officials work in; the SHRID composes the Census-2011 codes.
            "lgd_code": row["lgd_code"],
            "census_2011_id": row["census_2011_id"],
            "census_2001_id": row["census_2001_id"],
            "shrid": row["shrid"],
        },
        "hierarchy": {
            "state": _title(row["state"]),
            "district": _title(row["district"]),
            "subdistrict": _title(row["subdistrict"]),
            "block": _title(row["block"]),
        },
        "focus": _focus(row),
        "boundary": _boundary_summary(row),
        "gram_panchayats": _panchayats_for(session, village_id),
        "source": row["source"],
    }


def get_boundary(session: Session, village_id: uuid.UUID) -> dict[str, Any] | None:
    """The best available boundary for a village, labelled with what it is.

    Returns the village's own polygon when one has been seeded, otherwise the
    containing sub-district's, and always says which. A caller that computes an
    area from this must be able to tell the difference: the Durg tehsil outline
    is 662 km² against a village of a few hundred hectares.
    """
    row = session.execute(_DETAIL_SQL, {"village_id": str(village_id)}).mappings().first()
    if row is None:
        return None

    level = row["boundary_level"]
    geometry: str | None
    represents: str | None
    source: str | None
    area_name: str | None
    area_ha: float | None
    if level == "village":
        geometry = session.execute(
            text("SELECT ST_AsGeoJSON(geom) FROM villages WHERE id = :id"),
            {"id": str(village_id)},
        ).scalar_one_or_none()
        represents, source = "village", row["source"]
        area_ha = row["village_area_ha"]
        area_name = row["name"]
    elif row["area_id"] is not None:
        geometry = session.execute(
            text("SELECT ST_AsGeoJSON(geom) FROM admin_areas WHERE id = :id"),
            {"id": str(row["area_id"])},
        ).scalar_one_or_none()
        represents, source = row["area_level"], row["area_source"]
        area_ha = row["area_ha"]
        area_name = row["area_name"]
    else:
        geometry = None
        represents = source = area_name = None
        area_ha = None

    if geometry is None:
        return {
            "village_id": str(village_id),
            "available": False,
            "reason": (
                "no boundary has been seeded for this village, and it could not be "
                "matched to a containing sub-district -- its name is ambiguous "
                "within its state. It remains searchable; only the outline is missing."
            ),
            "geometry": None,
            "represents": None,
        }

    return {
        "village_id": str(village_id),
        "available": True,
        "geometry": json.loads(geometry),
        # The whole point of this field: never let a sub-district outline be
        # taken for a village boundary.
        "represents": represents,
        "is_village_boundary": represents == "village",
        "of": _title(area_name),
        "area_ha": None if area_ha is None else round(float(area_ha), 2),
        "source": source,
        "caveat": (
            None
            if represents == "village"
            else (
                f"This is the {represents} outline containing the village, not the "
                "village boundary -- no open source publishes village polygons for "
                "this state. Do not compute a village area from it."
            )
        ),
    }


def resolve_point(session: Session, lon: float, lat: float) -> dict[str, Any] | None:
    """Reverse-geocode a coordinate to the smallest administrative area holding it.

    Village polygons would answer this directly; without them the answer is the
    containing sub-district, together with the villages recorded inside it. That
    is a genuinely weaker answer than a point-in-village test and is reported as
    such rather than dressed up as one.
    """
    area = (
        session.execute(
            text(
                """
            SELECT a.id, a.name, a.level, a.area_ha,
                   d.name AS district, s.name AS state
              FROM admin_areas a
              LEFT JOIN admin_areas d ON d.id = a.parent_id
              LEFT JOIN admin_areas s ON s.id = d.parent_id
             WHERE a.level = 'subdistrict'
               AND ST_Contains(a.geom, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326))
             ORDER BY a.area_ha
             LIMIT 1
            """
            ),
            {"lon": lon, "lat": lat},
        )
        .mappings()
        .first()
    )
    if area is None:
        return None

    village_count = session.execute(
        text("SELECT count(*) FROM villages WHERE admin_area_id = :area_id"),
        {"area_id": str(area["id"])},
    ).scalar_one()

    return {
        "point": {"lon": lon, "lat": lat},
        "matched": {
            "level": area["level"],
            "name": _title(area["name"]),
            "district": _title(area["district"]),
            "state": _title(area["state"]),
            "area_ha": None if area["area_ha"] is None else round(float(area["area_ha"]), 2),
        },
        "villages_recorded_here": int(village_count),
        "precision": (
            "sub-district. Village polygons are not seeded, so this cannot "
            "narrow to a single village -- use /villages/search with the name "
            "if you know it."
        ),
    }


def imagery_for(session: Session, village_id: uuid.UUID) -> dict[str, Any] | None:
    """Satellite tile template plus the framing needed to use it (FR-1)."""
    row = session.execute(_DETAIL_SQL, {"village_id": str(village_id)}).mappings().first()
    if row is None:
        return None

    bounds = None
    if row["area_id"] is not None:
        extent = session.execute(
            text(
                """
                SELECT ST_XMin(g), ST_YMin(g), ST_XMax(g), ST_YMax(g)
                  FROM (SELECT ST_Envelope(geom) AS g FROM admin_areas WHERE id = :id) t
                """
            ),
            {"id": str(row["area_id"])},
        ).first()
        if extent:
            bounds = [round(float(v), 6) for v in extent]

    return {
        "village_id": str(village_id),
        "imagery": dict(IMAGERY),
        "focus": _focus(row),
        "bounds_4326": bounds,
        "bounds_of": None if bounds is None else row["area_level"],
    }


# ---------------------------------------------------------------------------
# Shared shaping
# ---------------------------------------------------------------------------


def _panchayats_for(session: Session, village_id: uuid.UUID) -> list[dict[str, str]]:
    """The Panchayat(s) a village belongs to.

    The only LGD code the open data provides anywhere, and the body that
    actually plans and builds MGNREGA water works -- so it is the unit a pond
    proposal is addressed to, even though it is not the village key HLD E2 asks
    for.
    """
    rows = session.execute(
        text(
            """
            SELECT g.name, g.lgd_code
              FROM village_gram_panchayats vg
              JOIN gram_panchayats g ON g.id = vg.gram_panchayat_id
             WHERE vg.village_id = :village_id
             ORDER BY g.name
            """
        ),
        {"village_id": str(village_id)},
    ).all()
    return [{"name": name, "lgd_code": code} for name, code in rows]


def _title(value: str | None) -> str | None:
    """Present a place name consistently, whichever source it came from.

    The register writes `chhattisgarh` and geoBoundaries writes `Chhattīsgarh`,
    so `/villages/{id}` and `/villages/resolve` were returning two spellings of
    one state and a caller comparing them would see a mismatch that means
    nothing. Diacritics are stripped rather than a name being rewritten: both
    are already Latin transliterations of the same Devanagari, and the plain form
    is the one the rest of the API uses.
    """
    if value is None:
        return None
    return strip_diacritics(str(value)).title()


def _focus(row: Any) -> dict[str, Any] | None:
    """A point to centre a map on, saying what it is the centre of."""
    if row["focus_lon"] is None or row["focus_lat"] is None:
        return None
    return {
        "lon": round(float(row["focus_lon"]), 6),
        "lat": round(float(row["focus_lat"]), 6),
        # A sub-district centroid is not the village. Framing a map on it is
        # useful; treating it as the village's location is not.
        "is_centre_of": row["focus_level"],
        "approximate": row["focus_level"] != "village",
    }


def _boundary_summary(row: Any) -> dict[str, Any]:
    return {
        "level": row["boundary_level"],
        "is_village_boundary": row["boundary_level"] == "village",
        "of": _title(row["area_name"]),
        "area_ha": None if row["area_ha"] is None else round(float(row["area_ha"]), 2),
    }
