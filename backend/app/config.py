"""Application settings, loaded from the environment."""

from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_ROOT.parent


class Settings(BaseSettings):
    """Runtime configuration.

    Every field can be overridden with an environment variable of the same
    name (case-insensitive), e.g. ``DATABASE_URL=postgresql+psycopg://...``.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "FreightLens API"
    environment: str = "local"
    database_url: str = f"sqlite+pysqlite:///{BACKEND_ROOT / 'freightlens.db'}"
    cors_origins: str = "http://localhost:5173,http://localhost:3000"
    sample_data_dir: Path = REPO_ROOT / "sample_data"
    frontend_dir: Path = REPO_ROOT / "frontend"

    # --- Audit tolerances -------------------------------------------------
    # Variances below these thresholds are considered rounding noise and are
    # not raised as exceptions. Carriers and shippers both round, so auditing
    # at the penny level produces unactionable noise.
    amount_tolerance: Decimal = Decimal("1.00")
    percent_tolerance: Decimal = Decimal("0.02")

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    """Cached settings accessor so the env is only parsed once per process."""
    return Settings()


settings = get_settings()
