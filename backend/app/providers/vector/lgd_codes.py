"""Gram Panchayat LGD codes and the Census 2001 crosswalk (M2-2c).

From the same CC0 Harvard Dataverse dataset as the village name index
(`doi:10.7910/DVN/QVFBFT`), file `All India Village to GP LGD codes.tab`.

What this file is *not*: a source of village LGD codes. HLD E2 asks for one and
the open data does not have it. Column 10 is the **Gram Panchayat** LGD code --
the elected local body, a cluster of villages. See migration 0003 for why that
distinction is kept rather than collapsed.

What it is good for:

* the Gram Panchayat is the body that plans and executes MGNREGA water works, so
  it is the unit a pond proposal is actually addressed to;
* it holds the only LGD code available in open data at all;
* its names disambiguate villages the Census hierarchy cannot -- the two Khapris
  of Durg sub-district sit in Panchayats named `Khapri` and `Khapri K`;
* it carries the Census 2001 village code alongside the 2011 one.

Joining is on the **Census 2011 codes, never on names**: this file's district and
sub-district columns reflect a later reorganisation than the register's, so the
hierarchies disagree. The SHRID decomposes into exactly these codes, verified on
Kutelabhatha -- SHRID `11-22-409-03317-442569` against state 22, district 409,
sub-district 3317, village 442569.
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

#: `All India Village to GP LGD codes.tab` inside doi:10.7910/DVN/QVFBFT, CC0 1.0.
DATAVERSE_FILE_ID = 11058283
DOWNLOAD_URL = f"https://dataverse.harvard.edu/api/access/datafile/{DATAVERSE_FILE_ID}"

#: ~56 MB across 638,847 rows.
EXPECTED_MIN_BYTES = 40_000_000

DOWNLOAD_TIMEOUT_S = 600.0
CHUNK_BYTES = 1 << 20

COL_STATE_NAME = "State Name"
COL_STATE_CODE = "State Code"
COL_DISTRICT_NAME = "District Name"
COL_DISTRICT_CODE = "District Census 2011 Code"
COL_SUBDISTRICT_NAME = "Subdistrict Name"
COL_SUBDISTRICT_CODE = "Subdistrict Census 2011 Code"
COL_VILLAGE_NAME = "Village Name"
COL_VILLAGE_2011 = "Village Census 2011 Code"
COL_VILLAGE_2001 = "Village Census 2001 Code"
COL_GP_LGD = "Gram Panchayat LGD Code"
COL_GP_NAME = "Gram Panchayat Name"

REQUIRED_COLUMNS = frozenset({COL_STATE_NAME, COL_VILLAGE_2011, COL_GP_LGD, COL_GP_NAME})


class LgdCodesError(RuntimeError):
    """The crosswalk could not be obtained or is not the file expected."""


@dataclass(frozen=True, slots=True)
class PanchayatLink:
    """One village-to-Panchayat row.

    `slots=True` for the same reason as `PlaceRecord`: the seeder holds hundreds
    of thousands of these at once.
    """

    #: Census 2011 village code -- the join key to `villages.census_2011_id`.
    village_census_2011: str
    census_2001: str | None
    state_code: str
    district_code: str
    subdistrict_code: str
    state_name: str
    district_name: str
    subdistrict_name: str
    village_name: str
    gp_lgd_code: str
    gp_name: str
    gp_name_normalised: str


def download(destination: Path, *, url: str = DOWNLOAD_URL, force: bool = False) -> Path:
    """Fetch the crosswalk to `destination`, streaming, unless already cached."""
    destination = Path(destination)
    if destination.exists() and not force:
        size = destination.stat().st_size
        if size >= EXPECTED_MIN_BYTES:
            log.info("LGD crosswalk already cached (%.1f MB)", size / 1e6)
            return destination
        log.warning("cached crosswalk is only %.1f MB; re-downloading", size / 1e6)

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
        raise LgdCodesError(f"could not download the LGD crosswalk: {exc}") from exc

    if written < EXPECTED_MIN_BYTES:
        partial.unlink(missing_ok=True)
        raise LgdCodesError(
            f"downloaded only {written / 1e6:.1f} MB; expected at least "
            f"{EXPECTED_MIN_BYTES / 1e6:.0f} MB. The Dataverse file id may have changed."
        )
    partial.replace(destination)
    log.info("LGD crosswalk downloaded (%.1f MB)", written / 1e6)
    return destination


def iter_links(
    source: Path | str | io.TextIOBase, *, state: str | None = None
) -> Iterator[PanchayatLink]:
    """Stream village-to-Panchayat rows, optionally for one state only."""
    if isinstance(source, io.TextIOBase):
        yield from _read(source, state=state)
        return
    with Path(source).open(encoding="utf-8", errors="replace") as handle:
        yield from _read(handle, state=state)


def _read(handle: io.TextIOBase, *, state: str | None) -> Iterator[PanchayatLink]:
    wanted_state = normalise_name(state) if state else None

    reader = csv.DictReader(handle, delimiter="\t", quotechar='"')
    missing = REQUIRED_COLUMNS - set(reader.fieldnames or ())
    if missing:
        raise LgdCodesError(
            f"the LGD crosswalk is missing expected columns: {sorted(missing)}. "
            f"Found: {sorted(reader.fieldnames or ())}"
        )

    for row in reader:
        state_name = _clean(row.get(COL_STATE_NAME))
        if wanted_state and normalise_name(state_name) != wanted_state:
            continue

        village_code = _clean(row.get(COL_VILLAGE_2011))
        # 23,366 rows carry '0' rather than a code -- villages the crosswalk
        # could not place. There is nothing to join them on, so they are skipped
        # rather than attached to whatever a zero would collide with.
        if not village_code or village_code == "0":
            continue

        gp_lgd = _clean(row.get(COL_GP_LGD))
        gp_name = _clean(row.get(COL_GP_NAME))
        if not gp_lgd or not gp_name:
            # 6.6 % of rows have no Panchayat recorded. The village still exists
            # and stays searchable; only this link is absent.
            continue

        normalised = normalise_name(gp_name)
        if not normalised:
            continue

        census_2001 = _clean(row.get(COL_VILLAGE_2001))
        yield PanchayatLink(
            village_census_2011=village_code,
            census_2001=census_2001 or None,
            state_code=_clean(row.get(COL_STATE_CODE)),
            district_code=_clean(row.get(COL_DISTRICT_CODE)),
            subdistrict_code=_clean(row.get(COL_SUBDISTRICT_CODE)),
            state_name=state_name,
            district_name=_clean(row.get(COL_DISTRICT_NAME)),
            subdistrict_name=_clean(row.get(COL_SUBDISTRICT_NAME)),
            village_name=_clean(row.get(COL_VILLAGE_NAME)),
            gp_lgd_code=gp_lgd,
            gp_name=gp_name,
            gp_name_normalised=normalised,
        )


def _clean(value: str | None) -> str:
    """Strip the file's surrounding quotes and whitespace."""
    return (value or "").strip().strip('"').strip()
