"""The village register and the boundary set, parsed offline.

Both providers download tens of megabytes in real use, so everything here runs
against a handful of synthetic rows. The cases are the ones that actually bit
during seeding: quoted TSV fields, towns mixed in with villages, a level whose
meaning changed upstream, and a truncated download that must not be mistaken for
a complete one.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from app.providers.vector import geoboundaries, shrug_names

HEADER = "shrid2\tstate_name\tdistrict_name\tsubdistrict_name\ttown_name\tvillage_name\tplace_name"


def register(*rows: str) -> io.StringIO:
    """A register handle with the real header and the given rows."""
    return io.StringIO("\n".join([HEADER, *rows]) + "\n")


def row(
    shrid: str = "11-22-409-03317-442569",
    state: str = "chhattisgarh",
    district: str = "durg",
    subdistrict: str = "durg",
    town: str = "",
    village: str = "kutelabhatha",
) -> str:
    place = village or town
    return "\t".join(f'"{v}"' for v in (shrid, state, district, subdistrict, town, village, place))


class TestReadingTheRegister:
    def test_it_reads_a_village(self) -> None:
        (place,) = shrug_names.iter_places(register(row()))
        assert place.shrid == "11-22-409-03317-442569"
        assert place.name == "kutelabhatha"
        assert place.state == "chhattisgarh"
        assert place.district == "durg"
        assert place.subdistrict == "durg"
        assert place.is_town is False

    def test_the_surrounding_quotes_are_stripped(self) -> None:
        """The register quotes every field; a stored `"durg"` would never match."""
        (place,) = shrug_names.iter_places(register(row()))
        assert '"' not in place.shrid
        assert '"' not in place.district

    def test_the_census_code_is_the_shrid_tail(self) -> None:
        (place,) = shrug_names.iter_places(register(row()))
        assert place.census_village_code == "442569"

    def test_the_normalised_name_comes_from_the_shared_fold(self) -> None:
        """Both sides of search must use one fold; this is the seeding side."""
        from app.core.names import normalise_name

        (place,) = shrug_names.iter_places(register(row(village="Kutelabhatha")))
        assert place.name_normalised == normalise_name("Kutelabhata")

    def test_a_town_is_flagged_as_one(self) -> None:
        (place,) = shrug_names.iter_places(register(row(town="bhilai nagar", village="")))
        assert place.is_town is True
        assert place.name == "bhilai nagar"

    def test_rows_with_neither_name_are_skipped(self) -> None:
        assert list(shrug_names.iter_places(register(row(town="", village="")))) == []

    def test_a_name_that_folds_to_nothing_is_skipped(self) -> None:
        """The column is NOT NULL and such a row could never be found anyway."""
        assert list(shrug_names.iter_places(register(row(village="---")))) == []

    def test_rows_with_no_shrid_are_skipped(self) -> None:
        """The SHRID is the canonical key; a row without one has no identity."""
        assert list(shrug_names.iter_places(register(row(shrid="")))) == []


class TestNarrowingTheRegister:
    ROWS = (
        row(shrid="11-22-409-03317-442569", village="kutelabhatha"),
        row(shrid="11-22-409-03311-441749", subdistrict="nawagarh", village="jeora"),
        row(
            shrid="11-22-406-03296-438885",
            district="bilaspur",
            subdistrict="masturi",
            village="kutela",
        ),
        row(
            shrid="11-27-521-04212-556001",
            state="maharashtra",
            district="pune",
            subdistrict="haveli",
            village="wagholi",
        ),
    )

    def test_by_state(self) -> None:
        places = list(shrug_names.iter_places(register(*self.ROWS), state="chhattisgarh"))
        assert {p.state for p in places} == {"chhattisgarh"}
        assert len(places) == 3

    def test_by_state_and_district(self) -> None:
        places = list(
            shrug_names.iter_places(register(*self.ROWS), state="chhattisgarh", district="durg")
        )
        assert len(places) == 2

    def test_the_filter_folds_too(self) -> None:
        """`--state Chhattisgarh` must match the register's `chhattisgarh`.

        An earlier version compared raw strings here, so a capitalised argument
        silently matched nothing at all.
        """
        assert len(list(shrug_names.iter_places(register(*self.ROWS), state="Chhattisgarh"))) == 3
        assert len(list(shrug_names.iter_places(register(*self.ROWS), state="CHHATTISGARH"))) == 3

    def test_an_unknown_state_yields_nothing_rather_than_everything(self) -> None:
        assert list(shrug_names.iter_places(register(*self.ROWS), state="atlantis")) == []


class TestRegisterFailures:
    def test_a_missing_column_is_named(self) -> None:
        handle = io.StringIO('shrid2\tstate_name\n"x"\t"y"\n')
        with pytest.raises(shrug_names.ShrugNamesError, match="missing expected columns"):
            list(shrug_names.iter_places(handle))

    def test_a_truncated_cache_is_not_accepted_as_the_register(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A short cached file must be rejected, not reused.

        Silently seeding from a partial download would leave most of India
        missing with nothing in the logs to say so. Rejecting it means a
        re-download is attempted -- pointed at a closed port here, so the call
        raises rather than reaching the network, which is the proof that the
        stub was not simply handed back.
        """
        stub = tmp_path / "register.tab"
        stub.write_text("too short", encoding="utf-8")
        with caplog.at_level("WARNING"), pytest.raises(shrug_names.ShrugNamesError):
            shrug_names.download(stub, url="http://127.0.0.1:1/nope")
        assert any(
            "re-downloading" in record.message for record in caplog.records
        ), "the undersized cache was not reported as rejected"

    def test_a_complete_cache_is_reused_without_a_download(self, tmp_path: Path) -> None:
        """Re-seeding must not re-fetch 51 MB it already has."""
        cached = tmp_path / "register.tab"
        cached.write_bytes(b"x" * (shrug_names.EXPECTED_MIN_BYTES + 1))
        # A closed port: reached only if the cache is ignored.
        assert shrug_names.download(cached, url="http://127.0.0.1:1/nope") == cached


