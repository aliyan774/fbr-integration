"""Settings loaded from the environment / .env via pydantic-settings.

One middleware instance serves both PRAL environments. The caller picks one in the URL:

    /api/v1/dev/invoices   -> PRAL sandbox  (``_sb`` URLs, scenarioId required and sent)
    /api/v1/prd/invoices   -> PRAL production (plain URLs, scenarioId stripped)

SAP sends the Bearer token that matches the environment (Z table ZFBR_TOKEN keyed by ENV).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_DEV = "dev"
ENV_PRD = "prd"
ENV_NAMES: tuple[str, ...] = (ENV_DEV, ENV_PRD)


@dataclass(frozen=True)
class FbrEnvironment:
    """Everything that differs between the two PRAL environments."""

    name: str  # "dev" | "prd"
    base_url: str
    suffix: str  # "_sb" | ""
    send_scenario_id: bool

    @property
    def is_sandbox(self) -> bool:
        return self.name == ENV_DEV

    @property
    def label(self) -> str:
        return "sandbox" if self.is_sandbox else "production"

    @property
    def validate_url(self) -> str:
        return f"{self.base_url}/di_data/v1/di/validateinvoicedata{self.suffix}"

    @property
    def post_url(self) -> str:
        return f"{self.base_url}/di_data/v1/di/postinvoicedata{self.suffix}"


class Settings(BaseSettings):
    # ENV_FILE lets the Windows service point at C:/fbr-middleware/.env (flow doc section 10).
    model_config = SettingsConfigDict(
        env_file=os.environ.get("ENV_FILE", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # PRAL gateway. FBR_BASE_URL is used for both environments unless overridden per environment,
    # e.g. FBR_DEV_BASE_URL=http://127.0.0.1:9000 to point dev at the mock while prd stays real.
    FBR_BASE_URL: str = "https://gw.fbr.gov.pk"
    FBR_DEV_BASE_URL: str = ""
    FBR_PRD_BASE_URL: str = ""
    # Comma-separated list of environments this instance accepts (others answer 404).
    FBR_ENABLED_ENVS: str = "dev,prd"
    FBR_TIMEOUT_SECONDS: float = 30.0
    FBR_MAX_RETRIES_5XX: int = 3

    # Concurrency
    MAX_INFLIGHT_PER_SELLER: int = 1
    MAX_INFLIGHT_TOTAL: int = 20

    # Middleware auth (comma separated)
    MIDDLEWARE_API_KEYS: str = ""

    # Storage
    DATABASE_URL: str = "sqlite+aiosqlite:///./data/fbr_middleware.db"

    # Reference sync (TODO: scheduler not implemented yet)
    REFERENCE_SYNC_CRON: str = "0 2 * * *"

    # Logging and listener
    LOG_DIR: str = "./logs"
    LOG_LEVEL: str = "INFO"
    LISTEN_HOST: str = "0.0.0.0"
    LISTEN_PORT: int = 8000

    # Optional TLS directly on uvicorn (flow doc section 10). Empty = plain HTTP (behind IIS ARR).
    SSL_CERTFILE: str = ""
    SSL_KEYFILE: str = ""

    @field_validator("FBR_BASE_URL", "FBR_DEV_BASE_URL", "FBR_PRD_BASE_URL", mode="before")
    @classmethod
    def _strip_trailing_slash(cls, v: object) -> str:
        return str(v or "").strip().rstrip("/")

    @field_validator("FBR_ENABLED_ENVS", mode="before")
    @classmethod
    def _check_envs(cls, v: object) -> str:
        names = [n.strip().lower() for n in str(v or "").split(",") if n.strip()]
        unknown = [n for n in names if n not in ENV_NAMES]
        if unknown:
            raise ValueError(f"FBR_ENABLED_ENVS: unknown environment(s) {unknown}; allowed: {list(ENV_NAMES)}")
        if not names:
            raise ValueError("FBR_ENABLED_ENVS must list at least one of dev, prd")
        return ",".join(names)

    # ---- derived helpers ----
    @property
    def api_keys(self) -> set[str]:
        return {k.strip() for k in self.MIDDLEWARE_API_KEYS.split(",") if k.strip()}

    @property
    def enabled_envs(self) -> tuple[str, ...]:
        return tuple(self.FBR_ENABLED_ENVS.split(","))

    def environment(self, name: str) -> FbrEnvironment | None:
        """The environment for a URL segment, or None if unknown / not enabled on this instance."""
        name = (name or "").lower()
        if name not in self.enabled_envs:
            return None
        if name == ENV_DEV:
            return FbrEnvironment(
                name=ENV_DEV,
                base_url=self.FBR_DEV_BASE_URL or self.FBR_BASE_URL,
                suffix="_sb",
                send_scenario_id=True,
            )
        return FbrEnvironment(
            name=ENV_PRD,
            base_url=self.FBR_PRD_BASE_URL or self.FBR_BASE_URL,
            suffix="",
            send_scenario_id=False,
        )

    def environments(self) -> list[FbrEnvironment]:
        return [env for name in self.enabled_envs if (env := self.environment(name)) is not None]

    @property
    def log_dir_path(self) -> Path:
        return Path(self.LOG_DIR)


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    """Used by tests to re-read settings after changing environment variables."""
    get_settings.cache_clear()
