"""Contour map (KML/KMZ) parser -- MC-2 .. MC-6, HLD 6.10.1.

KML is a container, not a schema. The one thing that must never be assumed is
*where the elevation lives*: real contour exports put it in the coordinate z
ordinate, in ExtendedData, in the Placemark name, or only in the enclosing
folder name. This parser tries each in priority order, picks the strategy that
resolves the most lines, and **reports which one it used** -- so a caller can see
that the result was derived from their file rather than assumed.

Nothing here is specific to any particular contour map: interval, extent, levels
and the working UTM zone are all derived from the input.
"""

from __future__ import annotations

import io
import itertools
import math
import re
import zipfile
from dataclasses import dataclass, field
from typing import Any

from defusedxml import ElementTree as DefusedET

from app.core.crs import utm_epsg_for
from app.providers.elevation.base import Bounds, ElevationStrategy

# ── limits (MC-12) ───────────────────────────────────────────────────────────
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 250 * 1024 * 1024  # zip-bomb guard for KMZ
MAX_ZIP_MEMBERS = 100

#: Minimum viable input. Two distinct levels is the mathematical floor: with one
#: level there is no gradient to interpolate.
MIN_LEVELS = 2
MIN_LINES = 2
#: Below this we still proceed but warn -- interpolation gets thin.
ADVISORY_MIN_LINES = 20
#: Above this fraction of unresolved placemarks the file is rejected rather than
#: silently analysed on partial data.
MAX_UNRESOLVED_FRACTION = 0.10

#: ExtendedData field names that carry an elevation, matched case-insensitively.
ELEVATION_FIELD_CANDIDATES = (
    "elev",
    "elevation",
    "level",
    "contour",
    "contour_value",
    "height",
    "alt",
    "altitude",
    "z",
    "value",
    "isovalue",
)

#: Leniently pull a signed number out of "277", "277.0 m", "Contour 277.0", "-12.5m".
_NUMBER = re.compile(r"[-+]?\d+(?:\.\d+)?")


class ContourParseError(ValueError):
    """Raised with a specific, actionable reason -- never a generic failure."""


@dataclass(frozen=True)
class ContourLine:
    """One contour: a polyline at a single elevation, lon/lat in EPSG:4326."""

    elevation_m: float
    coords: tuple[tuple[float, float], ...]

    @property
    def vertex_count(self) -> int:
        return len(self.coords)


@dataclass
class ParsedContours:
    """Everything derived from a contour file. No value here is hard-coded."""

    lines: list[ContourLine]
    elevation_strategy: ElevationStrategy
    bounds: Bounds
    utm_epsg: int
    levels: tuple[float, ...]
    interval_m: float | None
    vertex_count: int
    lines_parsed: int
    lines_unresolved: int
    boundary: tuple[tuple[float, float], ...] | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def elevation_range_m(self) -> tuple[float, float]:
        return (self.levels[0], self.levels[-1])

    @property
    def relief_m(self) -> float:
        return self.levels[-1] - self.levels[0]

    def summary(self) -> dict[str, Any]:
        """Provenance block for the API response (HLD 6.10.3)."""
        lo, hi = self.elevation_range_m
        return {
            "elevation_source": "uploaded_contour_map",
            "elevation_strategy": self.elevation_strategy,
            "lines_parsed": self.lines_parsed,
            "lines_unresolved": self.lines_unresolved,
            "vertices_used": self.vertex_count,
            "levels": len(self.levels),
            "contour_interval_m": self.interval_m,
            "elevation_min_m": lo,
            "elevation_max_m": hi,
            "relief_m": round(self.relief_m, 3),
            "bounds_4326": list(self.bounds.as_tuple()),
            "centroid_4326": list(self.bounds.centroid),
            "working_crs_epsg": self.utm_epsg,
            "has_boundary_polygon": self.boundary is not None,
            "warnings": list(self.warnings),
        }


# ── helpers ──────────────────────────────────────────────────────────────────
def _strip_ns(tag: str) -> str:
    """`{http://www.opengis.net/kml/2.2}Placemark` -> `Placemark`."""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _first_number(text: str | None) -> float | None:
    if not text:
        return None
    m = _NUMBER.search(text)
    if not m:
        return None
    try:
        v = float(m.group())
    except ValueError:
        return None
    return v if math.isfinite(v) else None


