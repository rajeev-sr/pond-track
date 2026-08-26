"""Contour KML/KMZ parser (MC-2..MC-6) and the generalisation guarantees.

The tests that matter most here are `TestStrategyMatrix` and `TestDerivation`:
together they assert that the parser reads the *input* rather than relying on any
convention of one particular file (MC-13, MC-14).
"""

from __future__ import annotations

import io
import zipfile

import pytest

from app.providers.elevation.contour_kml import (
    ADVISORY_MIN_LINES,
    MAX_UPLOAD_BYTES,
    ContourParseError,
    parse_contour_file,
)
from app.tests.synthetic_kml import (
    build_kml,
    build_kmz,
    concentric_rings,
    tilted_plane,
    twin_basins,
)

ALL_STRATEGIES = ("coordinate_z", "extended_data", "placemark_name", "folder_name")


class TestStrategyMatrix:
    """★ The same terrain, expressed four different ways, must parse identically.

    This is the core anti-hard-coding test. The supplied sample happens to store
    elevation in <Placemark><name> with 2-D coordinates; another export will use
    the z ordinate or ExtendedData. If the parser favoured one convention this
    test would fail, and the "generalise to other contour maps" requirement would
    be unmet.
    """

    @pytest.mark.parametrize("strategy", ALL_STRATEGIES)
    def test_strategy_is_detected_and_reported(self, strategy: str) -> None:
        r = parse_contour_file(build_kml(concentric_rings(), strategy))
        assert r.elevation_strategy == strategy

    def test_all_strategies_agree_on_everything_derived(self) -> None:
        results = {s: parse_contour_file(build_kml(concentric_rings(), s)) for s in ALL_STRATEGIES}
        ref = results["placemark_name"]
        for strategy, r in results.items():
            assert r.levels == ref.levels, strategy
            assert r.interval_m == ref.interval_m, strategy
            assert r.lines_parsed == ref.lines_parsed, strategy
            assert r.vertex_count == ref.vertex_count, strategy
            assert r.utm_epsg == ref.utm_epsg, strategy
            assert r.bounds.as_tuple() == pytest.approx(ref.bounds.as_tuple()), strategy
            assert r.relief_m == ref.relief_m, strategy

    def test_geometry_is_bit_identical_across_strategies(self) -> None:
        sets = [
            {
                (ln.elevation_m, ln.coords)
                for ln in parse_contour_file(build_kml(concentric_rings(), s)).lines
            }
            for s in ALL_STRATEGIES
        ]
        assert all(s == sets[0] for s in sets)

    def test_extended_data_field_name_is_not_hard_coded(self) -> None:
        # Different exporters name the field differently; all should resolve.
        for field in ("ELEV", "elevation", "Level", "CONTOUR", "height", "z"):
            r = parse_contour_file(build_kml(concentric_rings(), "extended_data", field_name=field))
            assert r.elevation_strategy == "extended_data", field
            assert len(r.levels) == 5, field

    def test_unknown_extended_field_falls_through_to_the_next_strategy(self) -> None:
        # A field we do not recognise must not silently become the elevation.
        kml = build_kml(concentric_rings(), "extended_data", field_name="SHAPE_LEN")
        with pytest.raises(ContourParseError, match="could not determine contour elevations"):
            parse_contour_file(kml)