class TestReadingBoundaries:
    def feature_collection(self, *features: dict) -> dict:
        return {"type": "FeatureCollection", "features": list(features)}

    def feature(self, name: str = "Durg", shape_id: str = "IND-3-1") -> dict:
        return {
            "type": "Feature",
            "properties": {"shapeName": name, "shapeID": shape_id, "shapeType": "ADM3"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [[81.2, 21.2], [81.4, 21.2], [81.4, 21.4], [81.2, 21.4], [81.2, 21.2]]
                ],
            },
        }

    def test_it_reads_a_boundary(self, tmp_path: Path) -> None:
        path = tmp_path / "adm3.geojson"
        path.write_text(json.dumps(self.feature_collection(self.feature())), encoding="utf-8")
        (boundary,) = geoboundaries.read_features(path, level="ADM3")
        assert boundary.name == "Durg"
        assert boundary.source_id == "IND-3-1"
        assert boundary.geometry["type"] == "Polygon"

    def test_the_level_is_recorded_as_what_it_means_here(self, tmp_path: Path) -> None:
        """ADM3 is a sub-district in India; the column stores the meaning."""
        path = tmp_path / "adm3.geojson"
        path.write_text(json.dumps(self.feature_collection(self.feature())), encoding="utf-8")
        (boundary,) = geoboundaries.read_features(path, level="ADM3")
        assert boundary.level == "subdistrict"

    def test_features_missing_an_id_or_name_are_skipped_not_guessed(self, tmp_path: Path) -> None:
        nameless = self.feature(name="", shape_id="IND-3-2")
        idless = self.feature(name="Patan", shape_id="")
        path = tmp_path / "adm3.geojson"
        path.write_text(
            json.dumps(self.feature_collection(self.feature(), nameless, idless)),
            encoding="utf-8",
        )
        boundaries = geoboundaries.read_features(path, level="ADM3")
        assert [b.name for b in boundaries] == ["Durg"]

    def test_an_empty_collection_is_an_error_not_an_empty_seed(self, tmp_path: Path) -> None:
        path = tmp_path / "adm3.geojson"
        path.write_text(json.dumps({"type": "FeatureCollection", "features": []}), encoding="utf-8")
        with pytest.raises(geoboundaries.GeoBoundariesError, match="no features"):
            geoboundaries.read_features(path, level="ADM3")


class TestIndiaLevelMeanings:
    def test_the_mapping_is_stated_not_assumed(self) -> None:
        assert geoboundaries.INDIA_LEVELS["ADM2"] == "district"
        assert geoboundaries.INDIA_LEVELS["ADM3"] == "subdistrict"

    def test_village_is_not_among_them(self) -> None:
        """The point of migration 0002: no open level reaches a village.

        If this ever fails because geoBoundaries added one, the seeder should
        start using it and `boundary_level` should say `village`.
        """
        assert "village" not in geoboundaries.INDIA_LEVELS.values()


LGD_HEADER = "\t".join(
    [
        "State Name",
        "State Code",
        "District Name",
        "District Census 2011 Code",
        "Subdistrict Name",
        "Subdistrict Census 2011 Code",
        "Village Name",
        "Village Census 2011 Code",
        "Village Census 2001 Code",
        "Gram Panchayat LGD Code",
        "Gram Panchayat Name",
    ]
)


def crosswalk(*rows: str) -> io.StringIO:
    return io.StringIO("\n".join([LGD_HEADER, *rows]) + "\n")


