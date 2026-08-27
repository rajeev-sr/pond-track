"""Soil texture and Hydrologic Soil Group from SoilGrids (HLD §4.2 C4).

SoilGrids (ISRIC) is a keyless REST service returning particle-size fractions at
250 m. Those become a USDA texture class, and the texture becomes the NRCS
Hydrologic Soil Group -- which is what the Curve Number lookup needs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from app.providers.base import Provenance, ProviderUnavailableError, get_json

PROVIDER = "soilgrids"
BASE_URL = "https://rest.isric.org/soilgrids/v2.0/properties/query"
PROVENANCE = Provenance(
    provider="ISRIC SoilGrids",
    dataset="SoilGrids v2.0 (clay/sand/silt, 0-5 cm)",
    resolution="250 m",
    licence="CC-BY 4.0",
)

HSG = Literal["A", "B", "C", "D"]

#: NRCS Hydrologic Soil Group per USDA texture class. A infiltrates freely, D
#: barely at all -- so D generates the most runoff and suits a pond best.
TEXTURE_TO_HSG: dict[str, HSG] = {
    "sand": "A",
    "loamy sand": "A",
    "sandy loam": "A",
    "loam": "B",
    "silt loam": "B",
    "silt": "B",
    "sandy clay loam": "C",
    "clay loam": "D",
    "silty clay loam": "D",
    "sandy clay": "D",
    "silty clay": "D",
    "clay": "D",
}

#: Saturated infiltration bands per group, mm/h (NRCS TR-55 Table 2-2 ranges).
HSG_INFILTRATION_MM_H: dict[str, str] = {
    "A": "> 7.6",
    "B": "3.8 - 7.6",
    "C": "1.3 - 3.8",
    "D": "< 1.3",
}


@dataclass(frozen=True)
class SoilProfile:
    clay_pct: float
    sand_pct: float
    silt_pct: float
    texture_class: str
    hydrologic_soil_group: str
    lon: float
    lat: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "clay_pct": round(self.clay_pct, 1),
            "sand_pct": round(self.sand_pct, 1),
            "silt_pct": round(self.silt_pct, 1),
            "usda_texture_class": self.texture_class,
            "hydrologic_soil_group": self.hydrologic_soil_group,
            "infiltration_rate_mm_per_h": HSG_INFILTRATION_MM_H[self.hydrologic_soil_group],
            "sampled_at": {"lon": round(self.lon, 6), "lat": round(self.lat, 6)},
            "source": PROVENANCE.as_dict(),
        }


def usda_texture_class(clay: float, sand: float, silt: float) -> str:
    """USDA soil texture triangle. Order matters: the classes overlap at edges,
    so the checks run from the most specific corner inward."""
    if sand >= 85.0 and silt + 1.5 * clay < 15.0:
        return "sand"
    if 70.0 <= sand <= 91.0 and silt + 1.5 * clay >= 15.0 and silt + 2.0 * clay < 30.0:
        return "loamy sand"
    if clay >= 40.0 and silt >= 40.0:
        return "silty clay"
    if clay >= 35.0 and sand >= 45.0:
        return "sandy clay"
    if clay >= 40.0:
        return "clay"
    if 27.0 <= clay < 40.0 and sand <= 20.0:
        return "silty clay loam"
    if 27.0 <= clay < 40.0 and 20.0 < sand <= 45.0:
        return "clay loam"
    if 20.0 <= clay < 35.0 and silt < 28.0 and sand > 45.0:
        return "sandy clay loam"
    if silt >= 80.0 and clay < 12.0:
        return "silt"
    if (silt >= 50.0 and 12.0 <= clay < 27.0) or (50.0 <= silt < 80.0 and clay < 12.0):
        return "silt loam"
    if (clay < 20.0 and sand > 52.0 and silt + 2.0 * clay >= 30.0) or (
        clay < 7.0 and silt < 50.0 and 43.0 <= sand <= 52.0
    ):
        return "sandy loam"
    return "loam"


def fetch_soil_profile(lon: float, lat: float) -> SoilProfile:
    """Particle-size fractions at a coordinate, as texture class and HSG."""
    data = get_json(
        PROVIDER,
        BASE_URL,
        params=[
            ("lon", lon),
            ("lat", lat),
            ("property", "clay"),
            ("property", "sand"),
            ("property", "silt"),
            ("depth", "0-5cm"),
            ("value", "mean"),
        ],
        # SoilGrids latency is erratic -- sampled at sub-second, 3.5 s, and a read
        # timeout from the same host minutes apart. A short timeout keeps a full
        # retry cycle inside the enrichment budget instead of blowing past it.
        timeout=8.0,
    )
    try:
        fractions: dict[str, float] = {}
        for layer in data["properties"]["layers"]:
            raw = layer["depths"][0]["values"]["mean"]
            if raw is None:
                raise ProviderUnavailableError(
                    PROVIDER, f"no {layer['name']} value at {lat:.4f},{lon:.4f}"
                )
            # SoilGrids returns g/kg for particle-size fractions; /10 gives percent.
            fractions[layer["name"]] = float(raw) / 10.0
        clay, sand, silt = fractions["clay"], fractions["sand"], fractions["silt"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderUnavailableError(PROVIDER, f"unexpected response shape: {exc}") from exc

    texture = usda_texture_class(clay, sand, silt)
    return SoilProfile(
        clay_pct=clay,
        sand_pct=sand,
        silt_pct=silt,
        texture_class=texture,
        hydrologic_soil_group=TEXTURE_TO_HSG[texture],
        lon=lon,
        lat=lat,
    )