class TestDerivation:
    """Interval, extent, CRS and levels come from the data, never from a default."""

    @pytest.mark.parametrize(
        ("levels", "expected_interval"),
        [
            ((100.0, 101.0, 102.0), 1.0),
            ((100.0, 100.5, 101.0, 101.5), 0.5),
            ((200.0, 205.0, 210.0), 5.0),
            ((0.0, 0.25, 0.5), 0.25),
        ],
    )
    def test_interval_is_derived(self, levels: tuple[float, ...], expected_interval: float) -> None:
        r = parse_contour_file(build_kml(concentric_rings(levels=levels)))
        assert r.interval_m == pytest.approx(expected_interval)

    def test_interval_is_the_mode_not_the_mean(self) -> None:
        # An irregular level set must report the *typical* spacing.
        r = parse_contour_file(build_kml(concentric_rings(levels=(10.0, 11.0, 12.0, 20.0))))
        assert r.interval_m == pytest.approx(1.0)

    @pytest.mark.parametrize(
        ("lon", "expected_epsg"),
        [(69.0, 32642), (77.0, 32643), (81.3, 32644), (88.4, 32645), (91.7, 32646)],
    )
    def test_working_crs_follows_the_data_location(self, lon: float, expected_epsg: int) -> None:
        r = parse_contour_file(build_kml(concentric_rings(center=(lon, 21.0))))
        assert r.utm_epsg == expected_epsg

    def test_bounds_cover_every_vertex(self) -> None:
        lines = concentric_rings()
        r = parse_contour_file(build_kml(lines))
        xs = [p[0] for _, cs in lines for p in cs]
        ys = [p[1] for _, cs in lines for p in cs]
        assert r.bounds.min_lon == pytest.approx(min(xs))
        assert r.bounds.max_lon == pytest.approx(max(xs))
        assert r.bounds.min_lat == pytest.approx(min(ys))
        assert r.bounds.max_lat == pytest.approx(max(ys))

    def test_relief_and_range(self) -> None:
        r = parse_contour_file(build_kml(concentric_rings(levels=(50.0, 55.0, 60.0))))
        assert r.elevation_range_m == (50.0, 60.0)
        assert r.relief_m == 10.0

    def test_summary_is_json_serialisable_and_complete(self) -> None:
        import json

        s = parse_contour_file(build_kml(concentric_rings())).summary()
        json.dumps(s)
        for key in (
            "elevation_source",
            "elevation_strategy",
            "contour_interval_m",
            "levels",
            "relief_m",
            "bounds_4326",
            "centroid_4326",
            "working_crs_epsg",
            "vertices_used",
            "lines_parsed",
            "lines_unresolved",
            "warnings",
        ):
            assert key in s, key
        assert s["elevation_source"] == "uploaded_contour_map"


class TestGeometryVariants:
    def test_tilted_plane(self) -> None:
        r = parse_contour_file(build_kml(tilted_plane()))
        assert r.lines_parsed == 4
        assert r.interval_m == pytest.approx(10.0)

    def test_twin_basins_keeps_all_lines(self) -> None:
        r = parse_contour_file(build_kml(twin_basins()))
        assert r.lines_parsed == 6
        assert len(r.levels) == 3

    def test_label_points_are_ignored_not_counted(self) -> None:
        # The supplied sample carries one label Point per contour line.
        lines = concentric_rings()
        r = parse_contour_file(build_kml(lines, with_label_points=True))
        assert r.lines_parsed == len(lines)

    def test_boundary_polygon_is_captured(self) -> None:
        r = parse_contour_file(build_kml(concentric_rings(), with_boundary=True))
        assert r.boundary is not None
        assert len(r.boundary) >= 4

    def test_no_boundary_when_absent(self) -> None:
        assert parse_contour_file(build_kml(concentric_rings())).boundary is None

    @pytest.mark.parametrize(
        "ns",
        [
            "http://www.opengis.net/kml/2.2",
            "http://earth.google.com/kml/2.1",
            "http://earth.google.com/kml/2.0",
        ],
    )
    def test_namespace_agnostic(self, ns: str) -> None:
        r = parse_contour_file(build_kml(concentric_rings(), namespace=ns))
        assert r.lines_parsed == 5

    def test_multigeometry_lines_are_all_collected(self) -> None:
        kml = (
            b'<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
            b"<Placemark><name>10</name><MultiGeometry>"
            b"<LineString><coordinates>77.0,21.0 77.001,21.0</coordinates></LineString>"
            b"<LineString><coordinates>77.0,21.001 77.001,21.001</coordinates></LineString>"
            b"</MultiGeometry></Placemark>"
            b"<Placemark><name>20</name><LineString>"
            b"<coordinates>77.0,21.002 77.001,21.002</coordinates></LineString></Placemark>"
            b"</Document></kml>"
        )
        assert parse_contour_file(kml).lines_parsed == 3

    def test_whitespace_and_newlines_in_coordinates(self) -> None:
        kml = (
            b'<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
            b"<Placemark><name>10</name><LineString><coordinates>\n"
            b"  77.0,21.0\n  77.001,21.0\n  77.002,21.0\n"
            b"</coordinates></LineString></Placemark>"
            b"<Placemark><name>11</name><LineString><coordinates>\n"
            b"  77.0,21.001\n  77.001,21.001\n</coordinates></LineString></Placemark>"
            b"</Document></kml>"
        )
        r = parse_contour_file(kml)
        assert r.lines_parsed == 2
        assert r.vertex_count == 5

    @pytest.mark.parametrize(
        "name_text", ["277", "277.0", "277 m", "277.0m", "Contour 277.0", "elev=277"]
    )
    def test_lenient_number_parsing_in_names(self, name_text: str) -> None:
        kml = (
            '<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
            f"<Placemark><name>{name_text}</name><LineString>"
            "<coordinates>77.0,21.0 77.001,21.0</coordinates></LineString></Placemark>"
            "<Placemark><name>278</name><LineString>"
            "<coordinates>77.0,21.001 77.001,21.001</coordinates></LineString></Placemark>"
            "</Document></kml>"
        ).encode()
        assert 277.0 in parse_contour_file(kml).levels