def _parse_coord_text(text: str | None) -> tuple[list[tuple[float, float]], list[float | None]]:
    """KML `<coordinates>` -> (lon/lat pairs, per-vertex z or None).

    KML permits whitespace *and* newlines between tuples, and tuples are
    `lon,lat[,alt]`. Malformed tuples are skipped rather than aborting the file.
    """
    if not text:
        return [], []
    xy: list[tuple[float, float]] = []
    zs: list[float | None] = []
    for token in text.split():
        parts = token.split(",")
        if len(parts) < 2:
            continue
        try:
            lon, lat = float(parts[0]), float(parts[1])
        except ValueError:
            continue
        if not (math.isfinite(lon) and math.isfinite(lat)):
            continue
        xy.append((lon, lat))
        if len(parts) >= 3:
            try:
                z = float(parts[2])
                zs.append(z if math.isfinite(z) else None)
            except ValueError:
                zs.append(None)
        else:
            zs.append(None)
    return xy, zs


def _read_kml_bytes(data: bytes, filename: str | None = None) -> bytes:
    """Return raw KML, transparently unwrapping a KMZ archive (MC-4, MC-12).

    Container type is decided by the zip magic number, not the extension --
    files get renamed. `filename` is used only to make a mismatch diagnosable,
    which is otherwise a confusing upload failure.
    """
    if len(data) > MAX_UPLOAD_BYTES:
        raise ContourParseError(
            f"file is {len(data) / 1e6:.1f} MB; the limit is " f"{MAX_UPLOAD_BYTES / 1e6:.0f} MB"
        )
    if not data:
        raise ContourParseError("uploaded file is empty")

    is_zip = data[:2] == b"PK"
    ext = (filename or "").lower().rsplit(".", 1)[-1] if filename else ""

    if not is_zip:
        if ext == "kmz":
            raise ContourParseError(
                f"{filename!r} is named .kmz but its contents are not a zip archive. "
                "If it is plain KML, rename it to .kml; if it is a KMZ, it is corrupt."
            )
        return data

    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ContourParseError("file looks like a KMZ archive but could not be opened") from exc

    with zf:
        members = zf.infolist()
        if len(members) > MAX_ZIP_MEMBERS:
            raise ContourParseError(
                f"KMZ contains {len(members)} entries; the limit is {MAX_ZIP_MEMBERS}"
            )
        total = sum(m.file_size for m in members)
        if total > MAX_UNCOMPRESSED_BYTES:
            raise ContourParseError(
                f"KMZ expands to {total / 1e6:.0f} MB, over the "
                f"{MAX_UNCOMPRESSED_BYTES / 1e6:.0f} MB limit (possible zip bomb)"
            )
        # Prefer doc.kml (the OGC convention); otherwise the first .kml member.
        names = [m.filename for m in members if m.filename.lower().endswith(".kml")]
        if not names:
            raise ContourParseError("KMZ archive contains no .kml file")
        chosen = next((n for n in names if n.lower().endswith("doc.kml")), names[0])
        return zf.read(chosen)


@dataclass
class _RawPlacemark:
    """A LineString plus every candidate elevation we could find for it."""

    coords: tuple[tuple[float, float], ...]
    z_values: tuple[float | None, ...]
    name_text: str | None
    extended: dict[str, str]
    folder_path: tuple[str, ...]


