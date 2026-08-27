"""The all-India village and town name index (HLD 4.2 E1, adapted).

SHRUG's village *polygons* are distributed through a form on devdatalab.org
rather than a fetchable URL. Its *names* are not: the SHRID-to-LGD crosswalk
published on Harvard Dataverse is **CC0**, needs no credentials, and carries
every one of the 596,390 Census-2011 villages and towns with its full
state / district / sub-district hierarchy.

That is the more valuable half for what the system does. A user searching for
their village needs the name index; the polygon only matters once they have
found it, and the analysis itself takes its terrain from an uploaded contour map
or a DEM, never from a village outline.

The SHRID is the canonical key, which is what HLD CH-24 asks for -- a code, not
a name. It composes the Census-2011 codes::

    11-22-409-03317-442569
    │  │  │   │     └── village
    │  │  │   └──────── sub-district (tehsil)
    │  │  └──────────── district
    │  └─────────────── state (22 = Chhattisgarh)
    └────────────────── SHRUG release lineage

Consecutive village codes are geographic neighbours, which is a useful property:
442569 (Kutelabhatha) and 442570 (Khapri) adjoin, and both lie inside the
surveyed area of the sample contour map.
"""

from __future__ import annotations

import csv
import io
import logging
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.core.names import normalise_name

log = logging.getLogger(__name__)

#: Harvard Dataverse file id for `shrid_loc_names.tab`, inside
#: doi:10.7910/DVN/QVFBFT ("All India SHRUG Shrid Matched to LGD"), CC0 1.0.
DATAVERSE_FILE_ID = 11058281
DOWNLOAD_URL = f"https://dataverse.harvard.edu/api/access/datafile/{DATAVERSE_FILE_ID}"

#: ~51 MB. Large enough to stream and cache rather than re-fetch.
EXPECTED_MIN_BYTES = 40_000_000
EXPECTED_ROWS = 590_000

DOWNLOAD_TIMEOUT_S = 600.0
CHUNK_BYTES = 1 << 20

REQUIRED_COLUMNS = frozenset(
    {"shrid2", "state_name", "district_name", "subdistrict_name", "town_name", "village_name"}
)


@dataclass(frozen=True, slots=True)
class PlaceRecord:
    """One village or town from the register.

    `slots=True` because the seeder holds ~600,000 of these; without it the
    per-instance dict roughly triples the memory this step needs.
    """

    shrid: str
    state: str
    district: str
    subdistrict: str
    name: str
    name_normalised: str
    #: Towns and villages are both in the register and are distinguished by
    #: which name column is populated. A town is not a pond-siting candidate,
    #: but it is a legitimate search result and a useful landmark.
    is_town: bool

    @property
    def census_village_code(self) -> str:
        """The trailing Census-2011 village/town code from the SHRID."""
        return self.shrid.rsplit("-", 1)[-1]


class ShrugNamesError(RuntimeError):
    """The register could not be obtained or does not look like the register."""


def download(destination: Path, *, url: str = DOWNLOAD_URL, force: bool = False) -> Path:
    """Fetch the register to `destination`, streaming, unless it is already there.

    Written to a temporary neighbour and moved into place on success, so an
    interrupted download can never leave a truncated file that a later run
    mistakes for a complete one.
    """
    destination = Path(destination)
    if destination.exists() and not force:
        size = destination.stat().st_size
        if size >= EXPECTED_MIN_BYTES:
            log.info("village register already cached (%.1f MB)", size / 1e6)
            return destination
        log.warning("cached register is only %.1f MB; re-downloading", size / 1e6)

    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    written = 0
    try:
        with httpx.stream(
            "GET", url, timeout=DOWNLOAD_TIMEOUT_S, follow_redirects=True
        ) as response:
            response.raise_for_status()
            with partial.open("wb") as handle:
                for chunk in response.iter_bytes(CHUNK_BYTES):
                    handle.write(chunk)
                    written += len(chunk)
    except httpx.HTTPError as exc:
        partial.unlink(missing_ok=True)
        raise ShrugNamesError(f"could not download the village register: {exc}") from exc

    if written < EXPECTED_MIN_BYTES:
        partial.unlink(missing_ok=True)
        raise ShrugNamesError(
            f"downloaded only {written / 1e6:.1f} MB; expected at least "
            f"{EXPECTED_MIN_BYTES / 1e6:.0f} MB. The Dataverse file id may have changed."
        )
    partial.replace(destination)
    log.info("village register downloaded (%.1f MB)", written / 1e6)
    return destination


def iter_places(
    source: Path | str | io.TextIOBase,
    *,
    state: str | None = None,
    district: str | None = None,
) -> Iterator[PlaceRecord]:
    """Stream the register, optionally narrowed to one state or district.

    Streams rather than loading: the file is 51 MB of text and the seeder only
    ever needs one state at a time, so materialising all 596,390 rows to filter
    four thousand of them would be waste for no gain.

    Accepts a path or an already-open text handle -- the latter is what makes
    this testable against a few lines of in-memory TSV instead of a download.
    """
    if isinstance(source, io.TextIOBase):
        yield from _read(source, state=state, district=district)
        return
    with Path(source).open(encoding="utf-8", errors="replace") as handle:
        yield from _read(handle, state=state, district=district)


def _read(
    handle: io.TextIOBase,
    *,
    state: str | None,
    district: str | None,
) -> Iterator[PlaceRecord]:
    """Parse an open register handle into records.

    Names in the register are lower-cased already, but that is not relied on --
    `normalise_name` is applied regardless, and it is the same function the
    search path applies to the query, so both sides always meet in one space.
    """
    wanted_state = normalise_name(state) if state else None
    wanted_district = normalise_name(district) if district else None

    reader = csv.DictReader(handle, delimiter="\t", quotechar='"')
    missing = REQUIRED_COLUMNS - set(reader.fieldnames or ())
    if missing:
        raise ShrugNamesError(
            f"the register is missing expected columns: {sorted(missing)}. "
            f"Found: {sorted(reader.fieldnames or ())}"
        )

    for row in reader:
        shrid = _clean(row.get("shrid2"))
        if not shrid:
            continue

        state_name = _clean(row.get("state_name"))
        if wanted_state and normalise_name(state_name) != wanted_state:
            continue
        district_name = _clean(row.get("district_name"))
        if wanted_district and normalise_name(district_name) != wanted_district:
            continue

        village = _clean(row.get("village_name"))
        town = _clean(row.get("town_name"))
        name = village or town
        if not name:
            continue

        normalised = normalise_name(name)
        if not normalised:
            # A name that folds to nothing cannot be searched for, and the
            # column is NOT NULL. Skipping it is honest; inventing a key would
            # put a permanently unfindable row in the index.
            log.debug("skipping %s: name %r folds to empty", shrid, name)
            continue

        yield PlaceRecord(
            shrid=shrid,
            state=state_name,
            district=district_name,
            subdistrict=_clean(row.get("subdistrict_name")),
            name=name,
            name_normalised=normalised,
            is_town=bool(town) and not village,
        )


def _clean(value: str | None) -> str:
    """Strip the register's surrounding quotes and whitespace."""
    return (value or "").strip().strip('"').strip()