class TestKmz:
    def test_kmz_is_unwrapped(self) -> None:
        r = parse_contour_file(build_kmz(concentric_rings()))
        assert r.lines_parsed == 5

    def test_kmz_with_a_non_standard_inner_name(self) -> None:
        r = parse_contour_file(build_kmz(concentric_rings(), inner="contours/lines.kml"))
        assert r.lines_parsed == 5

    def test_doc_kml_is_preferred_when_several_exist(self) -> None:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("zzz.kml", build_kml(concentric_rings(levels=(1.0, 2.0))))
            zf.writestr("doc.kml", build_kml(concentric_rings(levels=(10.0, 20.0, 30.0))))
        r = parse_contour_file(buf.getvalue())
        assert len(r.levels) == 3

    def test_kmz_without_any_kml_is_rejected(self) -> None:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("readme.txt", "nothing here")
        with pytest.raises(ContourParseError, match="no .kml file"):
            parse_contour_file(buf.getvalue())


class TestValidationMessages:
    """Failures must name the actual problem, not return a generic 400."""

    def test_empty_file(self) -> None:
        with pytest.raises(ContourParseError, match="empty"):
            parse_contour_file(b"")

    def test_not_xml(self) -> None:
        with pytest.raises(ContourParseError, match="not well-formed"):
            parse_contour_file(b"this is plainly not xml")

    def test_no_linestrings(self) -> None:
        kml = (
            b'<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
            b"<Placemark><name>1</name><Point><coordinates>77,21</coordinates></Point>"
            b"</Placemark></Document></kml>"
        )
        with pytest.raises(ContourParseError, match="no contour LineStrings"):
            parse_contour_file(kml)

    def test_single_elevation_has_no_relief(self) -> None:
        flat = [
            (100.0, [(77.0, 21.0), (77.001, 21.0)]),
            (100.0, [(77.0, 21.001), (77.001, 21.001)]),
        ]
        with pytest.raises(ContourParseError, match="could not determine|no relief"):
            parse_contour_file(build_kml(flat))

    def test_latitude_out_of_range_is_diagnosed(self) -> None:
        bad = [(10.0, [(77.0, 210.0), (77.001, 210.0)]), (20.0, [(77.0, 211.0), (77.001, 211.0)])]
        with pytest.raises(ContourParseError, match="outside \\[-90, 90\\]|transposed"):
            parse_contour_file(build_kml(bad))

    def test_projected_coordinates_are_diagnosed(self) -> None:
        # A file already in UTM metres, a common mistake.
        bad = [
            (10.0, [(500000.0, 2340000.0), (500100.0, 2340000.0)]),
            (20.0, [(500000.0, 2340100.0), (500100.0, 2340100.0)]),
        ]
        with pytest.raises(ContourParseError, match="outside|projected CRS"):
            parse_contour_file(build_kml(bad))

    def test_too_few_lines(self) -> None:
        one = [(10.0, [(77.0, 21.0), (77.001, 21.0)])]
        with pytest.raises(ContourParseError):
            parse_contour_file(build_kml(one))

    def test_sparse_input_warns_but_succeeds(self) -> None:
        r = parse_contour_file(build_kml(concentric_rings(levels=(1.0, 2.0, 3.0))))
        assert r.lines_parsed == 3
        assert any(str(ADVISORY_MIN_LINES) in w for w in r.warnings)

    def test_a_dense_file_produces_no_warnings(self) -> None:
        many = tuple(float(i) for i in range(1, ADVISORY_MIN_LINES + 5))
        r = parse_contour_file(build_kml(concentric_rings(levels=many, step_deg=0.0005)))
        assert r.warnings == []


