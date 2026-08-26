"""Synthetic contour-KML generators for the generalisation tests (MC-13, MC-14).

The point of these is that the *answer is known analytically*. A test that only
runs the supplied sample proves the code works on that file; a test on a
generated inverted cone proves the code derives its answer from whatever arrives.

`build_kml` can emit the same terrain with the elevation placed in any of the
four locations the parser supports, which is what makes the strategy-matrix test
in MC-14 possible.
"""

from __future__ import annotations

import io
import math
import zipfile

Coord = tuple[float, float]
Line = tuple[float, list[Coord]]


def concentric_rings(
    center: Coord = (77.0, 21.0),
    levels: tuple[float, ...] = (100.0, 101.0, 102.0, 103.0, 104.0),
    step_deg: float = 0.002,
    vertices: int = 48,
) -> list[Line]:
    """Inverted cone (a bowl): rings of increasing radius and elevation.

    Every point drains toward the centre, so the catchment of the centre cell is
    the entire surface -- an analytic expectation independent of any input file.
    """
    out: list[Line] = []
    for i, elev in enumerate(levels, start=1):
        r = step_deg * i
        ring = [
            (
                center[0] + r * math.cos(2 * math.pi * k / vertices),
                center[1] + r * math.sin(2 * math.pi * k / vertices),
            )
            for k in range(vertices + 1)  # closed ring
        ]
        out.append((elev, ring))
    return out


def tilted_plane(
    origin: Coord = (77.0, 21.0),
    levels: tuple[float, ...] = (10.0, 20.0, 30.0, 40.0),
    span_deg: float = 0.01,
    step_deg: float = 0.002,
    vertices: int = 12,
) -> list[Line]:
    """Uniform slope: parallel straight contours, elevation rising with y."""
    out: list[Line] = []
    for i, elev in enumerate(levels):
        y = origin[1] + step_deg * i
        out.append((elev, [(origin[0] + span_deg * k / vertices, y) for k in range(vertices + 1)]))
    return out


def twin_basins(
    left: Coord = (77.00, 21.0),
    right: Coord = (77.02, 21.0),
    levels: tuple[float, ...] = (50.0, 51.0, 52.0),
    step_deg: float = 0.002,
) -> list[Line]:
    """Two separate bowls. A catchment delineated in one must not leak into the
    other, which is how the drainage divide gets tested."""
    return concentric_rings(left, levels, step_deg) + concentric_rings(right, levels, step_deg)


def _coord_text(coords: list[Coord], z: float | None) -> str:
    if z is None:
        return " ".join(f"{lon:.8f},{lat:.8f}" for lon, lat in coords)
    return " ".join(f"{lon:.8f},{lat:.8f},{z:g}" for lon, lat in coords)


def build_kml(
    lines: list[Line],
    strategy: str = "placemark_name",
    *,
    namespace: str = "http://www.opengis.net/kml/2.2",
    field_name: str = "ELEV",
    with_label_points: bool = False,
    with_boundary: bool = False,
) -> bytes:
    """Emit a KML carrying `lines`, with the elevation placed per `strategy`.

    strategy:
      coordinate_z   -- as the third ordinate of every coordinate
      extended_data  -- as <SimpleData name={field_name}>
      placemark_name -- as <Placemark><name>
      folder_name    -- one <Folder> per level, named with the elevation
    """
    if strategy not in {"coordinate_z", "extended_data", "placemark_name", "folder_name"}:
        raise ValueError(f"unknown strategy {strategy!r}")

    def placemark(elev: float, coords: list[Coord]) -> str:
        name = f"<name>{elev:g}</name>" if strategy == "placemark_name" else ""
        ext = (
            f'<ExtendedData><SchemaData schemaUrl="#c">'
            f'<SimpleData name="{field_name}">{elev:g}</SimpleData>'
            f"</SchemaData></ExtendedData>"
            if strategy == "extended_data"
            else ""
        )
        z = elev if strategy == "coordinate_z" else None
        return (
            f"<Placemark>{name}{ext}<LineString>"
            f"<coordinates>{_coord_text(coords, z)}</coordinates>"
            f"</LineString></Placemark>"
        )

    body: list[str] = []
    if strategy == "folder_name":
        by_level: dict[float, list[list[Coord]]] = {}
        for elev, coords in lines:
            by_level.setdefault(elev, []).append(coords)
        for elev, groups in by_level.items():
            inner = "".join(placemark(elev, c) for c in groups)
            body.append(f"<Folder><name>{elev:g}</name>{inner}</Folder>")
    else:
        body.append("".join(placemark(e, c) for e, c in lines))

    if with_label_points:
        # Mirrors the supplied sample, which carries one label Point per line.
        # These must be ignored by the parser, not counted as contours.
        body.append(
            "".join(
                f"<Placemark><name>{e:g}</name><Point><coordinates>"
                f"{c[0][0]:.8f},{c[0][1]:.8f}</coordinates></Point></Placemark>"
                for e, c in lines
            )
        )

    if with_boundary:
        xs = [p[0] for _, cs in lines for p in cs]
        ys = [p[1] for _, cs in lines for p in cs]
        ring = [
            (min(xs), min(ys)),
            (max(xs), min(ys)),
            (max(xs), max(ys)),
            (min(xs), max(ys)),
            (min(xs), min(ys)),
        ]
        body.append(
            "<Placemark><Polygon><outerBoundaryIs><LinearRing><coordinates>"
            f"{_coord_text(list(ring), 0.0)}"
            "</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>"
        )

    return (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<kml xmlns="{namespace}"><Document><name>synthetic</name>'
        f'{"".join(body)}</Document></kml>'
    ).encode()


def build_kmz(
    lines: list[Line], strategy: str = "placemark_name", *, inner: str = "doc.kml"
) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(inner, build_kml(lines, strategy))
    return buf.getvalue()
