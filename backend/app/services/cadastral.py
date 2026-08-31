"""Ingest a user-supplied cadastral layer (FR-11, M10-3, M10-4).

Land tenure is the single largest gap between "recommended" and "buildable": no
open dataset carries village-level ownership, so a site that is physically ideal
may be privately held. This lets someone who *has* that layer supply it.

Everything here is defensive, because this is the one endpoint that parses a file
a stranger chose. A shapefile arrives as a zip, and a zip is an instruction list:

* **Zip bombs.** A few hundred kilobytes can expand to gigabytes. Both the
  declared uncompressed size and the bytes actually written are capped, because
  the declared size is attacker-controlled and cannot be trusted on its own.
* **Path traversal.** An entry named `../../etc/passwd` — or an absolute path, or
  a symlink — escapes the extraction directory. Names are validated before
  anything is written, not after.
* **Entry count.** Ten thousand tiny files is a different denial of service from
  one enormous one.

And one correctness trap that is quieter than any of those: **the datum.** Indian
cadastral sheets are frequently on Everest 1830 / Kalianpur, and treating those
coordinates as WGS 84 shifts every parcel by roughly 100-400 m. That is far
enough to put a plot on the wrong side of a road while looking entirely
plausible, so a layer whose CRS cannot be determined is refused rather than
assumed.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.logging import get_logger

log = get_logger("services.cadastral")

#: The upload itself.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024

#: Total bytes written during extraction. A zip bomb's whole trick is that this
#: is unrelated to the archive's own size.
MAX_EXTRACTED_BYTES = 200 * 1024 * 1024

#: Ten thousand entries is not a cadastral layer, it is an attack or a mistake.
MAX_ENTRIES = 200

#: A shapefile is a set of sidecar files. Anything else in the archive is
#: ignored rather than extracted -- there is no reason to write a stranger's
#: .exe to disk even inside a temporary directory.
SHAPEFILE_SUFFIXES = frozenset({".shp", ".shx", ".dbf", ".prj", ".cpg", ".sbn", ".sbx"})

#: Attribute names that plausibly carry ownership, in the order they are tried.
#: Indian cadastral exports are not consistent, and guessing badly is better than
#: refusing every file that does not use one exact spelling -- as long as the
#: field actually used is reported back, which it is.
OWNERSHIP_FIELDS: tuple[str, ...] = (
    "ownership",
    "owner",
    "owner_type",
    "tenure",
    "land_type",
    "landtype",
    "category",
    "class",
    "khata_type",
    "type",
)

#: Values that mean "the village or the state holds this", which is what makes a
#: parcel allottable for a pond. Lower-cased and matched as substrings, because
#: real data carries things like "GOVT. WASTE LAND".
PUBLIC_TENURE_TOKENS: tuple[str, ...] = (
    "government",
    "govt",
    "gram panchayat",
    "panchayat",
    "gairan",
    "gochar",
    "shamlat",
    "poramboke",
    "waste",
    "common",
    "public",
    "revenue",
)


class CadastralError(ValueError):
    """The layer cannot be ingested, with a reason a person can act on."""


@dataclass(frozen=True)
class Parcel:
    parcel_id: str
    geometry: dict[str, Any]
    ownership: str | None
    is_public: bool
    area_ha: float
    attributes: dict[str, Any]

    def as_feature(self) -> dict[str, Any]:
        return {
            "type": "Feature",
            "id": self.parcel_id,
            "geometry": self.geometry,
            "properties": {
                "parcel_id": self.parcel_id,
                "ownership": self.ownership,
                "is_public": self.is_public,
                "area_ha": round(self.area_ha, 4),
                **self.attributes,
            },
        }


@dataclass(frozen=True)
class CadastralLayer:
    parcels: tuple[Parcel, ...]
    source_crs: str
    ownership_field: str | None
    reprojected: bool
    datum_operation: str | None
    datum_accuracy_m: float
    skipped: int
    notes: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        public = [p for p in self.parcels if p.is_public]
        return {
            "parcel_count": len(self.parcels),
            "public_parcel_count": len(public),
            "public_area_ha": round(sum(p.area_ha for p in public), 3),
            "total_area_ha": round(sum(p.area_ha for p in self.parcels), 3),
            "source_crs": self.source_crs,
            "reprojected_to_wgs84": self.reprojected,
            "datum_operation": self.datum_operation,
            "datum_accuracy_m": (
                None
                if self.datum_accuracy_m != self.datum_accuracy_m
                else round(self.datum_accuracy_m, 1)
            ),
            "ownership_field": self.ownership_field,
            "features_skipped": self.skipped,
            "notes": list(self.notes),
        }

    def feature_collection(self) -> dict[str, Any]:
        return {
            "type": "FeatureCollection",
            "features": [p.as_feature() for p in self.parcels],
        }


def _safe_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    """The entries worth extracting, having rejected everything dangerous."""
    members = archive.infolist()
    if len(members) > MAX_ENTRIES:
        raise CadastralError(
            f"the archive holds {len(members)} entries; the limit is {MAX_ENTRIES}"
        )

    declared = sum(m.file_size for m in members)
    if declared > MAX_EXTRACTED_BYTES:
        raise CadastralError(
            f"the archive declares {declared / 1e6:.0f} MB uncompressed; the limit "
            f"is {MAX_EXTRACTED_BYTES / 1e6:.0f} MB"
        )

    keep: list[zipfile.ZipInfo] = []
    for member in members:
        if member.is_dir():
            continue
        name = member.filename
        # Reject before writing, never after: by the time a traversal has been
        # written the damage is done.
        if name.startswith("/") or ".." in Path(name).parts:
            raise CadastralError(f"unsafe path in the archive: {name!r}")
        if (member.external_attr >> 16) & 0o170000 == 0o120000:
            raise CadastralError(f"the archive contains a symlink: {name!r}")
        if Path(name).suffix.lower() in SHAPEFILE_SUFFIXES:
            keep.append(member)

    suffixes = {Path(m.filename).suffix.lower() for m in keep}
    if ".shp" not in suffixes:
        raise CadastralError(
            "no .shp found in the archive. A zipped shapefile needs at least "
            ".shp, .shx and .dbf together, and .prj so the CRS is known."
        )
    missing = {".shx", ".dbf"} - suffixes
    if missing:
        raise CadastralError(
            f"the archive is missing {sorted(missing)}; a shapefile is a set of "
            "sidecar files and cannot be read without them."
        )
    if ".prj" not in suffixes:
        # Unlike GeoJSON, a shapefile has no default CRS. This is where the
        # Kalianpur trap actually lives: without a .prj the coordinates could be
        # anything, and reading Everest 1830 numbers as WGS 84 displaces every
        # parcel by roughly 100-400 m while looking entirely plausible.
        raise CadastralError(
            "the archive has no .prj, so the shapefile's coordinate reference "
            "system is unknown. It is refused rather than assumed to be WGS 84: "
            "Indian cadastral sheets are frequently on Everest 1830 / Kalianpur, "
            "and reading those coordinates as WGS 84 shifts every parcel by "
            "roughly 100-400 m -- far enough to put a plot on the wrong side of "
            "a road. Export the layer again with its .prj."
        )
    return keep


def _extract(data: bytes, destination: Path) -> Path:
    """Extract a zipped shapefile safely and return the path to the .shp."""
    import io as _io

    written = 0
    with zipfile.ZipFile(_io.BytesIO(data)) as archive:
        members = _safe_members(archive)
        for member in members:
            target = destination / Path(member.filename).name
            with archive.open(member) as source, target.open("wb") as sink:
                # Streamed and counted, because `file_size` is the archive's own
                # claim about itself and a bomb lies about it.
                while chunk := source.read(64 * 1024):
                    written += len(chunk)
                    if written > MAX_EXTRACTED_BYTES:
                        raise CadastralError(
                            "the archive expands beyond "
                            f"{MAX_EXTRACTED_BYTES / 1e6:.0f} MB; refusing to continue"
                        )
                    sink.write(chunk)
    shp = next(p for p in destination.iterdir() if p.suffix.lower() == ".shp")
    return shp


def _extent(features: list[Any], crs: Any) -> tuple[float, float, float, float]:
    """The layer's bounding box in lon/lat, to steer the operation choice.

    PROJ ranks candidate transformations by their area of use, so it needs to
    know roughly where the data is. Approximated from the source coordinates:
    good enough to pick the right operation for a district, which is all it is
    for.
    """
    from shapely.geometry import shape

    xs: list[float] = []
    ys: list[float] = []
    for feature in features[:200]:
        geometry = feature.get("geometry")
        if not geometry:
            continue
        try:
            minx, miny, maxx, maxy = shape(dict(geometry)).bounds
        except Exception:
            continue
        xs += [minx, maxx]
        ys += [miny, maxy]
    if not xs:
        # Nothing usable to bound; India's envelope is a safe default and only
        # affects which operation PROJ prefers, not the arithmetic it applies.
        return (68.0, 6.0, 98.0, 38.0)
    if crs.is_geographic:
        return (min(xs), min(ys), max(xs), max(ys))
    from pyproj import CRS, Transformer

    to_ll = Transformer.from_crs(crs, CRS.from_epsg(4326), always_xy=True)
    lons, lats = to_ll.transform([min(xs), max(xs)], [min(ys), max(ys)])
    return (min(lons), min(lats), max(lons), max(lats))


def _datum_transformer(
    source: Any, bounds: tuple[float, float, float, float]
) -> tuple[Any, str, float]:
    """A real datum transformation to WGS 84, or refuse.

    `Transformer.from_crs(src, 4326)` -- the obvious call -- is not safe here.
    Asked to convert Kalianpur 1962 to WGS 84 without an area of interest, PROJ
    selects its **Ballpark geographic offset**, which returns the coordinates
    unchanged. Measured at 81.29 E, 21.25 N: six published operations exist and
    all shift the point 173-194 m, while the default call shifts it by 0. A
    datum transform that silently does nothing is the worst outcome available --
    the parcels look plausible and sit ~190 m from where they belong.

    Narrowing by area of interest is the obvious fix and is also a trap. The
    published Kalianpur operations declare areas of use that a single village's
    bounding box does not intersect, so passing the layer's own extent filtered
    *every real operation out* and left only the ballpark -- turning a silent
    error into a refusal of perfectly good data. So the extent is tried first,
    because a region-specific operation is the better answer when one applies,
    and the unrestricted candidate set is the fallback.

    Returns `(transform, description, accuracy_m)`.
    """
    from pyproj import CRS
    from pyproj.transformer import AreaOfInterest, TransformerGroup

    wgs84 = CRS.from_epsg(4326)
    west, south, east, north = bounds

    def real_candidates(area: Any) -> list[Any]:
        kwargs = {"area_of_interest": area} if area is not None else {}
        group = TransformerGroup(source, wgs84, always_xy=True, **kwargs)
        return [t for t in group.transformers if "ballpark" not in (t.description or "").lower()]

    attempts = (
        AreaOfInterest(
            west_lon_degree=west,
            south_lat_degree=south,
            east_lon_degree=east,
            north_lat_degree=north,
        ),
        None,
    )
    usable: list[Any] = []
    for area in attempts:
        usable = real_candidates(area)
        if usable:
            break

    if not usable:
        raise CadastralError(
            f"no datum transformation is published from {source.name} to WGS 84 "
            "-- only PROJ's ballpark offset, which would leave the coordinates "
            "unchanged. Since these are different datums, that would place every "
            "parcel roughly 100-400 m from its true position while looking "
            "correct. Reproject the layer to WGS 84 before uploading it, or "
            "install the PROJ transformation grids for this region."
        )

    # Best stated accuracy. A -1 means "unknown", which is not the same as
    # "good", so it sorts last rather than first.
    usable.sort(key=lambda t: (t.accuracy if t.accuracy and t.accuracy > 0 else 1e9))
    best = usable[0]
    accuracy = best.accuracy if best.accuracy and best.accuracy > 0 else float("nan")
    return best.transform, (best.description or "").strip(), accuracy


def _area_ha(geometry: dict[str, Any]) -> float:
    """Parcel area in hectares, measured on an equal-area projection.

    Not from degrees: a square degree is not a constant area, and treating it as
    one over-states parcels in the north of India against the south.
    """
    from pyproj import Geod
    from shapely.geometry import shape

    geom = shape(geometry)
    if geom.is_empty:
        return 0.0
    geod = Geod(ellps="WGS84")
    area, _perimeter = geod.geometry_area_perimeter(geom)
    return abs(area) / 10_000.0


def _classify(properties: dict[str, Any], field: str | None) -> tuple[str | None, bool]:
    if not field:
        return None, False
    raw = properties.get(field)
    if raw is None:
        return None, False
    text = str(raw).strip()
    lowered = text.lower()
    return text, any(token in lowered for token in PUBLIC_TENURE_TOKENS)


def _pick_ownership_field(sample: dict[str, Any]) -> str | None:
    lowered = {k.lower(): k for k in sample}
    for candidate in OWNERSHIP_FIELDS:
        if candidate in lowered:
            return lowered[candidate]
    return None


def load(data: bytes, filename: str | None = None) -> CadastralLayer:
    """Parse a GeoJSON or zipped shapefile into parcels in EPSG:4326.

    Raises `CadastralError` with a specific reason on anything unusable — an
    unreadable file, a missing CRS, or an archive that looks hostile.
    """
    if not data:
        raise CadastralError("the uploaded file is empty")
    if len(data) > MAX_UPLOAD_BYTES:
        raise CadastralError(
            f"the file is {len(data) / 1e6:.1f} MB; the limit is "
            f"{MAX_UPLOAD_BYTES / 1e6:.0f} MB"
        )

    import tempfile

    is_zip = data[:2] == b"PK"
    with tempfile.TemporaryDirectory(prefix="cadastral-") as tmp:
        directory = Path(tmp)
        if is_zip:
            try:
                source = _extract(data, directory)
            except zipfile.BadZipFile as exc:
                raise CadastralError(f"the archive is not a readable zip: {exc}") from exc
        else:
            source = directory / (Path(filename or "layer.geojson").name or "layer.geojson")
            if source.suffix.lower() not in (".geojson", ".json"):
                source = directory / "layer.geojson"
            source.write_bytes(data)
        return _read(source, zipped=is_zip)


def _read(path: Path, *, zipped: bool) -> CadastralLayer:
    import fiona
    from pyproj import CRS
    from shapely.geometry import mapping, shape
    from shapely.ops import transform as shapely_transform

    notes: list[str] = []
    try:
        with fiona.open(path) as source:
            crs_wkt = source.crs_wkt or ""
            raw_crs = source.crs
            features = list(source)
    except Exception as exc:  # fiona raises a family of driver errors
        raise CadastralError(
            f"could not read the layer: {type(exc).__name__}: {exc}. Expected "
            "GeoJSON, or a zip holding .shp/.shx/.dbf/.prj together."
        ) from exc

    if not features:
        raise CadastralError("the layer holds no features")

    if not crs_wkt and not raw_crs:
        # Reachable only for a non-shapefile source, since a zip without a .prj
        # is already refused. GeoJSON has a specified default, so falling back is
        # correct rather than a guess.
        if zipped:
            raise CadastralError(
                "the shapefile declares no coordinate reference system; supply " "its .prj."
            )
        crs_wkt = "EPSG:4326"
        notes.append(
            "No CRS declared. Read as WGS 84, which RFC 7946 requires of "
            "GeoJSON -- this is the specified default, not an assumption. A "
            "shapefile has no such default and is refused without its .prj."
        )

    crs = CRS.from_user_input(crs_wkt or raw_crs)
    wgs84 = CRS.from_epsg(4326)
    reprojected = not crs.equals(wgs84)
    transformer = None
    operation = None
    accuracy = float("nan")
    if reprojected:
        transformer, operation, accuracy = _datum_transformer(crs, _extent(features, crs))
        notes.append(
            f"Reprojected from {crs.name} to WGS 84 using '{operation}'"
            + (f", stated accuracy {accuracy:g} m." if accuracy == accuracy else ".")
            + " The operation is chosen explicitly rather than left to PROJ's "
            "default, which selects a ballpark offset that moves nothing."
        )

    ownership_field = _pick_ownership_field(dict(features[0].get("properties") or {}))
    if ownership_field is None:
        notes.append(
            "No ownership attribute recognised, so every parcel is reported as "
            f"tenure-unknown. Looked for: {', '.join(OWNERSHIP_FIELDS)}."
        )

    parcels: list[Parcel] = []
    skipped = 0
    for index, feature in enumerate(features):
        geometry = feature.get("geometry")
        if not geometry:
            skipped += 1
            continue
        try:
            geom = shape(dict(geometry))
            if geom.is_empty:
                skipped += 1
                continue
            if not geom.is_valid:
                geom = geom.buffer(0)
            if transformer is not None:
                geom = shapely_transform(transformer, geom)
        except Exception:  # a bad ring is data, not a bug
            skipped += 1
            continue

        properties = dict(feature.get("properties") or {})
        ownership, is_public = _classify(properties, ownership_field)
        as_geojson = dict(mapping(geom))
        parcels.append(
            Parcel(
                parcel_id=str(feature.get("id") or index + 1),
                geometry=as_geojson,
                ownership=ownership,
                is_public=is_public,
                area_ha=_area_ha(as_geojson),
                attributes=properties,
            )
        )

    if not parcels:
        raise CadastralError("no usable geometry in the layer")

    if ownership_field and not any(p.is_public for p in parcels):
        notes.append(
            f"No parcel in `{ownership_field}` matched a public-tenure term, so "
            "none is flagged allottable. Check the vocabulary the sheet uses."
        )

    log.info(
        "cadastral layer loaded",
        parcels=len(parcels),
        skipped=skipped,
        crs=crs.name,
        zipped=zipped,
    )
    return CadastralLayer(
        parcels=tuple(parcels),
        source_crs=crs.name,
        ownership_field=ownership_field,
        reprojected=reprojected,
        datum_operation=operation,
        datum_accuracy_m=accuracy,
        skipped=skipped,
        notes=tuple(notes),
    )