class TestSecurity:
    """Uploads are untrusted input (MC-12, HLD 2.6)."""

    def test_oversize_upload_is_rejected_before_parsing(self) -> None:
        with pytest.raises(ContourParseError, match="limit is"):
            parse_contour_file(b"x" * (MAX_UPLOAD_BYTES + 1))

    def test_billion_laughs_is_blocked(self) -> None:
        bomb = (
            b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
            b'<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">'
            b'<!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">'
            b"]><kml><Document><name>&lol3;</name></Document></kml>"
        )
        # defusedxml refuses entity declarations outright; the parser must surface
        # that as a clean ContourParseError rather than exhausting memory.
        with pytest.raises(ContourParseError):
            parse_contour_file(bomb)

    def test_external_entity_is_blocked(self) -> None:
        xxe = (
            b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY e SYSTEM "file:///etc/passwd">]>'
            b"<kml><Document><name>&e;</name></Document></kml>"
        )
        with pytest.raises(ContourParseError):
            parse_contour_file(xxe)

    def test_zip_bomb_is_rejected_on_declared_size(self) -> None:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("doc.kml", b"\0" * (300 * 1024 * 1024))
        with pytest.raises(ContourParseError, match="zip bomb|over the"):
            parse_contour_file(buf.getvalue())

    def test_corrupt_zip_is_diagnosed(self) -> None:
        with pytest.raises(ContourParseError, match="KMZ archive but could not be opened"):
            parse_contour_file(b"PK\x03\x04" + b"garbage" * 20)


class TestFilenameDiagnostics:
    """`filename` exists to make container mismatches diagnosable, nothing more.

    Content sniffing decides the container -- files get renamed all the time --
    but a .kmz that is not a zip is worth naming explicitly.
    """

    def test_kmz_extension_on_plain_kml_is_diagnosed(self) -> None:
        with pytest.raises(ContourParseError, match="named .kmz but its contents"):
            parse_contour_file(build_kml(concentric_rings()), "survey.kmz")

    def test_kml_extension_on_a_real_kmz_still_works(self) -> None:
        # Renamed file: content wins over extension.
        r = parse_contour_file(build_kmz(concentric_rings()), "survey.kml")
        assert r.lines_parsed == 5

    def test_no_filename_supplied_is_fine(self) -> None:
        assert parse_contour_file(build_kml(concentric_rings())).lines_parsed == 5

    def test_unknown_extension_is_accepted_on_content(self) -> None:
        assert parse_contour_file(build_kml(concentric_rings()), "data.xml").lines_parsed == 5