def lgd_row(
    state: str = "Chhattisgarh",
    state_code: str = "22",
    district: str = "Durg",
    district_code: str = "409",
    subdistrict: str = "Durg",
    subdistrict_code: str = "3317",
    village: str = "Kutelabhatha",
    census_2011: str = "442569",
    census_2001: str = "1146000",
    gp_lgd: str = "124575",
    gp_name: str = "Kutelabhata",
) -> str:
    return "\t".join(
        f'"{v}"'
        for v in (
            state,
            state_code,
            district,
            district_code,
            subdistrict,
            subdistrict_code,
            village,
            census_2011,
            census_2001,
            gp_lgd,
            gp_name,
        )
    )


class TestTheLgdCrosswalk:
    def test_it_reads_a_village_to_panchayat_row(self) -> None:
        from app.providers.vector import lgd_codes

        (link,) = lgd_codes.iter_links(crosswalk(lgd_row()))
        assert link.village_census_2011 == "442569"
        assert link.census_2001 == "1146000"
        assert link.gp_lgd_code == "124575"
        assert link.gp_name == "Kutelabhata"

    def test_the_census_codes_line_up_with_the_shrid(self) -> None:
        """The join key. SHRID `11-22-409-03317-442569` decomposes to these.

        Verified against the live data: state 22, district 409, sub-district
        3317, village 442569. If the two code spaces ever diverge, every
        Panchayat link would attach to the wrong village.
        """
        from app.providers.vector import lgd_codes

        (link,) = lgd_codes.iter_links(crosswalk(lgd_row()))
        shrid = "11-22-409-03317-442569"
        _, state, district, subdistrict, village = shrid.split("-")
        assert link.state_code == state
        assert link.district_code == district
        assert int(link.subdistrict_code) == int(subdistrict)
        assert link.village_census_2011 == village

    def test_the_panchayat_name_is_folded_for_search(self) -> None:
        """`Kutelabhata` and `Kutelabhatha` must land on one form."""
        from app.core.names import normalise_name
        from app.providers.vector import lgd_codes

        (link,) = lgd_codes.iter_links(crosswalk(lgd_row()))
        assert link.gp_name_normalised == normalise_name("Kutelabhatha")

    def test_rows_with_a_zero_census_code_are_skipped(self) -> None:
        """23,366 rows carry '0' rather than a code.

        There is nothing to join them on, and a zero would collide with whatever
        else carries it.
        """
        from app.providers.vector import lgd_codes

        assert list(lgd_codes.iter_links(crosswalk(lgd_row(census_2011="0")))) == []

    def test_rows_with_no_panchayat_are_skipped(self) -> None:
        """6.6 % of rows have none. The village still exists and stays searchable."""
        from app.providers.vector import lgd_codes

        assert list(lgd_codes.iter_links(crosswalk(lgd_row(gp_lgd="")))) == []
        assert list(lgd_codes.iter_links(crosswalk(lgd_row(gp_name="")))) == []

    def test_it_narrows_by_state_leniently(self) -> None:
        from app.providers.vector import lgd_codes

        rows = crosswalk(
            lgd_row(),
            lgd_row(state="Maharashtra", state_code="27", census_2011="556001"),
        )
        links = list(lgd_codes.iter_links(rows, state="CHHATTISGARH"))
        assert [link.village_census_2011 for link in links] == ["442569"]

    def test_one_village_may_have_several_panchayats(self) -> None:
        """12,045 Indian villages do. Bambooflat is in Bambooflat-I and -II.

        The reader must not collapse them: a single link per village would be
        wrong for 2 % of the country.
        """
        from app.providers.vector import lgd_codes

        rows = crosswalk(
            lgd_row(
                village="Bambooflat", census_2011="645516", gp_lgd="234478", gp_name="Bambooflat-I"
            ),
            lgd_row(
                village="Bambooflat", census_2011="645516", gp_lgd="272941", gp_name="Bambooflat-Ii"
            ),
        )
        links = list(lgd_codes.iter_links(rows))
        assert len(links) == 2
        assert {link.gp_lgd_code for link in links} == {"234478", "272941"}

    def test_a_missing_column_is_named(self) -> None:
        from app.providers.vector import lgd_codes

        handle = io.StringIO('State Name\tVillage Name\n"x"\t"y"\n')
        with pytest.raises(lgd_codes.LgdCodesError, match="missing expected columns"):
            list(lgd_codes.iter_links(handle))

    def test_the_file_id_is_pinned(self) -> None:
        """A Dataverse file id change would silently fetch the wrong table."""
        from app.providers.vector import lgd_codes

        assert lgd_codes.DATAVERSE_FILE_ID == 11058283
        assert str(lgd_codes.DATAVERSE_FILE_ID) in lgd_codes.DOWNLOAD_URL
