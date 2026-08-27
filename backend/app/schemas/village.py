"""Response shapes for the village endpoints (M2-1, M2-2).

Every response that offers a geometry or a coordinate also says what that
geometry or coordinate *is*. That is not decoration: village polygons are not
available for this state, so what the API can offer is the containing
sub-district, and a caller who mistakes a 662 km² tehsil outline for a village
boundary will compute a catchment-to-village ratio that is wrong by three orders
of magnitude without anything looking broken.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

BoundaryLevel = Literal["village", "subdistrict", "district", "state"]
MatchRule = Literal["exact", "folded", "prefix", "trigram"]


class Focus(BaseModel):
    """A point to centre a map on."""

    lon: float
    lat: float
    is_centre_of: str | None = Field(
        default=None,
        description="What this point is the centre of: 'village', 'subdistrict', …",
    )
    approximate: bool = Field(
        description=(
            "True when the point is not the village's own centroid. A "
            "sub-district centre is fine for framing a map and wrong as the "
            "village's location."
        )
    )


class GramPanchayatRef(BaseModel):
    """An elected local body a village belongs to.

    Carries the only LGD code available in open data. It is *not* the village's
    own code — a Panchayat covers a cluster of villages — so it is presented as
    what it is rather than filling `identifiers.lgd_code`.
    """

    name: str
    lgd_code: str = Field(description="Local Government Directory code for the Panchayat")


class VillageIdentifiers(BaseModel):
    """HLD CH-24: the canonical key is a code, never a name."""

    lgd_code: str | None = Field(
        default=None,
        description=(
            "Local Government Directory code for the *village*. Always null: no "
            "open source publishes one. The LGD code that is available belongs to "
            "the Gram Panchayat and appears under `gram_panchayats`. Use "
            "`census_2011_id` or `shrid` as the canonical key."
        ),
    )
    census_2011_id: str | None = Field(default=None, description="Census 2011 village code")
    census_2001_id: str | None = Field(
        default=None, description="Census 2001 village code, for joining older records"
    )
    shrid: str | None = Field(
        default=None,
        description=(
            "SHRUG composite id: version-state-district-subdistrict-village. "
            "Consecutive village codes are geographic neighbours."
        ),
    )


class Hierarchy(BaseModel):
    state: str | None = None
    district: str | None = None
    subdistrict: str | None = Field(
        default=None, description="Census sub-district (tehsil) — the level above the village"
    )
    block: str | None = Field(
        default=None, description="CD Block — a rural-development unit, not the sub-district"
    )


class VillageMatchOut(BaseModel):
    """One search result, with what it took to match it."""

    id: str
    name: str
    display: str = Field(
        description="Name plus hierarchy, which is what separates three villages called Khapri"
    )
    hierarchy: Hierarchy
    identifiers: VillageIdentifiers
    similarity: float = Field(
        ge=0.0,
        le=1.0,
        description="Trigram similarity between the folded query and the folded name",
    )
    matched_by: MatchRule = Field(
        description=(
            "exact — the folded name equals the folded query; folded — matched only "
            "after transliteration folding; prefix — the query is the start of the "
            "name; trigram — fuzzy match only."
        )
    )
    boundary_level: BoundaryLevel | None = Field(
        default=None, description="What geometry this village can offer, if any"
    )
    gram_panchayats: list[GramPanchayatRef] = Field(
        default_factory=list,
        description=(
            "The Panchayat(s) this village belongs to. Where "
            "`hierarchy_is_ambiguous` is set, this is what tells the namesakes "
            "apart: Durg's two Khapris are in `Khapri K` and `Khapri`."
        ),
    )
    hierarchy_is_ambiguous: bool = Field(
        default=False,
        description=(
            "True when another result in this response has the same name *and* "
            "the same hierarchy. Durg district holds ten villages called Khapri, "
            "two of them in the same sub-district, so the hierarchy is not always "
            "enough — show the identifier alongside the name when this is set "
            "(HLD CH-24)."
        ),
    )
    focus: Focus | None = None


class VillageSearchResponse(BaseModel):
    query: str = Field(description="What the caller sent, unchanged")
    query_folded: str = Field(
        description=(
            "The canonical form actually searched on. Both the stored name and "
            "the query pass through the same fold, which is how a Devanagari or "
            "differently-spelled query finds the register's spelling."
        )
    )
    filters: dict[str, str | None] = Field(
        default_factory=dict, description="State/district filters as resolved against the register"
    )
    count: int
    results: list[VillageMatchOut]
    note: str | None = Field(
        default=None, description="Present when the result needs explaining — e.g. nothing matched"
    )


class BoundarySummary(BaseModel):
    level: BoundaryLevel | None = None
    is_village_boundary: bool
    of: str | None = Field(default=None, description="The area the polygon actually outlines")
    area_ha: float | None = None


class VillageDetailResponse(BaseModel):
    id: str
    name: str
    name_as_recorded: str = Field(
        description="Exactly as the Census register holds it, lower-cased and all"
    )
    identifiers: VillageIdentifiers
    hierarchy: Hierarchy
    focus: Focus | None = None
    boundary: BoundarySummary
    gram_panchayats: list[GramPanchayatRef] = Field(default_factory=list)
    source: str


class VillageBoundaryResponse(BaseModel):
    village_id: str
    available: bool
    reason: str | None = Field(
        default=None, description="Why no boundary could be offered, when none could"
    )
    geometry: dict[str, Any] | None = Field(
        default=None, description="GeoJSON geometry in EPSG:4326"
    )
    represents: BoundaryLevel | None = Field(
        default=None, description="What this polygon outlines — read this before using its area"
    )
    is_village_boundary: bool = False
    of: str | None = None
    area_ha: float | None = None
    source: str | None = None
    caveat: str | None = Field(
        default=None,
        description="Set whenever the polygon is coarser than the village that was asked for",
    )


class ImagerySpec(BaseModel):
    provider: str
    tile_url_template: str
    tile_size: int
    min_zoom: int
    max_zoom: int
    attribution: str = Field(description="Must be displayed — a licence condition, not a courtesy")
    scheme: str
    note: str | None = None


class VillageImageryResponse(BaseModel):
    village_id: str
    imagery: ImagerySpec
    focus: Focus | None = None
    bounds_4326: list[float] | None = Field(
        default=None, description="[min_lon, min_lat, max_lon, max_lat] to fit the view to"
    )
    bounds_of: str | None = Field(
        default=None, description="What those bounds enclose — a sub-district, not the village"
    )


class MatchedArea(BaseModel):
    level: str
    name: str | None = None
    district: str | None = None
    state: str | None = None
    area_ha: float | None = None


class ResolvePointResponse(BaseModel):
    point: dict[str, float]
    matched: MatchedArea
    villages_recorded_here: int
    precision: str = Field(description="How far this answer actually narrows — stated, not implied")
