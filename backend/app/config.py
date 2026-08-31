"""Application configuration (12-factor, env-driven).

M0-17: configuration is validated at import/startup and fails fast with a
message naming the missing variable. The alternative -- discovering a missing
API key as a 500 three minutes into an analysis -- wastes far more time.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import Field, computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigError(RuntimeError):
    """Raised when configuration is missing or inconsistent."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"), env_file_encoding="utf-8", extra="ignore"
    )

    # ── runtime ──────────────────────────────────────────────────────────────
    ENV: Literal["development", "test", "production"] = "development"
    LOG_LEVEL: str = "INFO"
    LOG_JSON: bool = True
    DEMO_MODE: bool = False

    # ── database ─────────────────────────────────────────────────────────────
    #: Defaults address the stack *from the host*, because that is the only
    #: context in which a default is consulted: `docker-compose.yml` sets
    #: `POSTGRES_HOST: postgis` and `POSTGRES_PORT: 5432` for the containers
    #: explicitly. In-cluster service names as defaults meant a host-run uvicorn
    #: failed with "Temporary failure in name resolution" before it ever read a
    #: setting the developer could see. The ports match the compose mapping,
    #: which deliberately avoids 5432/6379 -- see `.env.example`.
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 15432
    POSTGRES_DB: str = "contour"
    POSTGRES_USER: str = "contour"
    POSTGRES_PASSWORD: str = "contour_dev_only_change_me"

    # ── redis / celery ───────────────────────────────────────────────────────
    #: As above: the host-side address of the compose-published Redis port.
    REDIS_URL: str = "redis://localhost:16379/0"

    # ── storage / tiles ──────────────────────────────────────────────────────
    #: Defaults to `<repo>/data/cache`, resolved from this file rather than from
    #: the working directory, so `uvicorn` started in `backend/` and pytest
    #: started at the root agree. The previous default was the container's
    #: absolute `/data/cache`, which made every host run try to mkdir `/data`
    #: and fail: two provider caches logged a warning and the derivatives
    #: endpoint returned 500. Compose still sets `/data/cache` explicitly, and
    #: because it bind-mounts `./data` there, host and container share one
    #: directory -- a cache warmed in either is visible to the other.
    COG_STORE_PATH: str = str(Path(__file__).resolve().parents[2] / "data" / "cache")
    #: As above: compose maps TiTiler's 8000 to 8001 on the host.
    TITILER_ENDPOINT: str = "http://localhost:8001"

    #: Where the raster store appears *to TiTiler*, which is a separate container
    #: with its own filesystem view.
    #:
    #: A tile URL carries `?url=<path to the COG>`, and TiTiler opens that path
    #: itself -- so it must be TiTiler's path, not ours. The two agree only when
    #: the API is also a container. Run the API on the host and it writes
    #: `<repo>/data/cache/...` while TiTiler sees the same bytes at
    #: `/data/cache/...`, so every tile came back HTTP 500 "No such file or
    #: directory" and the slope and shaded-relief layers were silently blank
    #: while the API reported success.
    #:
    #: `_tile_url` rewrites the `COG_STORE_PATH` prefix to this one. Setting them
    #: equal disables the translation, which is what the containers do.
    TILER_STORE_PATH: str = "/data/cache"

    # ── provider credentials: there are none ─────────────────────────────────
    # This project runs entirely on keyless open data, and that is a deliberate
    # constraint rather than a stage it has not reached yet.
    #
    # Five credential fields used to live here -- OpenTopography, data.gov.in,
    # Bhuvan, Bhoonidhi and Copernicus Data Space -- for the ISRO-integration and
    # ML phases (M8/M9). Those phases were never built, no code ever read the
    # values, and every capability they would have added already has a working
    # keyless source in production:
    #
    #   land cover   ESA WorldCover 10 m (open S3)      instead of Bhuvan LULC
    #   elevation    the uploaded contour sheet, and    instead of CartoDEM
    #                Copernicus GLO-30 (AWS Open Data)
    #   rainfall     Open-Meteo ERA5-Land + NASA POWER  instead of IMD via OGD
    #                (two independent 30-year reanalyses, cross-checked)
    #
    # Keeping empty placeholders made `/health/ready` report five "missing: KEY"
    # lines that read as problems while describing features that do not exist.
    # Configuration for something the code does not do is worse than no
    # configuration, so it is gone.

    # ── analysis behaviour ───────────────────────────────────────────────────
    MAX_AOI_KM2: float = 100.0
    DEFAULT_DEM_SOURCE: Literal["COP30", "SRTMGL1", "NASADEM", "AW3D30"] = "COP30"
    AOI_BUFFER_M: float = 500.0
    SUITABILITY_ALPHA: float = Field(0.6, ge=0.0, le=1.0)
    CORS_ORIGINS: str = "http://localhost:5173,http://localhost:8080"

    @field_validator("LOG_LEVEL")
    @classmethod
    def _valid_log_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        up = v.upper()
        if up not in allowed:
            raise ValueError(f"LOG_LEVEL must be one of {sorted(allowed)}, got {v!r}")
        return up

    @field_validator("MAX_AOI_KM2")
    @classmethod
    def _sane_aoi_cap(cls, v: float) -> float:
        if not 1.0 <= v <= 1000.0:
            raise ValueError(f"MAX_AOI_KM2 must be within [1, 1000] km2, got {v}")
        return v

    @computed_field  # type: ignore[prop-decorator]
    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    # ── feature gating ───────────────────────────────────────────────────────
    #: What each capability needs. Every entry is an empty tuple, because every
    #: data source this system uses is keyless -- and the mechanism is kept
    #: rather than deleted so that adding a gated capability later is a one-line
    #: change with the reporting already wired through `/health/ready`.
    #: ClassVar, not a field: this is a constant of the code, and must not be
    #: overridable from the environment.
    FEATURE_REQUIREMENTS: ClassVar[dict[str, tuple[str, ...]]] = {
        "dem_acquisition": (),
        "contour_map_ingest": (),
        "land_cover": (),
        "soil": (),
        "rainfall_reanalysis": (),
        "osm_features": (),
    }

    def missing_for(self, feature: str) -> list[str]:
        """Names of the env vars a feature needs but does not have."""
        if feature not in self.FEATURE_REQUIREMENTS:
            raise KeyError(f"unknown feature {feature!r}")
        return [k for k in self.FEATURE_REQUIREMENTS[feature] if not getattr(self, k, "")]

    def is_available(self, feature: str) -> bool:
        return not self.missing_for(feature)

    def require(self, feature: str) -> None:
        """Raise a ConfigError naming exactly what to set."""
        missing = self.missing_for(feature)
        if missing:
            raise ConfigError(
                f"{feature!r} is not configured: set {', '.join(missing)} in .env "
                "(see .env.example)."
            )

    def startup_report(self) -> dict[str, object]:
        """Logged once at startup so the running configuration is never a mystery."""
        return {
            "env": self.ENV,
            "demo_mode": self.DEMO_MODE,
            "dem_source": self.DEFAULT_DEM_SOURCE,
            "max_aoi_km2": self.MAX_AOI_KM2,
            "features_available": sorted(
                f for f in self.FEATURE_REQUIREMENTS if self.is_available(f)
            ),
            "features_unavailable": sorted(
                f for f in self.FEATURE_REQUIREMENTS if not self.is_available(f)
            ),
        }

    def validate_startup(self) -> list[str]:
        """Fail fast on anything fatal; return non-fatal warnings.

        Missing *Ring 2* credentials are warnings: the app must still boot so
        that /health answers and Ring 1 work continues. A production deployment
        still using the default DB password is fatal.
        """
        if self.ENV == "production" and self.POSTGRES_PASSWORD == "contour_dev_only_change_me":
            raise ConfigError(
                "POSTGRES_PASSWORD is still the development default; refusing "
                "to start with ENV=production."
            )
        warnings: list[str] = []
        for feature in self.FEATURE_REQUIREMENTS:
            missing = self.missing_for(feature)
            if missing:
                warnings.append(f"{feature} disabled (missing {', '.join(missing)})")
        return warnings


@lru_cache
def get_settings() -> Settings:
    return Settings()
