"""Application configuration (12-factor, env-driven).

M0-17: configuration is validated at import/startup and fails fast with a
message naming the missing variable. The alternative -- discovering a missing
API key as a 500 three minutes into an analysis -- wastes far more time.
"""

from __future__ import annotations

from functools import lru_cache
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
    POSTGRES_HOST: str = "postgis"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "contour"
    POSTGRES_USER: str = "contour"
    POSTGRES_PASSWORD: str = "contour_dev_only_change_me"

    # ── redis / celery ───────────────────────────────────────────────────────
    REDIS_URL: str = "redis://redis:6379/0"

    # ── storage / tiles ──────────────────────────────────────────────────────
    COG_STORE_PATH: str = "/data/cache"
    TITILER_ENDPOINT: str = "http://titiler:8000"

    # ── provider credentials ─────────────────────────────────────────────────
    # NOTE: none of these are required to run the mandatory pipeline. The DEM
    # comes from the Copernicus GLO-30 AWS Open Data bucket, which needs no
    # credential at all (HLD 4.2 A1). Each key below unlocks an *enrichment*.
    OPENTOPOGRAPHY_API_KEY: str = ""  # optional: SRTM/NASADEM/AW3D30 variety
    DATA_GOV_IN_API_KEY: str = ""  # optional: official IMD district rainfall
    # Ring 2 (M8/M9) -- absent is fine; adapters report NotConfigured
    BHUVAN_TOKEN: str = ""
    BHOONIDHI_API_KEY: str = ""
    COPERNICUS_CLIENT_ID: str = ""
    COPERNICUS_CLIENT_SECRET: str = ""

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
    #: What each capability needs. Checked on demand, not at startup, so the app
    #: still boots (and /health still answers) with Ring 2 keys absent.
    #: ClassVar, not a field: this is a constant of the code, and must not be
    #: overridable from the environment.
    FEATURE_REQUIREMENTS: ClassVar[dict[str, tuple[str, ...]]] = {
        # Empty tuple => always available. The primary DEM source is an open
        # S3 bucket, so terrain acquisition has no credential dependency.
        "dem_acquisition": (),
        "dem_alternate_products": ("OPENTOPOGRAPHY_API_KEY",),
        "imd_district_rainfall": ("DATA_GOV_IN_API_KEY",),
        "bhuvan_layers": ("BHUVAN_TOKEN",),
        "bhoonidhi_cartodem": ("BHOONIDHI_API_KEY",),
        "sentinel2_ndwi": ("COPERNICUS_CLIENT_ID", "COPERNICUS_CLIENT_SECRET"),
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
