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

    def test_alternate_dem_products_are_still_gated(self) -> None:
        # SRTM / NASADEM / AW3D30 variety needs OpenTopography -- optional only.
        assert not _s().is_available("dem_alternate_products")
        assert _s(OPENTOPOGRAPHY_API_KEY="abc").is_available("dem_alternate_products")

    def test_sentinel_needs_both_credentials(self) -> None:
        s = _s(COPERNICUS_CLIENT_ID="id")
        assert s.missing_for("sentinel2_ndwi") == ["COPERNICUS_CLIENT_SECRET"]

    def test_require_names_exactly_what_to_set(self) -> None:
        with pytest.raises(ConfigError) as exc:
            _s().require("bhuvan_layers")
        assert "BHUVAN_TOKEN" in str(exc.value)
        assert ".env" in str(exc.value)

    def test_require_passes_when_configured(self) -> None:
        _s(BHUVAN_TOKEN="abc").require("bhuvan_layers")

    def test_require_is_a_no_op_for_an_ungated_feature(self) -> None:
        _s().require("dem_acquisition")

    def test_unknown_feature_is_a_programming_error(self) -> None:
        with pytest.raises(KeyError):
            _s().missing_for("teleportation")


class TestStartupValidation:
    def test_missing_ring2_keys_are_warnings_not_failures(self) -> None:
        # The app must still boot so /health answers and Ring 1 work continues.
        warnings = _s(DATA_GOV_IN_API_KEY="def").validate_startup()
        assert any("bhuvan_layers" in w for w in warnings)
        # dem_acquisition is ungated, so it must never surface as a warning.
        assert all("dem_acquisition" not in w for w in warnings)

    def test_production_refuses_the_default_db_password(self) -> None:
        with pytest.raises(ConfigError, match="POSTGRES_PASSWORD"):
            _s(ENV="production").validate_startup()

    def test_production_ok_with_a_real_password(self) -> None:
        _s(ENV="production", POSTGRES_PASSWORD="s3cret").validate_startup()

    def test_startup_report_partitions_features(self) -> None:
        r = _s().startup_report()
        assert "dem_acquisition" in r["features_available"]  # type: ignore[operator]
        assert "bhuvan_layers" in r["features_unavailable"]  # type: ignore[operator]

    def test_report_never_contains_a_secret(self) -> None:
        r = _s(OPENTOPOGRAPHY_API_KEY="super-secret-value").startup_report()
        assert "super-secret-value" not in str(r)
