"""Cadastral layer ingest (FR-11).

This is the one endpoint that parses a file a stranger chose, so most of what
follows is about refusing things. The other half is the datum: an Indian
cadastral sheet on Everest 1830 read as WGS 84 lands ~190 m from the truth while
looking entirely correct, and PROJ's *default* transformation for that pair moves
nothing at all.
"""

from __future__ import annotations

import io
import json
import math
import zipfile
from typing import Any

import pytest

fiona = pytest.importorskip("fiona", reason="cadastral ingest needs fiona")

from app.services import cadastral  # noqa: E402

RING = [
    [
        (81.290, 21.250),
        (81.292, 21.250),
        (81.292, 21.252),
        (81.290, 21.252),
        (81.290, 21.250),
    ]
]


def geojson(*, crs: bool = True, ownership: str | None = "Gram Panchayat gairan") -> bytes:
    properties: dict[str, Any] = {"khasra": "112/2"}
    if ownership is not None:
        properties["ownership"] = ownership
    body: dict[str, Any] = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": properties,
                "geometry": {"type": "Polygon", "coordinates": RING},
            }
        ],
    }
    if crs:
        body["crs"] = {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}}
    return json.dumps(body).encode()


def shapefile_zip(epsg: int = 4326, *, include: set[str] | None = None) -> bytes:
    """A real zipped shapefile, optionally missing sidecars."""
    import pathlib
    import tempfile

    from fiona.crs import CRS

    schema = {"geometry": "Polygon", "properties": {"ownership": "str"}}
    with tempfile.TemporaryDirectory() as directory:
        path = pathlib.Path(directory) / "parcels.shp"
        with fiona.open(
            path, "w", driver="ESRI Shapefile", schema=schema, crs=CRS.from_epsg(epsg)
        ) as sink:
            sink.write(
                {
                    "geometry": {"type": "Polygon", "coordinates": RING},
                    "properties": {"ownership": "Gram Panchayat gairan"},
                }
            )
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for entry in pathlib.Path(directory).iterdir():
                if include is not None and entry.suffix.lower() not in include:
                    continue
                archive.write(entry, entry.name)
        return buffer.getvalue()


class TestItRefusesHostileArchives:
    def test_path_traversal(self) -> None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("../../etc/passwd.shp", b"x")
        with pytest.raises(cadastral.CadastralError, match="unsafe path"):
            cadastral.load(buffer.getvalue(), "evil.zip")

    def test_an_absolute_path(self) -> None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("/tmp/pwned.shp", b"x")
        with pytest.raises(cadastral.CadastralError, match="unsafe path"):
            cadastral.load(buffer.getvalue(), "evil.zip")

    def test_too_many_entries(self) -> None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for i in range(cadastral.MAX_ENTRIES + 5):
                archive.writestr(f"f{i}.shp", b"x")
        with pytest.raises(cadastral.CadastralError, match="entries"):
            cadastral.load(buffer.getvalue(), "many.zip")

    def test_a_zip_bomb(self) -> None:
        """A couple of hundred kilobytes that would expand past the cap."""
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("big.shp", b"\0" * (cadastral.MAX_EXTRACTED_BYTES + 1024))
        payload = buffer.getvalue()
        assert len(payload) < 1_000_000, "the fixture should be small to be a bomb"
        with pytest.raises(cadastral.CadastralError, match="MB"):
            cadastral.load(payload, "bomb.zip")

    def test_an_archive_with_no_shapefile(self) -> None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("readme.txt", b"hello")
        with pytest.raises(cadastral.CadastralError, match="no .shp"):
            cadastral.load(buffer.getvalue(), "z.zip")

    def test_a_shapefile_missing_its_sidecars(self) -> None:
        payload = shapefile_zip(include={".shp", ".prj"})
        with pytest.raises(cadastral.CadastralError, match="missing"):
            cadastral.load(payload, "partial.zip")


class TestItRefusesUnusableInput:
    def test_an_empty_file(self) -> None:
        with pytest.raises(cadastral.CadastralError, match="empty"):
            cadastral.load(b"", "x.geojson")

    def test_an_oversize_file(self) -> None:
        with pytest.raises(cadastral.CadastralError, match="limit"):
            cadastral.load(b"x" * (cadastral.MAX_UPLOAD_BYTES + 1), "big.geojson")

    def test_something_that_is_not_a_layer(self) -> None:
        with pytest.raises(cadastral.CadastralError, match="could not read"):
            cadastral.load(b"this is not geojson at all", "notes.geojson")


