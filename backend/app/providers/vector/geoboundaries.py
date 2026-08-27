"""Administrative boundaries from geoBoundaries (HLD 4.2, gap-fill for E1).

Village polygons for Chhattisgarh are not obtainable without credentials -- see
migration 0002 for the full account. geoBoundaries is what *is* available
openly, under ODbL 1.0, and it reaches:

===== ============== ======
Level Means          Units
===== ============== ======
ADM1  State             36
ADM2  District         736
ADM3  Sub-district   6,836
ADM4  CD Block       7,152
===== ============== ======

Sub-district is three levels above what a pond needs, so this is explicitly not
a village boundary and the schema records it as such (`boundary_level`). What it
does give is a real, correctly-georeferenced outline for every tehsil in India,
which is enough to place a village on a map, frame the view, and constrain a
search to the right part of the country.

One quirk drives the design here: geoBoundaries features carry no parent
reference. A sub-district does not say which district it belongs to, so the
hierarchy has to be rebuilt by containment -- which PostGIS does far better than
Python, so it is done in the database during seeding rather than here.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger(__name__)

API_ROOT = "https://www.geoboundaries.org/api/current/gbOpen"

#: What each ADM level means for India specifically. geoBoundaries reports this
#: per country in `boundaryCanonical`, and it differs between them -- ADM3 is a
#: sub-district here and something else elsewhere -- so it is checked against
#: this map at fetch time rather than assumed.
INDIA_LEVELS: dict[str, str] = {
    "ADM1": "state",
    "ADM2": "district",
    "ADM3": "subdistrict",
    "ADM4": "cd_block",
}

LICENCE = "ODbL 1.0 (geoBoundaries, gbOpen)"
SOURCE_NAME = "geoboundaries"

METADATA_TIMEOUT_S = 60.0
DOWNLOAD_TIMEOUT_S = 600.0
CHUNK_BYTES = 1 << 20

#: The national ADM3 file is ~106 MB; anything much smaller is a truncated
#: download or an error page saved to disk.
MIN_PLAUSIBLE_BYTES = 1_000_000


class GeoBoundariesError(RuntimeError):
    """The boundary set could not be obtained or is not what was expected."""


@dataclass(frozen=True, slots=True)
class BoundaryFeature:
    """One administrative area: its name, the source's id, and its geometry."""

    source_id: str
    name: str
    level: str
    geometry: dict[str, Any]


def metadata(iso3: str, level: str, *, client: httpx.Client | None = None) -> dict[str, Any]:
    """Fetch the release metadata for one country and level."""
    url = f"{API_ROOT}/{iso3.upper()}/{level.upper()}/"
    owned = client is None
    client = client or httpx.Client(timeout=METADATA_TIMEOUT_S, follow_redirects=True)
    try:
        response = client.get(url)
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPError as exc:
        raise GeoBoundariesError(f"could not read geoBoundaries metadata: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise GeoBoundariesError(f"geoBoundaries metadata was not JSON: {exc}") from exc
    finally:
        if owned:
            client.close()

    # The API returns either an object or a single-element list depending on the
    # endpoint version; normalise so callers do not have to care.
    if isinstance(payload, list):
        if not payload:
            raise GeoBoundariesError(f"no geoBoundaries release for {iso3} {level}")
        payload = payload[0]
    if not isinstance(payload, dict):
        raise GeoBoundariesError(f"unexpected geoBoundaries metadata shape: {type(payload)}")
    return payload


def download(
    destination: Path,
    *,
    iso3: str = "IND",
    level: str = "ADM3",
    force: bool = False,
    client: httpx.Client | None = None,
) -> Path:
    """Fetch one level's GeoJSON to `destination`, streaming, unless cached.

    Also verifies that the level still means what `INDIA_LEVELS` says. A silent
    redefinition upstream would put CD blocks in a column labelled sub-district,
    and nothing downstream would notice.
    """
    destination = Path(destination)
    if destination.exists() and not force and destination.stat().st_size >= MIN_PLAUSIBLE_BYTES:
        log.info("%s %s already cached (%.1f MB)", iso3, level, destination.stat().st_size / 1e6)
        return destination

    meta = metadata(iso3, level, client=client)
    canonical = str(meta.get("boundaryCanonical") or "").strip().lower()
    expected = INDIA_LEVELS.get(level.upper())
    # "Sub-District" against "subdistrict"; compare on letters alone.
    if (
        iso3.upper() == "IND"
        and expected
        and canonical
        and canonical.replace("-", "").replace(" ", "") != expected.replace("_", "")
    ):
        raise GeoBoundariesError(
            f"{level} now means {canonical!r} for {iso3}, not {expected!r}. "
            "Refusing to seed a level whose meaning changed."
        )

    url = meta.get("gjDownloadURL")
    if not url:
        raise GeoBoundariesError(f"no download URL in the {iso3} {level} metadata")

    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    written = 0
    try:
        with httpx.stream(
            "GET", str(url), timeout=DOWNLOAD_TIMEOUT_S, follow_redirects=True
        ) as response:
            response.raise_for_status()
            with partial.open("wb") as handle:
                for chunk in response.iter_bytes(CHUNK_BYTES):
                    handle.write(chunk)
                    written += len(chunk)
    except httpx.HTTPError as exc:
        partial.unlink(missing_ok=True)
        raise GeoBoundariesError(f"could not download {iso3} {level}: {exc}") from exc

    if written < MIN_PLAUSIBLE_BYTES:
        partial.unlink(missing_ok=True)
        raise GeoBoundariesError(
            f"{iso3} {level} download was only {written / 1e3:.0f} kB; expected megabytes"
        )
    partial.replace(destination)
    log.info("%s %s downloaded (%.1f MB)", iso3, level, written / 1e6)
    return destination


def read_features(path: Path, *, level: str) -> list[BoundaryFeature]:
    """Load a downloaded GeoJSON into `BoundaryFeature` records.

    Read whole rather than streamed: these files are tens of megabytes, are
    loaded once per seed, and a single geometry can be several megabytes on its
    own, so incremental parsing would buy little.
    """
    with Path(path).open(encoding="utf-8") as handle:
        payload = json.load(handle)

    features = payload.get("features")
    if not isinstance(features, list) or not features:
        raise GeoBoundariesError(f"{path} holds no features")

    canonical_level = INDIA_LEVELS.get(level.upper(), level.lower())
    out: list[BoundaryFeature] = []
    skipped = 0
    for feature in features:
        properties = feature.get("properties") or {}
        geometry = feature.get("geometry")
        source_id = str(properties.get("shapeID") or "").strip()
        name = str(properties.get("shapeName") or "").strip()
        if not (source_id and name and geometry):
            skipped += 1
            continue
        out.append(
            BoundaryFeature(
                source_id=source_id, name=name, level=canonical_level, geometry=geometry
            )
        )
    if skipped:
        log.warning("%s: skipped %d features with no id, name or geometry", path.name, skipped)
    return out