def _collect(root: Any) -> tuple[list[_RawPlacemark], tuple[tuple[float, float], ...] | None]:
    """Walk the KML tree, gathering LineStrings with their elevation candidates.

    Namespace-agnostic (kml/2.2, kml/2.1 and gx: all parse). `<Point>` label
    placemarks and `<Style>` are ignored -- the sample file carries 1355 label
    Points that merely duplicate the line elevations.
    """
    placemarks: list[_RawPlacemark] = []
    boundary: tuple[tuple[float, float], ...] | None = None

    def child_text(el: Any, tag: str) -> str | None:
        for c in el:
            if _strip_ns(c.tag) == tag:
                return (c.text or "").strip() or None
        return None

    def walk(el: Any, folders: tuple[str, ...]) -> None:
        nonlocal boundary
        tag = _strip_ns(el.tag)

        if tag in ("Folder", "Document"):
            nm = child_text(el, "name")
            folders = (*folders, nm) if nm else folders

        if tag == "Placemark":
            name_text = child_text(el, "name")
            extended: dict[str, str] = {}
            for sd in el.iter():
                st = _strip_ns(sd.tag)
                if st in ("SimpleData", "Data"):
                    key = sd.get("name")
                    if st == "SimpleData":
                        val = (sd.text or "").strip()
                    else:  # <Data name="x"><value>1</value></Data>
                        val = ""
                        for vc in sd:
                            if _strip_ns(vc.tag) == "value":
                                val = (vc.text or "").strip()
                    if key and val:
                        extended[key] = val

            # A Placemark may hold several LineStrings via MultiGeometry.
            for geom in el.iter():
                gt = _strip_ns(geom.tag)
                if gt == "LineString":
                    txt = None
                    for c in geom:
                        if _strip_ns(c.tag) == "coordinates":
                            txt = c.text
                    xy, zs = _parse_coord_text(txt)
                    if len(xy) >= 2:
                        placemarks.append(
                            _RawPlacemark(tuple(xy), tuple(zs), name_text, extended, folders)
                        )
                elif gt == "Polygon" and boundary is None:
                    for c in geom.iter():
                        if _strip_ns(c.tag) == "coordinates":
                            pxy, _ = _parse_coord_text(c.text)
                            if len(pxy) >= 4:
                                boundary = tuple(pxy)
                            break
            return  # do not descend further into a Placemark

        for child in el:
            walk(child, folders)

    walk(root, ())
    return placemarks, boundary


