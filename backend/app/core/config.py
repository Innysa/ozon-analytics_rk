from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration, sourced from environment variables / Replit Secrets.

    Never hardcode secrets here — every sensitive value comes from the environment.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    APP_NAME: str = "Ozon AI Аналитик"
    ENV: Literal["development", "production", "test"] = "development"

    DATABASE_URL: str = "postgresql+psycopg://postgres:postgres@localhost:5432/ozon_analytics"

    SESSION_SECRET: str = "insecure-dev-secret-change-me"
    SESSION_COOKIE_NAME: str = "oaa_session"
    SESSION_MAX_AGE_SECONDS: int = 60 * 60 * 12  # 12 hours

    # None (default) = derive from ENV (Secure in production). Browsers never
    # send a Secure cookie back over plain HTTP, so a "production" deployment
    # without TLS in front of it would otherwise be unable to log in at all —
    # set this to false explicitly for that case (e.g. IP-only, no domain yet)
    # instead of lying about ENV to work around it.
    SESSION_COOKIE_SECURE: bool | None = None

    @field_validator("SESSION_COOKIE_SECURE", mode="before")
    @classmethod
    def _blank_session_cookie_secure_means_unset(cls, v: object) -> object:
        # A stray "SESSION_COOKIE_SECURE=" (no value) in .env would otherwise
        # crash Settings() at startup with a bool-parsing error, taking the
        # whole app down over a blank line rather than just falling back to
        # deriving it from ENV as intended.
        return None if v == "" else v

    @property
    def session_cookie_secure(self) -> bool:
        if self.SESSION_COOKIE_SECURE is not None:
            return self.SESSION_COOKIE_SECURE
        return self.ENV == "production"

    APP_ENCRYPTION_KEY: str = ""  # Fernet key, required to store Ozon credentials

    AI_PROVIDER: Literal["yandex", "demo"] = "demo"
    YANDEX_API_KEY: str = ""
    YANDEX_FOLDER_ID: str = ""
    YANDEX_MODEL: str = "yandexgpt/latest"

    DEMO_MODE: bool = False

    # Automatic Ozon Performance API advertising-statistics sync (see
    # app.services.advertising_daily_sync_service). Defaults reflect Ozon's
    # own field-tested constraints, not arbitrary choices: 10 campaigns per
    # statistics-report request is Ozon's hard limit, and only 1 such report
    # may be in flight per account at a time.
    ADVERTISING_STATS_BATCH_SIZE: int = 10
    ADVERTISING_STATS_POLL_INTERVAL_SECONDS: int = 15
    ADVERTISING_STATS_POLL_TIMEOUT_SECONDS: int = 600
    ADVERTISING_STATS_DEFAULT_LOOKBACK_DAYS: int = 7
    ADVERTISING_STATS_SCHEDULER_ENABLED: bool = True
    ADVERTISING_STATS_SCHEDULER_HOUR_UTC: int = 3  # once a day, off-peak

    CORS_ORIGINS: str = "http://localhost:5173"

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