class TestTheDatum:
    def test_a_shapefile_without_a_prj_is_refused(self) -> None:
        """A shapefile has no default CRS, and this is where the Kalianpur trap
        lives: assuming WGS 84 displaces every parcel 100-400 m."""
        payload = shapefile_zip(include={".shp", ".shx", ".dbf"})
        with pytest.raises(cadastral.CadastralError, match="no .prj"):
            cadastral.load(payload, "no-prj.zip")

    def test_geojson_without_a_crs_is_accepted_as_wgs84(self) -> None:
        """RFC 7946 requires GeoJSON coordinates to be WGS 84, so this is the
        specified default rather than an assumption — unlike a shapefile."""
        layer = cadastral.load(geojson(crs=False), "p.geojson")
        assert layer.parcels

    def test_kalianpur_is_actually_shifted(self) -> None:
        """The heart of M10-4.

        `Transformer.from_crs(4145, 4326)` — the idiomatic call — selects PROJ's
        ballpark offset and moves the point by 0 m. Six published operations
        exist and all shift it 173-194 m. A silent no-op is the worst outcome
        available: plausible-looking parcels ~190 m from where they belong.
        """
        layer = cadastral.load(shapefile_zip(epsg=4145), "kalianpur.zip")
        assert layer.reprojected is True
        moved = layer.parcels[0].geometry["coordinates"][0][0]
        origin = RING[0][0]
        dx = (moved[0] - origin[0]) * 111_320 * math.cos(math.radians(origin[1]))
        dy = (moved[1] - origin[1]) * 111_320
        shift = math.hypot(dx, dy)
        assert 100.0 < shift < 400.0, f"datum shift was {shift:.1f} m -- a no-op is the bug"

    def test_the_operation_and_its_accuracy_are_reported(self) -> None:
        layer = cadastral.load(shapefile_zip(epsg=4145), "kalianpur.zip")
        block = layer.as_dict()
        assert block["datum_operation"]
        assert block["datum_accuracy_m"] is not None
        assert any("ballpark" in note.lower() for note in layer.notes)

    def test_wgs84_input_is_not_needlessly_transformed(self) -> None:
        layer = cadastral.load(shapefile_zip(epsg=4326), "wgs.zip")
        assert layer.reprojected is False
        assert layer.datum_operation is None


class TestOwnership:
    def test_public_tenure_is_recognised(self) -> None:
        layer = cadastral.load(geojson(ownership="Gram Panchayat gairan"), "p.geojson")
        assert layer.parcels[0].is_public is True
        assert layer.ownership_field == "ownership"

    @pytest.mark.parametrize(
        "value",
        ["Government waste land", "GOVT. PORAMBOKE", "shamlat deh", "Revenue Department"],
    )
    def test_real_world_spellings(self, value: str) -> None:
        layer = cadastral.load(geojson(ownership=value), "p.geojson")
        assert layer.parcels[0].is_public is True, value

    def test_private_land_is_not_flagged_allottable(self) -> None:
        layer = cadastral.load(geojson(ownership="Private - Ramesh Kumar"), "p.geojson")
        assert layer.parcels[0].is_public is False

    def test_a_layer_with_no_ownership_field_says_so(self) -> None:
        layer = cadastral.load(geojson(ownership=None), "p.geojson")
        assert layer.ownership_field is None
        assert any("No ownership attribute" in note for note in layer.notes)
        assert layer.parcels[0].is_public is False

    def test_area_is_measured_on_the_ellipsoid(self) -> None:
        """Not from degrees: a square degree is not a constant area."""
        layer = cadastral.load(geojson(), "p.geojson")
        # ~0.002 x 0.002 degrees at 21 N is roughly 4.6 ha.
        assert 3.0 < layer.parcels[0].area_ha < 7.0

    def test_the_original_attributes_survive(self) -> None:
        layer = cadastral.load(geojson(), "p.geojson")
        assert layer.parcels[0].as_feature()["properties"]["khasra"] == "112/2"
