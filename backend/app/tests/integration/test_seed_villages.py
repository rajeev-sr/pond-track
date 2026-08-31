"""The village seeder's linking logic, against a real PostGIS database.

Only the linking step is exercised, on a handful of hand-built rows. Seeding
Chhattisgarh for real means 19,715 villages and 250 MB of downloads, which is a
job for `make seed`, not for a test suite. What is worth testing is the matching,
because every failure mode found while building it was silent: villages that
linked to nothing, or worse, would have linked to the wrong tehsil.

Skipped unless a database is reachable.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.providers.vector.shrug_names import PlaceRecord

pytestmark = pytest.mark.integration


def place(
    shrid: str,
    *,
    name: str,
    state: str = "chhattisgarh",
    district: str = "durg",
    subdistrict: str = "durg",
) -> PlaceRecord:
    from app.core.names import normalise_name

    return PlaceRecord(
        shrid=shrid,
        state=state,
        district=district,
        subdistrict=subdistrict,
        name=name,
        name_normalised=normalise_name(name),
        is_town=False,
    )


@pytest.fixture
def session() -> Iterator[Session]:
    from sqlalchemy.exc import OperationalError

    from app.db.session import get_sessionmaker

    try:
        with get_sessionmaker()() as db:
            db.execute(text("SELECT 1"))
            yield db
    except OperationalError as exc:
        pytest.skip(f"no database reachable: {exc.__class__.__name__}")


@pytest.fixture
def scratch(session: Session) -> Iterator[str]:
    """A unique tag so these rows never collide with a real seed, and are removed."""
    tag = f"test-{uuid.uuid4().hex[:12]}"
    yield tag
    session.execute(text("DELETE FROM villages WHERE shrid LIKE :like"), {"like": f"{tag}%"})
    session.execute(text("DELETE FROM admin_areas WHERE source = :src"), {"src": tag})
    session.commit()


def insert_area(
    session: Session,
    *,
    tag: str,
    level: str,
    name: str,
    parent_id: str | None = None,
) -> str:
    """Insert one admin area and return its id.

    `source_id` gets a random suffix because `(source, source_id)` is unique and
    the ambiguity test deliberately inserts two sub-districts with the *same*
    name -- which is the situation being tested, not a mistake to be deduplicated
    away.
    """
    from app.core.names import normalise_name

    # A small square somewhere harmless; geometry is NOT NULL but the matching
    # under test is by name, so the shape only has to be valid.
    area_id = session.execute(
        text(
            """
            INSERT INTO admin_areas
                (id, level, name, name_normalised, source_id, source, parent_id, geom)
            VALUES (gen_random_uuid(), :level, :name, :folded, :source_id, :src,
                    CAST(:parent AS uuid),
                    ST_Multi(ST_GeomFromText(
                        'POLYGON((81 21, 81.1 21, 81.1 21.1, 81 21.1, 81 21))', 4326)))
            RETURNING id
            """
        ),
        {
            "level": level,
            "name": name,
            "folded": normalise_name(name),
            "source_id": f"{tag}-{level}-{name}-{uuid.uuid4().hex[:8]}",
            "src": tag,
            "parent": parent_id,
        },
    ).scalar_one()
    session.commit()
    return str(area_id)


def insert_villages(session: Session, places: list[PlaceRecord]) -> None:
    session.execute(
        text(
            """
            INSERT INTO villages
                (id, shrid, name, name_normalised, state, district, subdistrict, source)
            VALUES (gen_random_uuid(), :shrid, :name, :folded, :state, :district,
                    :subdistrict, 'test')
            """
        ),
        [
            {
                "shrid": p.shrid,
                "name": p.name,
                "folded": p.name_normalised,
                "state": p.state,
                "district": p.district,
                "subdistrict": p.subdistrict,
            }
            for p in places
        ],
    )
    session.commit()


def link(session: Session, places: list[PlaceRecord]) -> tuple[int, int]:
    from scripts.seed_villages import _link_villages_to_subdistricts

    return _link_villages_to_subdistricts(session, places)


def linked_area_name(session: Session, shrid: str) -> str | None:
    return session.execute(
        text(
            """
            SELECT a.name FROM villages v
              JOIN admin_areas a ON a.id = v.admin_area_id
             WHERE v.shrid = :shrid
            """
        ),
        {"shrid": shrid},
    ).scalar_one_or_none()


class TestNamesThatFoldTogetherButAreDifferentPlaces:
    """`Balod` and `Baloda` both fold to `balod`, and are two sub-districts.

    Folding is right for retrieval and wrong as a join key. Before exact
    matching was tried first, this collision left 92 real villages with no
    polygon at all -- one that was sitting in the table the whole time.
    """

    def test_each_village_reaches_its_own_sub_district(
        self, session: Session, scratch: str
    ) -> None:
        state = insert_area(session, tag=scratch, level="state", name="Chhattisgarh")
        district = insert_area(session, tag=scratch, level="district", name="Durg", parent_id=state)
        insert_area(session, tag=scratch, level="subdistrict", name="Balod", parent_id=district)
        insert_area(session, tag=scratch, level="subdistrict", name="Baloda", parent_id=district)

        places = [
            place(f"{scratch}-1", name="Testgaon A", subdistrict="balod"),
            place(f"{scratch}-2", name="Testgaon B", subdistrict="baloda"),
        ]
        insert_villages(session, places)
        matched, unmatched = link(session, places)

        assert (matched, unmatched) == (2, 0)
        assert linked_area_name(session, f"{scratch}-1") == "Balod"
        assert linked_area_name(session, f"{scratch}-2") == "Baloda"


class TestASubDistrictWhoseDistrictWasReorganised:
    """Census 2011 says one district; the 2018 boundaries say another.

    Chhattisgarh split Durg in 2012, so the register places Nawagarh in Durg
    while the boundary set places it in Bemetara. Matching on district alone
    lost 279 villages in the target district; the state is the stable key.
    """

    def test_it_still_finds_the_polygon_through_the_state(
        self, session: Session, scratch: str
    ) -> None:
        state = insert_area(session, tag=scratch, level="state", name="Chhattisgarh")
        # The sub-district now sits under a district the register never heard of.
        new_district = insert_area(
            session, tag=scratch, level="district", name="Bemetara", parent_id=state
        )
        insert_area(
            session,
            tag=scratch,
            level="subdistrict",
            name="Testnawagarh",
            parent_id=new_district,
        )

        places = [
            place(f"{scratch}-1", name="Testgaon C", district="durg", subdistrict="testnawagarh")
        ]
        insert_villages(session, places)
        matched, unmatched = link(session, places)

        assert (matched, unmatched) == (1, 0)
        assert linked_area_name(session, f"{scratch}-1") == "Testnawagarh"


class TestGenuineAmbiguity:
    """Two identically-named sub-districts in one state, in different districts.

    Nothing available distinguishes them, so the row is left unlinked. Attaching
    it to whichever was seen first would put the village in the wrong part of
    the state on the map, which is a worse answer than no polygon.
    """

    def test_it_refuses_to_choose(self, session: Session, scratch: str) -> None:
        state = insert_area(session, tag=scratch, level="state", name="Chhattisgarh")
        first = insert_area(
            session, tag=scratch, level="district", name="Bemetara", parent_id=state
        )
        second = insert_area(
            session, tag=scratch, level="district", name="Janjgir", parent_id=state
        )
        insert_area(session, tag=scratch, level="subdistrict", name="Testambig", parent_id=first)
        insert_area(session, tag=scratch, level="subdistrict", name="Testambig", parent_id=second)

        places = [
            place(f"{scratch}-1", name="Testgaon D", district="raipur", subdistrict="testambig")
        ]
        insert_villages(session, places)
        matched, unmatched = link(session, places)

        assert (matched, unmatched) == (0, 1)
        assert linked_area_name(session, f"{scratch}-1") is None


class TestWhatTheLinkAsserts:
    def test_the_boundary_level_says_it_is_not_the_village_outline(
        self, session: Session, scratch: str
    ) -> None:
        """The one thing that must never be implied: a village boundary."""
        state = insert_area(session, tag=scratch, level="state", name="Chhattisgarh")
        district = insert_area(session, tag=scratch, level="district", name="Durg", parent_id=state)
        insert_area(session, tag=scratch, level="subdistrict", name="Testdurg", parent_id=district)

        places = [place(f"{scratch}-1", name="Testgaon E", subdistrict="testdurg")]
        insert_villages(session, places)
        link(session, places)

        level = session.execute(
            text("SELECT boundary_level FROM villages WHERE shrid = :shrid"),
            {"shrid": f"{scratch}-1"},
        ).scalar_one()
        assert level == "subdistrict"