# ── elevation resolution strategies (HLD 6.10.1, MC-3) ───────────────────────
def _from_z(pm: _RawPlacemark) -> float | None:
    zs = [z for z in pm.z_values if z is not None]
    if len(zs) < max(1, len(pm.z_values) // 2):
        return None
    # A contour is a line of *constant* elevation: a varying z means the z
    # ordinate is carrying something else (draped terrain, an offset), so it is
    # not a usable contour value.
    if max(zs) - min(zs) > 1e-6:
        return None
    return zs[0]


def _from_extended(pm: _RawPlacemark) -> float | None:
    for key, val in pm.extended.items():
        if key.strip().lower() in ELEVATION_FIELD_CANDIDATES:
            v = _first_number(val)
            if v is not None:
                return v
    return None


def _from_name(pm: _RawPlacemark) -> float | None:
    return _first_number(pm.name_text)


def _from_folder(pm: _RawPlacemark) -> float | None:
    for nm in reversed(pm.folder_path):
        v = _first_number(nm)
        if v is not None:
            return v
    return None


_STRATEGIES: tuple[tuple[ElevationStrategy, Any], ...] = (
    ("coordinate_z", _from_z),
    ("extended_data", _from_extended),
    ("placemark_name", _from_name),
    ("folder_name", _from_folder),
)


def _choose_strategy(
    placemarks: list[_RawPlacemark],
) -> tuple[ElevationStrategy, list[float | None]]:
    """Pick the strategy that resolves the most lines to >= 2 distinct levels.

    Ties break toward the earlier (more authoritative) strategy. Requiring two
    distinct levels matters: a folder name like "contours_1.0m" resolves *every*
    line to 1.0, which is a uniform -- and useless -- surface. Demanding relief
    rejects that automatically instead of needing a special case.
    """
    best: tuple[int, int, ElevationStrategy, list[float | None]] | None = None
    for idx, (name, fn) in enumerate(_STRATEGIES):
        vals = [fn(pm) for pm in placemarks]
        resolved = [v for v in vals if v is not None]
        distinct = len(set(resolved))
        if distinct < MIN_LEVELS:
            continue
        score = (len(resolved), -idx)
        if best is None or score > (best[0], -best[1]):
            best = (len(resolved), idx, name, vals)
    if best is None:
        raise ContourParseError(
            "could not determine contour elevations. Tried, in order: the coordinate "
            "z ordinate, ExtendedData fields "
            f"({', '.join(ELEVATION_FIELD_CANDIDATES[:5])}...), the Placemark <name>, "
            "and the enclosing Folder <name>. None yielded at least "
            f"{MIN_LEVELS} distinct elevation values."
        )
    return best[2], best[3]


def _derive_interval(levels: tuple[float, ...]) -> float | None:
    """Modal difference between consecutive levels -- derived, never assumed."""
    if len(levels) < 2:
        return None
    diffs = [round(b - a, 6) for a, b in itertools.pairwise(levels) if b > a]
    if not diffs:
        return None
    counts: dict[float, int] = {}
    for d in diffs:
        counts[d] = counts.get(d, 0) + 1
    return max(counts.items(), key=lambda kv: (kv[1], -kv[0]))[0]


def parse_contour_file(data: bytes, filename: str | None = None) -> ParsedContours:
    """Parse a KML/KMZ contour map into geometry plus derived metadata.

    Raises `ContourParseError` with a specific reason on unusable input.
    """
    kml = _read_kml_bytes(data, filename)

    try:
        # defusedxml blocks entity expansion (billion laughs) and external
        # entity resolution -- required because this is untrusted upload.
        root = DefusedET.fromstring(kml)
    except Exception as exc:
        raise ContourParseError(f"file is not well-formed XML/KML: {exc}") from exc

    placemarks, boundary = _collect(root)
    if not placemarks:
        raise ContourParseError(
            "no contour LineStrings found. Expected <Placemark> elements containing "
            "<LineString><coordinates>; check that the file is a contour export "
            "rather than points or polygons."
        )

    strategy, values = _choose_strategy(placemarks)

    lines: list[ContourLine] = []
    unresolved = 0
    for pm, v in zip(placemarks, values, strict=True):
        if v is None:
            unresolved += 1
            continue
        lines.append(ContourLine(elevation_m=v, coords=pm.coords))

    total = len(placemarks)
    if unresolved and unresolved / total > MAX_UNRESOLVED_FRACTION:
        raise ContourParseError(
            f"{unresolved} of {total} contour lines ({unresolved / total:.0%}) had no "
            f"resolvable elevation using strategy '{strategy}'; the limit is "
            f"{MAX_UNRESOLVED_FRACTION:.0%}. The file may mix conventions."
        )
    if len(lines) < MIN_LINES:
        raise ContourParseError(
            f"only {len(lines)} usable contour line(s); at least {MIN_LINES} are needed"
        )

    levels = tuple(sorted({ln.elevation_m for ln in lines}))
    if len(levels) < MIN_LEVELS:
        raise ContourParseError(
            f"all contours share one elevation ({levels[0]} m), so the surface has no "
            "relief and no catchment can be derived"
        )

    lons = [c[0] for ln in lines for c in ln.coords]
    lats = [c[1] for ln in lines for c in ln.coords]
    if not (min(lons) >= -180.0 and max(lons) <= 180.0):
        raise ContourParseError(
            f"longitudes span {min(lons):.4f}..{max(lons):.4f}, outside [-180, 180]; "
            "the file may be in a projected CRS rather than EPSG:4326"
        )
    if not (min(lats) >= -90.0 and max(lats) <= 90.0):
        raise ContourParseError(
            f"latitudes span {min(lats):.4f}..{max(lats):.4f}, outside [-90, 90]; "
            "the file may have lat/lon transposed"
        )

    bounds = Bounds(min(lons), min(lats), max(lons), max(lats))
    clon, clat = bounds.centroid

    warnings: list[str] = []
    if len(lines) < ADVISORY_MIN_LINES:
        warnings.append(
            f"only {len(lines)} contour lines; interpolation will be coarse "
            f"(>= {ADVISORY_MIN_LINES} recommended)"
        )
    if unresolved:
        warnings.append(f"{unresolved} placemark(s) had no resolvable elevation and were skipped")
    if bounds.width_deg == 0 or bounds.height_deg == 0:
        raise ContourParseError("contour extent is degenerate (zero width or height)")

    return ParsedContours(
        lines=lines,
        elevation_strategy=strategy,
        bounds=bounds,
        utm_epsg=utm_epsg_for(clon, clat),
        levels=levels,
        interval_m=_derive_interval(levels),
        vertex_count=sum(ln.vertex_count for ln in lines),
        lines_parsed=len(lines),
        lines_unresolved=unresolved,
        boundary=boundary,
        warnings=warnings,
    )
