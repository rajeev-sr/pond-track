#!/usr/bin/env python3
"""Seed administrative boundaries and the village name index (M0-11).

What lands in the database, and from where:

===============  ====================================  ==================
Table            Source                                Licence
===============  ====================================  ==================
`admin_areas`    geoBoundaries gbOpen ADM1/2/3         ODbL 1.0
`villages`       SHRUG SHRID location names (CC0)      CC0 1.0
===============  ====================================  ==================

The two arrive at different granularities -- names go down to the village,
polygons stop at the sub-district -- so each village is linked to its containing
sub-district and `boundary_level` records that the polygon on offer is the
sub-district's, not the village's. See migration 0002 for why no keyless source
reaches village geometry.

Joining them is where the name fold earns its second keep. geoBoundaries writes
sub-district names in its own romanisation and SHRUG writes them in the Census
enumerator's; matching the raw strings loses a large fraction of them, while
matching `normalise_name` of each lands them together.

Idempotent: re-running updates rows in place, keyed on the source's own
identifier (`admin_areas.source_id`) and on the SHRID (`villages.shrid`).

    python -m scripts.seed_villages --state chhattisgarh
    python -m scripts.seed_villages --state chhattisgarh --district durg
    python -m scripts.seed_villages --state chhattisgarh --refresh
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.names import normalise_name
from app.db.session import get_sessionmaker
from app.providers.vector import geoboundaries, lgd_codes, shrug_names

log = logging.getLogger("seed_villages")

#: Cached downloads live here so a re-seed costs nothing. Inside the container
#: this is the mounted `./data` volume, so the files survive a rebuild.
DEFAULT_CACHE = Path("/data/seed")

#: Rows per executemany batch. Large enough that round-trips stop dominating,
#: small enough that a failure does not roll back an hour of work.
BATCH = 5_000

#: geoBoundaries levels to seed, coarsest first -- a district's parent must
#: exist before the district is linked to it.
LEVELS = ("ADM1", "ADM2", "ADM3")


@dataclass
class Counts:
    areas_written: int = 0
    villages_written: int = 0
    villages_linked: int = 0
    villages_unlinked: int = 0
    panchayats_written: int = 0
    panchayat_links: int = 0
    villages_with_panchayat: int = 0
    villages_multi_panchayat: int = 0

    def report(self) -> None:
        log.info("admin areas written/updated : %d", self.areas_written)
        log.info("villages written/updated    : %d", self.villages_written)
        log.info(
            "villages linked to a sub-district: %d (%d could not be matched)",
            self.villages_linked,
            self.villages_unlinked,
        )
        if self.panchayats_written or self.panchayat_links:
            log.info("gram panchayats written     : %d", self.panchayats_written)
            log.info(
                "village-panchayat links     : %d across %d villages "
                "(%d in more than one panchayat)",
                self.panchayat_links,
                self.villages_with_panchayat,
                self.villages_multi_panchayat,
            )


def seed_admin_areas(session: Session, cache: Path, *, refresh: bool) -> int:
    """Load state, district and sub-district polygons, then rebuild the hierarchy."""
    written = 0
    for level in LEVELS:
        path = cache / f"geoboundaries-IND-{level}.geojson"
        geoboundaries.download(path, iso3="IND", level=level, force=refresh)
        features = geoboundaries.read_features(path, level=level)
        log.info("%s: %d features", level, len(features))

        rows = [
            {
                "level": f.level,
                "name": f.name,
                "name_normalised": normalise_name(f.name)[:200],
                "source_id": f.source_id,
                "source": geoboundaries.SOURCE_NAME,
                "geojson": json.dumps(f.geometry),
            }
            for f in features
            if normalise_name(f.name)
        ]

        for start in range(0, len(rows), BATCH):
            chunk = rows[start : start + BATCH]
            session.execute(
                text(
                    """
                    INSERT INTO admin_areas
                        (id, level, name, name_normalised, source_id, source,
                         geom, centroid, area_ha)
                    VALUES (
                        gen_random_uuid(), :level, :name, :name_normalised,
                        :source_id, :source,
                        -- ST_Multi: the source mixes Polygon and MultiPolygon,
                        -- and the column accepts only the latter.
                        ST_Multi(ST_MakeValid(ST_GeomFromGeoJSON(:geojson))),
                        ST_PointOnSurface(ST_MakeValid(ST_GeomFromGeoJSON(:geojson))),
                        ST_Area(ST_GeomFromGeoJSON(:geojson)::geography) / 10000.0
                    )
                    ON CONFLICT (source, source_id) DO UPDATE SET
                        name            = EXCLUDED.name,
                        name_normalised = EXCLUDED.name_normalised,
                        level           = EXCLUDED.level,
                        geom            = EXCLUDED.geom,
                        centroid        = EXCLUDED.centroid,
                        area_ha         = EXCLUDED.area_ha
                    """
                ),
                chunk,
            )
            written += len(chunk)
            session.commit()
            log.info("  %s: %d/%d", level, min(start + BATCH, len(rows)), len(rows))

    _link_hierarchy(session)
    return written


def _link_hierarchy(session: Session) -> None:
    """Attach each area to its parent by containment.

    geoBoundaries features carry no parent reference at all, so the hierarchy has
    to be inferred. A representative interior point is used rather than the
    centroid -- `ST_PointOnSurface` is guaranteed to lie inside the polygon,
    whereas the centroid of a crescent-shaped district can fall outside it and
    would then be contained by a neighbour, silently mis-parenting the area.
    """
    for child, parent in (("district", "state"), ("subdistrict", "district")):
        result = session.execute(
            text(
                """
                UPDATE admin_areas AS c
                   SET parent_id = p.id
                  FROM admin_areas AS p
                 WHERE c.level = :child
                   AND p.level = :parent
                   AND ST_Contains(p.geom, c.centroid)
                """
            ),
            {"child": child, "parent": parent},
        )
        session.commit()
        # `Result` only exposes rowcount for DML; mypy types it on CursorResult.
        affected = getattr(result, "rowcount", -1)
        log.info("linked %d %s rows to their %s", affected, child, parent)

    orphans = session.execute(
        text("SELECT level, count(*) FROM admin_areas WHERE parent_id IS NULL GROUP BY level")
    ).all()
    for level, count in orphans:
        if level != "state":  # states have no parent by construction
            log.warning("%d %s rows have no parent -- boundary gaps or slivers", count, level)


def seed_villages(
    session: Session,
    cache: Path,
    *,
    state: str,
    district: str | None,
    refresh: bool,
) -> tuple[int, int, int]:
    """Load the village name index for one state, linked to sub-districts."""
    register = cache / "shrid_loc_names.tab"
    shrug_names.download(register, force=refresh)

    places = list(shrug_names.iter_places(register, state=state, district=district))
    if not places:
        raise SystemExit(
            f"no villages found for state={state!r} district={district!r}. "
            "Check the spelling against the register."
        )
    log.info("%d places for %s%s", len(places), state, f" / {district}" if district else "")

    rows = [
        {
            "shrid": p.shrid,
            "census_2011_id": p.census_village_code[:20],
            "name": p.name[:200],
            "name_normalised": p.name_normalised[:200],
            "state": p.state[:200],
            "district": p.district[:200],
            "subdistrict": p.subdistrict[:200],
            "source": "shrug_names",
        }
        for p in places
    ]

    written = 0
    for start in range(0, len(rows), BATCH):
        chunk = rows[start : start + BATCH]
        session.execute(
            text(
                """
                INSERT INTO villages
                    (id, shrid, census_2011_id, name, name_normalised,
                     state, district, subdistrict, source)
                VALUES (gen_random_uuid(), :shrid, :census_2011_id, :name,
                        :name_normalised, :state, :district, :subdistrict, :source)
                ON CONFLICT (shrid) DO UPDATE SET
                    name            = EXCLUDED.name,
                    name_normalised = EXCLUDED.name_normalised,
                    state           = EXCLUDED.state,
                    district        = EXCLUDED.district,
                    subdistrict     = EXCLUDED.subdistrict,
                    census_2011_id  = EXCLUDED.census_2011_id
                """
            ),
            chunk,
        )
        written += len(chunk)
        session.commit()
        log.info("  villages: %d/%d", min(start + BATCH, len(rows)), len(rows))

    linked, unlinked = _link_villages_to_subdistricts(session, places)
    return written, linked, unlinked


def _link_villages_to_subdistricts(
    session: Session, places: list[shrug_names.PlaceRecord]
) -> tuple[int, int]:
    """Point each village at its sub-district polygon.

    Matched in Python rather than SQL on purpose. The fold lives in exactly one
    place -- `app.core.names` -- and re-implementing it as a SQL function would
    create a second copy free to drift from the first, which is precisely the
    failure that makes seeded rows unfindable.

    It works from the records just written rather than re-querying by state name.
    An earlier version selected `WHERE state = :state` using the raw `--state`
    argument, which meant `--state Chhattisgarh` matched none of the rows the
    register stores as `chhattisgarh` and silently linked nothing at all. The
    records are already in hand; asking the database to find them again by a
    string whose case nobody controls only invents a way to get it wrong.

    A sub-district name alone is not unique nationally, so the key is
    `(district, sub-district)`; `admin_areas.parent_id` supplies the district,
    which `_link_hierarchy` established by containment.

    That key alone is not enough, because **the two sources disagree about which
    district a sub-district is in.** The register is Census 2011; the boundaries
    are 2018. Chhattisgarh split Durg into Durg, Balod and Bemetara in 2012, so
    the register places Nawagarh in Durg while the boundary set places it in
    Bemetara, and 279 villages in our own target district failed to match on a
    district name that had simply moved.

    So `(state, sub-district)` is tried next. State boundaries survive district
    reorganisation far better than district boundaries do, which makes it the
    more durable key -- it is only second because it is the less specific one.
    A name unique nationally is accepted last.

    Each key is tried on the **exact** name before the folded one. The fold is
    built for retrieval, where merging `Balod` and `Baloda` costs nothing -- both
    appear in the results and the district column separates them. As a join key
    it is wrong: those are two distinct sub-districts of Chhattisgarh, and
    folding them together manufactured an ambiguity that blocked 92 villages
    from linking to a polygon that was sitting right there. Exact first, folded
    second.

    Where every key is ambiguous the row is left unlinked rather than attached
    to a guess: a village pointing at the wrong tehsil would place it in the
    wrong part of the country on the map, which is worse than no polygon. Two
    Nawagarh tehsils in one state, in different districts, is real ambiguity and
    refusing to choose is the correct outcome.

    `boundary_level` is set to `subdistrict`, so no consumer can mistake the
    polygon for the village's own outline.
    """
    subdistricts = session.execute(
        text(
            """
            SELECT a.id,
                   lower(a.name)     AS sub_exact,
                   a.name_normalised AS sub_folded,
                   lower(d.name)     AS district_exact,
                   d.name_normalised AS district_folded,
                   lower(s.name)     AS state_exact,
                   s.name_normalised AS state_folded
              FROM admin_areas AS a
              LEFT JOIN admin_areas AS d ON d.id = a.parent_id
              LEFT JOIN admin_areas AS s ON s.id = d.parent_id
             WHERE a.level = 'subdistrict'
            """
        )
    ).all()

    # Two parallel sets of indexes: exact lower-cased names, and folded names.
    # Every lookup tries exact before folded. Values are lists so ambiguity is
    # visible rather than silently resolved by whichever row was seen last.
    exact_by_district: dict[tuple[str, str], list[str]] = {}
    exact_by_state: dict[tuple[str, str], list[str]] = {}
    exact_by_sub: dict[str, list[str]] = {}
    folded_by_district: dict[tuple[str, str], list[str]] = {}
    folded_by_state: dict[tuple[str, str], list[str]] = {}
    folded_by_sub: dict[str, list[str]] = {}
    for row in subdistricts:
        area_id, sub_x, sub_f, dist_x, dist_f, state_x, state_f = row
        if dist_x:
            exact_by_district.setdefault((dist_x, sub_x), []).append(area_id)
        if dist_f:
            folded_by_district.setdefault((dist_f, sub_f), []).append(area_id)
        if state_x:
            exact_by_state.setdefault((state_x, sub_x), []).append(area_id)
        if state_f:
            folded_by_state.setdefault((state_f, sub_f), []).append(area_id)
        exact_by_sub.setdefault(sub_x, []).append(area_id)
        folded_by_sub.setdefault(sub_f, []).append(area_id)

    def only(candidates: list[str] | None) -> str | None:
        """The single candidate, or nothing. Ambiguity is never resolved by guess."""
        return candidates[0] if candidates and len(candidates) == 1 else None

    updates: list[dict[str, str]] = []
    unmatched: dict[str, int] = {}
    for place in places:
        shrid, district_name, subdistrict_name = place.shrid, place.district, place.subdistrict
        sub_x = (subdistrict_name or "").strip().lower()
        dist_x = (district_name or "").strip().lower()
        state_x = (place.state or "").strip().lower()
        sub_f = normalise_name(subdistrict_name or "")
        dist_f = normalise_name(district_name or "")
        state_f = normalise_name(place.state or "")

        area_id = (
            # Most specific first: exact district + exact sub-district.
            only(exact_by_district.get((dist_x, sub_x)))
            or only(folded_by_district.get((dist_f, sub_f)))
            # The district may have been reorganised; the state will not have.
            or only(exact_by_state.get((state_x, sub_x)))
            or only(folded_by_state.get((state_f, sub_f)))
            # Last resort: a sub-district name unique across the whole country.
            or only(exact_by_sub.get(sub_x))
            or only(folded_by_sub.get(sub_f))
        )
        if area_id is None:
            unmatched[subdistrict_name or "(none)"] = (
                unmatched.get(subdistrict_name or "(none)", 0) + 1
            )
            continue
        updates.append({"shrid": shrid, "area_id": str(area_id)})

    for start_index in range(0, len(updates), BATCH):
        session.execute(
            text(
                """
                UPDATE villages
                   SET admin_area_id = CAST(:area_id AS uuid),
                       boundary_level = 'subdistrict'
                 WHERE shrid = :shrid
                """
            ),
            updates[start_index : start_index + BATCH],
        )
        session.commit()

    if unmatched:
        log.warning(
            "%d villages across %d sub-districts could not be matched to a polygon",
            sum(unmatched.values()),
            len(unmatched),
        )
        for name, count in sorted(unmatched.items(), key=lambda kv: -kv[1])[:10]:
            log.warning("    %-28s %d villages (folds to %r)", name, count, normalise_name(name))

    return len(updates), sum(unmatched.values())


def seed_panchayats(
    session: Session, cache: Path, *, state: str, refresh: bool
) -> tuple[int, int, int, int]:
    """Load Gram Panchayats and link them to the villages already seeded.

    The join is on the **Census 2011 village code**, never on names: this file's
    district and sub-district columns reflect a later reorganisation than the
    name register's, so the hierarchies disagree while the codes do not.

    Returns (panchayats, links, villages_with_a_panchayat, villages_with_several).
    """
    crosswalk = cache / "village_to_gp_lgd_codes.tab"
    lgd_codes.download(crosswalk, force=refresh)

    links = list(lgd_codes.iter_links(crosswalk, state=state))
    if not links:
        log.warning("no panchayat rows for state=%r; skipping", state)
        return (0, 0, 0, 0)
    log.info("%d village-panchayat rows for %s", len(links), state)

    # Resolved once. Calling this per row would issue a query per row -- a
    # database round-trip for a value that cannot change during the run.
    stored_state = _stored_state(session, state)

    # One row per Panchayat, keyed on its LGD code. The crosswalk repeats a
    # Panchayat once per village in it, so this collapses ~10x.
    panchayats: dict[str, dict[str, str]] = {}
    for link in links:
        panchayats.setdefault(
            link.gp_lgd_code,
            {
                "lgd_code": link.gp_lgd_code,
                "name": link.gp_name[:200],
                "name_normalised": link.gp_name_normalised[:200],
                "state": link.state_name[:200],
                "district": link.district_name[:200],
                "subdistrict": link.subdistrict_name[:200],
                "source": "shrug_lgd_crosswalk",
            },
        )
    rows = list(panchayats.values())
    for start in range(0, len(rows), BATCH):
        session.execute(
            text(
                """
                INSERT INTO gram_panchayats
                    (id, lgd_code, name, name_normalised, state, district,
                     subdistrict, source)
                VALUES (gen_random_uuid(), :lgd_code, :name, :name_normalised,
                        :state, :district, :subdistrict, :source)
                ON CONFLICT (lgd_code) DO UPDATE SET
                    name            = EXCLUDED.name,
                    name_normalised = EXCLUDED.name_normalised,
                    state           = EXCLUDED.state,
                    district        = EXCLUDED.district,
                    subdistrict     = EXCLUDED.subdistrict
                """
            ),
            rows[start : start + BATCH],
        )
        session.commit()
    log.info("gram panchayats: %d distinct", len(rows))

    # The Census 2001 code travels in the same file; store it while we have it.
    census_2001 = [
        {"code_2011": link.village_census_2011, "code_2001": link.census_2001}
        for link in links
        if link.census_2001
    ]
    for start in range(0, len(census_2001), BATCH):
        session.execute(
            text(
                """
                UPDATE villages SET census_2001_id = :code_2001
                 WHERE census_2011_id = :code_2011 AND state = :state
                """
            ),
            [{**row, "state": stored_state} for row in census_2001[start : start + BATCH]],
        )
        session.commit()

    link_rows = [
        {
            "code_2011": link.village_census_2011,
            "gp_lgd": link.gp_lgd_code,
            "state": stored_state,
        }
        for link in links
    ]
    written = 0
    for start in range(0, len(link_rows), BATCH):
        session.execute(
            text(
                """
                INSERT INTO village_gram_panchayats (village_id, gram_panchayat_id)
                SELECT v.id, g.id
                  FROM villages v
                  JOIN gram_panchayats g ON g.lgd_code = :gp_lgd
                 WHERE v.census_2011_id = :code_2011
                   AND v.state = :state
                ON CONFLICT DO NOTHING
                """
            ),
            link_rows[start : start + BATCH],
        )
        session.commit()
        written += len(link_rows[start : start + BATCH])

    # How many source rows found a village to attach to. A silent shortfall here
    # would look like the crosswalk simply having fewer villages, when the real
    # cause is the two files being different vintages -- the crosswalk lists
    # villages the 2011 name register does not, and vice versa.
    source_villages = len({link.village_census_2011 for link in links})
    attached = session.execute(
        text(
            """
            SELECT count(DISTINCT vg.village_id)
              FROM village_gram_panchayats vg
              JOIN villages v ON v.id = vg.village_id
             WHERE v.state = :state
            """
        ),
        {"state": stored_state},
    ).scalar_one()
    if attached < source_villages:
        log.warning(
            "%d of %d villages in the crosswalk had no seeded village to attach to "
            "(census codes present in the LGD file but not in the name register)",
            source_villages - int(attached),
            source_villages,
        )

    stats = session.execute(
        text(
            """
            SELECT count(*) AS links,
                   count(DISTINCT village_id) AS villages,
                   count(*) FILTER (WHERE n > 1) AS multi
              FROM (
                SELECT vg.village_id,
                       vg.gram_panchayat_id,
                       count(*) OVER (PARTITION BY vg.village_id) AS n
                  FROM village_gram_panchayats vg
                  JOIN villages v ON v.id = vg.village_id
                 WHERE v.state = :state
              ) t
            """
        ),
        {"state": stored_state},
    ).one()
    return (len(rows), int(stats[0]), int(stats[1]), int(stats[2]))


def _stored_state(session: Session, state: str) -> str:
    """The spelling the seeded rows actually hold, for a caller-supplied state."""
    folded = normalise_name(state)
    for candidate in session.execute(
        text("SELECT DISTINCT state FROM villages WHERE state IS NOT NULL")
    ).scalars():
        if normalise_name(candidate) == folded:
            return str(candidate)
    return state


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", default="chhattisgarh", help="state to seed villages for")
    parser.add_argument("--district", default=None, help="optional single district")
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE, help="download cache")
    parser.add_argument("--refresh", action="store_true", help="re-download the sources")
    parser.add_argument(
        "--skip-boundaries", action="store_true", help="reuse admin_areas already in the database"
    )
    parser.add_argument(
        "--skip-panchayats",
        action="store_true",
        help="skip the Gram Panchayat crosswalk (a further 56 MB download)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", stream=sys.stdout
    )
    counts = Counts()
    with get_sessionmaker()() as session:
        if not args.skip_boundaries:
            counts.areas_written = seed_admin_areas(session, args.cache, refresh=args.refresh)
        written, linked, unlinked = seed_villages(
            session, args.cache, state=args.state, district=args.district, refresh=args.refresh
        )
        counts.villages_written = written
        counts.villages_linked = linked
        counts.villages_unlinked = unlinked
        if not args.skip_panchayats:
            (
                counts.panchayats_written,
                counts.panchayat_links,
                counts.villages_with_panchayat,
                counts.villages_multi_panchayat,
            ) = seed_panchayats(session, args.cache, state=args.state, refresh=args.refresh)
    counts.report()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
