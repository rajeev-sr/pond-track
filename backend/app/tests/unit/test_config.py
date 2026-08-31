"""Configuration loading, validation and feature gating (M0-17)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import ConfigError, Settings


def _s(**kw: object) -> Settings:
    return Settings(_env_file=None, **kw)  # type: ignore[call-arg]


class TestDefaults:
    def test_declared_defaults(self) -> None:
        # Asserts the *declared* defaults rather than a constructed instance:
        # pydantic-settings reads os.environ regardless of _env_file, so a
        # constructed Settings reflects the ambient environment, not the schema.
        fields = Settings.model_fields
        assert fields["ENV"].default == "development"
        assert fields["DEFAULT_DEM_SOURCE"].default == "COP30"
        assert fields["MAX_AOI_KM2"].default == 100.0
        assert fields["DEMO_MODE"].default is False

    def test_constructs_without_a_dotenv_file(self) -> None:
        s = _s()
        assert s.DEFAULT_DEM_SOURCE == "COP30"
        assert s.MAX_AOI_KM2 == 100.0

    def test_environment_overrides_the_default(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        monkeypatch.setenv("MAX_AOI_KM2", "42")
        assert _s().MAX_AOI_KM2 == 42.0

    def test_database_url_is_psycopg3(self) -> None:
        # Every component is passed explicitly: relying on defaults here would
        # couple the assertion to whatever conftest puts in the environment.
        url = _s(
            POSTGRES_USER="u",
            POSTGRES_PASSWORD="p",
            POSTGRES_HOST="h",
            POSTGRES_PORT=5432,
            POSTGRES_DB="d",
        ).database_url
        assert url == "postgresql+psycopg://u:p@h:5432/d"

    def test_cors_origins_parse_to_a_list(self) -> None:
        s = _s(CORS_ORIGINS="http://a.test, http://b.test ,")
        assert s.cors_origin_list == ["http://a.test", "http://b.test"]


class TestValidators:
    def test_log_level_is_normalised(self) -> None:
        assert _s(LOG_LEVEL="debug").LOG_LEVEL == "DEBUG"

    def test_rejects_bad_log_level(self) -> None:
        with pytest.raises(ValidationError):
            _s(LOG_LEVEL="chatty")

    @pytest.mark.parametrize("v", [0.5, 1001.0])
    def test_rejects_absurd_aoi_cap(self, v: float) -> None:
        with pytest.raises(ValidationError):
            _s(MAX_AOI_KM2=v)

    @pytest.mark.parametrize("v", [-0.1, 1.1])
    def test_alpha_must_be_a_fraction(self, v: float) -> None:
        with pytest.raises(ValidationError):
            _s(SUITABILITY_ALPHA=v)

    def test_alpha_one_means_pure_ahp(self) -> None:
        # HLD 6.5.4's documented degradation path must be expressible.
        assert _s(SUITABILITY_ALPHA=1.0).SUITABILITY_ALPHA == 1.0

    def test_rejects_unknown_dem_source(self) -> None:
        with pytest.raises(ValidationError):
            _s(DEFAULT_DEM_SOURCE="GUESSWORK")


class TestFeatureGating:
    def test_dem_acquisition_needs_no_credential(self) -> None:
        # The primary DEM source is the Copernicus GLO-30 AWS open bucket, so
        # the mandatory pipeline must be runnable from an entirely empty .env.
        s = _s()
        assert s.is_available("dem_acquisition")
        assert s.missing_for("dem_acquisition") == []

    def test_no_capability_requires_a_credential(self) -> None:
        """★ The project runs entirely on keyless open data.

        Five credential fields used to live in the config for ISRO and IMD
        sources planned in later phases. No code ever read them, every capability
        they would have gated already had a keyless equivalent in production, and
        their only visible effect was five "missing: KEY" lines in
        `/health/ready` describing features that did not exist. This asserts the
        replacement invariant: nothing needs a key.
        """
        settings = _s()
        for feature in settings.FEATURE_REQUIREMENTS:
            assert settings.missing_for(feature) == [], feature
            assert settings.is_available(feature), feature

    def test_no_credential_field_remains_on_the_settings(self) -> None:
        """A blank credential field is configuration for something the code does
        not do, which is worse than no configuration."""
        fields = set(_s().model_fields)
        for gone in (
            "OPENTOPOGRAPHY_API_KEY",
            "DATA_GOV_IN_API_KEY",
            "BHUVAN_TOKEN",
            "BHOONIDHI_API_KEY",
            "COPERNICUS_CLIENT_ID",
            "COPERNICUS_CLIENT_SECRET",
        ):
            assert gone not in fields, f"{gone} is back on Settings"

    def test_the_gating_mechanism_still_works(self) -> None:
        """Kept rather than deleted: adding a gated capability later should be a
        one-line change with the reporting already wired through /health."""
        assert _s().missing_for("dem_acquisition") == []
        _s().require("dem_acquisition")

    def test_unknown_feature_is_a_programming_error(self) -> None:
        with pytest.raises(KeyError):
            _s().missing_for("teleportation")


class TestStartupValidation:
    def test_startup_has_nothing_to_warn_about(self) -> None:
        """With no gated capabilities left, a clean start is genuinely clean.

        This used to assert that missing Ring 2 keys produced warnings rather
        than failures. There are no Ring 2 keys now, so the correct assertion is
        that startup is silent -- a warning about an absent credential would be
        noise about something nobody has to obtain.
        """
        assert _s().validate_startup() == []

    def test_production_refuses_the_default_db_password(self) -> None:
        with pytest.raises(ConfigError, match="POSTGRES_PASSWORD"):
            _s(ENV="production").validate_startup()

    def test_production_ok_with_a_real_password(self) -> None:
        _s(ENV="production", POSTGRES_PASSWORD="s3cret").validate_startup()

    def test_startup_report_lists_every_feature_as_available(self) -> None:
        r = _s().startup_report()
        assert "dem_acquisition" in r["features_available"]  # type: ignore[operator]
        assert r["features_unavailable"] == []

    def test_the_report_has_no_secret_to_leak(self) -> None:
        """It cannot leak a credential because there is no credential to hold."""
        report = str(_s().startup_report())
        for word in ("api_key", "token", "secret", "password"):
            assert word not in report.lower(), word


class TestTheDefaultsWorkOnTheHost:
    """A default is only ever consulted outside the compose network.

    `docker-compose.yml` sets `POSTGRES_HOST`, `POSTGRES_PORT`, `REDIS_URL` and
    `COG_STORE_PATH` for its containers explicitly, so these defaults apply
    exactly when the in-cluster names would not resolve: a developer running
    `uvicorn app.main:app` on the host. They used to be the in-cluster names, and
    that produced "Temporary failure in name resolution" for Postgres and Redis,
    two silent "cache write failed" warnings, and an unhandled 500 from
    /terrain/derivatives trying to `mkdir /data`.
    """

    def test_no_default_is_an_in_cluster_service_name(self) -> None:
        """Compared on the parsed hostname, not as a substring.

        `redis://localhost:16379/0` contains the text "redis:" while naming no
        compose service at all, so a substring test fails on a correct value.
        """
        from urllib.parse import urlparse

        from app.config import Settings

        services = {"postgis", "redis", "titiler", "api", "frontend"}
        defaults = Settings.model_fields
        hosts = {
            "POSTGRES_HOST": str(defaults["POSTGRES_HOST"].default),
            "REDIS_URL": urlparse(str(defaults["REDIS_URL"].default)).hostname or "",
            "TITILER_ENDPOINT": urlparse(str(defaults["TITILER_ENDPOINT"].default)).hostname or "",
        }
        for name, host in hosts.items():
            assert host not in services, f"{name} default resolves only inside compose: {host!r}"

    def test_the_connection_defaults_match_the_published_ports(self) -> None:
        """Not 5432/6379: compose maps those away on purpose, so a developer's
        own Postgres cannot be silently connected to instead."""
        from urllib.parse import urlparse

        from app.config import Settings

        assert Settings.model_fields["POSTGRES_PORT"].default == 15432
        assert urlparse(str(Settings.model_fields["REDIS_URL"].default)).port == 16379

    def test_the_store_default_is_absolute_and_inside_the_repo(self) -> None:
        """Resolved from the module, not the working directory.

        `uvicorn` is started in `backend/` and pytest at the repo root; a
        relative default would give them two different caches.
        """
        from pathlib import Path

        from app.config import Settings

        default = Path(str(Settings.model_fields["COG_STORE_PATH"].default))
        assert default.is_absolute()
        repo = Path(__file__).resolve().parents[4]
        assert default == repo / "data" / "cache"

    def test_the_store_default_is_writable_by_this_process(self) -> None:
        """The actual property that failed, asserted directly."""
        import os
        from pathlib import Path

        from app.config import Settings

        default = Path(str(Settings.model_fields["COG_STORE_PATH"].default))
        existing = default
        while not existing.exists() and existing != existing.parent:
            existing = existing.parent
        assert os.access(existing, os.W_OK), f"{existing} is not writable"
