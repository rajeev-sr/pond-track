"""Village endpoints (M2-1, M2-2).

    GET /api/v1/villages/search?q=&state=&district=&limit=
    GET /api/v1/villages/{village_id}
    GET /api/v1/villages/{village_id}/boundary
    GET /api/v1/villages/{village_id}/imagery
    GET /api/v1/villages/resolve?lon=&lat=

Thin, as HLD 2.1 requires: validate, delegate to `services.villages`, translate
absence into an RFC 7807 problem. The interesting behaviour -- folding the query
so a Devanagari or differently-spelled name finds the register's spelling, and
labelling every polygon with what it actually outlines -- lives in the service.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.errors import NotFoundProblem, UnanswerableProblem, ValidationProblem
from app.core.logging import get_logger
from app.core.names import normalise_query
from app.db.session import get_db
from app.schemas.village import (
    VillageBoundaryResponse,
    VillageDetailResponse,
    VillageImageryResponse,
    VillageSearchResponse,
)
from app.services import villages as service

router = APIRouter(prefix="/villages", tags=["villages"])
log = get_logger(__name__)

MIN_QUERY_CHARS = 2


@router.get(
    "/search",
    response_model=VillageSearchResponse,
    summary="Search villages by name, in Latin or Devanagari",
)
def search_villages(
    session: Annotated[Session, Depends(get_db)],
    q: Annotated[
        str,
        Query(
            min_length=1,
            max_length=200,
            description=(
                "Village name. Spelling need not match the register: "
                "`kutelabhata`, `Kutelabhaata` and `कुटेलाभाठा` all find "
                "`kutelabhatha`."
            ),
        ),
    ],
    state: Annotated[str | None, Query(max_length=200)] = None,
    district: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=service.MAX_LIMIT)] = service.DEFAULT_LIMIT,
) -> VillageSearchResponse:
    """Rank villages by how well their name matches `q`.

    Both the query and the stored name pass through one transliteration fold, so
    the caller's spelling does not have to match the Census enumerator's.
    Namesakes are all returned with their hierarchy rather than one being picked:
    three villages called Khapri sit in a single district.
    """
    folded = normalise_query(q)
    if not folded:
        # Two different causes, and telling them apart matters: `!!!` has
        # nothing to search on, while `a` is a real letter that the fold's
        # trailing-vowel rule removes entirely. Reporting the second as "no
        # letters" would tell the user something they can see is false.
        if any(ch.isalnum() for ch in q):
            raise ValidationProblem(
                detail=(
                    f"{q!r} reduces to nothing once transliteration differences "
                    "are folded away, so there is no name left to search for. "
                    "Enter more of the village name."
                ),
                errors=[{"field": "q", "message": "query folds to an empty string"}],
            )
        raise ValidationProblem(
            detail=(
                f"{q!r} contains no letters or digits to search on. Enter a "
                "village name in Latin or Devanagari script."
            ),
            errors=[{"field": "q", "message": "no searchable characters"}],
        )
    if len(folded) < MIN_QUERY_CHARS:
        raise ValidationProblem(
            detail=(
                f"{q!r} reduces to {folded!r}, which is too short to search on -- "
                "it would match a large share of the register. Enter at least "
                f"{MIN_QUERY_CHARS} letters."
            ),
            errors=[{"field": "q", "message": "query too short after folding"}],
        )

    matches = service.search(session, q, state=state, district=district, limit=limit)
    filters = service.resolve_filters(session, state=state, district=district)
    note = _note_for(session, q, folded, state, district, matches)

    # Where two results share a name *and* a hierarchy, the name and place are
    # not enough to tell them apart and the caller has to show the code.
    seen: dict[str, int] = {}
    for match in matches:
        seen[match.display_hierarchy] = seen.get(match.display_hierarchy, 0) + 1
    ambiguous = {display for display, count in seen.items() if count > 1}

    return VillageSearchResponse(
        query=q,
        query_folded=folded,
        filters=filters,
        count=len(matches),
        results=[_match_out(m, ambiguous=ambiguous) for m in matches],
        note=note,
    )


@router.get(
    "/resolve",
    summary="Reverse-geocode a coordinate to the smallest area that contains it",
)
def resolve_point(
    session: Annotated[Session, Depends(get_db)],
    lon: Annotated[float, Query(ge=-180, le=180)],
    lat: Annotated[float, Query(ge=-90, le=90)],
) -> dict[str, object]:
    """Find the administrative area holding a point.

    Answers at sub-district precision, because village polygons are not seeded.
    The response says so rather than implying it found a village.
    """
    result = service.resolve_point(session, lon, lat)
    if result is None:
        raise UnanswerableProblem(
            detail=(
                f"({lat}, {lon}) falls outside every seeded sub-district. Either "
                "the point is outside India or the state covering it has not been "
                "seeded yet -- run `make seed STATE=<state>`."
            )
        )
    return result


@router.get(
    "/{village_id}",
    response_model=VillageDetailResponse,
    summary="Village metadata and canonical identifiers",
)
def get_village(
    session: Annotated[Session, Depends(get_db)],
    village_id: uuid.UUID,
) -> VillageDetailResponse:
    record = service.get(session, village_id)
    if record is None:
        raise NotFoundProblem(detail=f"no village with id {village_id}")
    return VillageDetailResponse.model_validate(record)


@router.get(
    "/{village_id}/boundary",
    response_model=VillageBoundaryResponse,
    summary="The best available boundary, labelled with what it outlines",
)
def get_boundary(
    session: Annotated[Session, Depends(get_db)],
    village_id: uuid.UUID,
) -> VillageBoundaryResponse:
    """Return a boundary polygon, always stating what it is the boundary *of*.

    Village polygons are not available for the seeded states, so this usually
    returns the containing sub-district. `represents`, `is_village_boundary` and
    `caveat` exist so no caller can compute a village area from a tehsil.
    """
    record = service.get_boundary(session, village_id)
    if record is None:
        raise NotFoundProblem(detail=f"no village with id {village_id}")
    return VillageBoundaryResponse.model_validate(record)


@router.get(
    "/{village_id}/imagery",
    response_model=VillageImageryResponse,
    summary="Satellite tile template, attribution and framing (FR-1)",
)
def get_imagery(
    session: Annotated[Session, Depends(get_db)],
    village_id: uuid.UUID,
) -> VillageImageryResponse:
    record = service.imagery_for(session, village_id)
    if record is None:
        raise NotFoundProblem(detail=f"no village with id {village_id}")
    return VillageImageryResponse.model_validate(record)


# ---------------------------------------------------------------------------


def _match_out(match: service.VillageMatch, *, ambiguous: set[str]) -> dict[str, object]:
    return {
        "id": str(match.id),
        "name": match.name.title(),
        "display": match.display_hierarchy,
        "hierarchy": {
            "state": _title(match.state),
            "district": _title(match.district),
            "subdistrict": _title(match.subdistrict),
            "block": None,
        },
        "identifiers": {
            "lgd_code": match.lgd_code,
            "census_2011_id": match.census_2011_id,
            "shrid": match.shrid,
        },
        "similarity": match.similarity,
        "matched_by": match.matched_by,
        "boundary_level": match.boundary_level,
        "gram_panchayats": [{"name": name, "lgd_code": code} for name, code in match.panchayats],
        "hierarchy_is_ambiguous": match.display_hierarchy in ambiguous,
        "focus": (
            None
            if match.focus_lon is None or match.focus_lat is None
            else {
                "lon": match.focus_lon,
                "lat": match.focus_lat,
                "is_centre_of": match.focus_level,
                "approximate": match.focus_level != "village",
            }
        ),
    }


def _title(value: str | None) -> str | None:
    return None if value is None else value.title()


#: A result at or above this similarity is what the caller was probably looking
#: for. Below it, the response is a list of things that merely resemble the query.
STRONG_MATCH = 0.9


def _note_for(
    session: Session,
    query: str,
    folded: str,
    state: str | None,
    district: str | None,
    matches: list[service.VillageMatch],
) -> str | None:
    """Explain a result that needs explaining, or return None.

    Fires on two situations, not one. An empty list obviously needs a reason.
    But so does a *filtered* search that came back with only weak matches: asking
    for `kutelabhatha` in Raipur returns five vaguely similar names and no
    indication that the village exists in Durg. Reporting nothing there leaves
    the caller to conclude their village is missing when the filter was simply
    wrong -- the most common cause and the easiest to fix.
    """
    if matches and max(m.similarity for m in matches) >= STRONG_MATCH:
        return None
    return _explain(session, query, folded, state, district, weak=bool(matches))


def _explain(
    session: Session,
    query: str,
    folded: str,
    state: str | None,
    district: str | None,
    *,
    weak: bool,
) -> str:
    if state or district:
        # Re-run unfiltered: if that finds it, the filter was the problem, which
        # is by far the most common cause and the easiest to act on.
        elsewhere = [
            m for m in service.search(session, query, limit=5) if m.similarity >= STRONG_MATCH
        ]
        if elsewhere:
            where = "; ".join(m.display_hierarchy for m in elsewhere[:3])
            applied = ", ".join(
                f"{k}={v!r}" for k, v in (("state", state), ("district", district)) if v
            )
            lead = f"Only weak matches inside {applied}" if weak else f"No match inside {applied}"
            return (
                f"{lead}, but {len(elsewhere)} strong match(es) elsewhere: {where}. "
                "Drop the filter, or correct it."
            )

    if weak:
        return (
            f"Nothing in the register matches {query!r} closely (searched as "
            f"{folded!r}); the results below only resemble it. Check the spelling, "
            "or widen the search."
        )

    states = service.seeded_states(session)
    if not states:
        return (
            "The village index is empty. Run `make seed STATE=<state>` to load it; "
            "no state has been seeded yet."
        )
    return (
        f"Nothing in the register matches {query!r} (searched as {folded!r}). "
        f"Seeded states: {', '.join(states)}. Check the spelling, or seed the "
        "state the village is in."
    )
