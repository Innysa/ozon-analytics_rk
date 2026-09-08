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

    # Automatic Ozon Seller API search-query-details sync (see
    # app.services.search_query_details_sync_service) — replaces the manual
    # "Аналитика → Запросы" XLSX upload with POST
    # /v1/analytics/product-queries/details. limit_by_sku/page_size defaults
    # are Ozon's own confirmed hard maxima (field-tested on a real account:
    # limit_by_sku range (0,15], page_size range (0,100]). SKU_BATCH_SIZE is
    # NOT a confirmed Ozon limit — the real maximum SKUs-per-request is
    # untested (only 1 SKU was tried); this default is a conservative guess
    # sized so a worst case (every SKU maxing out at LIMIT_BY_SKU rows) still
    # fits inside one PAGE_SIZE response without needing pagination, which
    # this client does not yet implement (no confirmed page/page_token
    # field). Revisit once the real per-request SKU cap is confirmed.
    SEARCH_QUERY_STATS_SKU_BATCH_SIZE: int = 6
    SEARCH_QUERY_STATS_LIMIT_BY_SKU: int = 15
    SEARCH_QUERY_STATS_PAGE_SIZE: int = 100
    # CONFIRMED (Ozon Seller's own in-app "База знаний" help widget, on the
    # "Поисковые запросы" analytics page): with a Premium Plus subscription
    # the seller can "Выбрать период для показа статистики — последние 28
    # дней или календарный месяц в течение последнего года" — i.e. the
    # rolling-window option this app uses is capped at 28 days (a specific
    # past calendar month is a separately allowed shape this app doesn't
    # request). This matches the empirical finding below: a 30-day window
    # succeeded, a 92-day one didn't — the true boundary sits at 28.
    SEARCH_QUERY_STATS_DEFAULT_LOOKBACK_DAYS: int = 28
    # Ozon's "getPremiumAnalyticsPeriod" has not finished aggregating the
    # most recent day(s) when this was field-tested: a request with
    # date_to="today" failed live with "InvalidArgument: There is no data
    # for the specified period", while the same account's own confirmed
    # working curl test used date_to 2 days before the day it was run. This
    # default reflects that one observed data point (a 2-day lag was
    # sufficient), not a documented Ozon SLA — raise it if "no data for the
    # specified period" recurs.
    SEARCH_QUERY_STATS_DATA_LAG_DAYS: int = 2
    # Ozon also rejects a date range it considers too long/too far in the
    # past for this endpoint with the SAME "InvalidArgument: There is no
    # data for the specified period" error (confirmed live: a 30-day window
    # ending 2 days ago succeeded — HTTP 200, just happened to have no rows
    # — but an explicit 92-day window ending 7 days ago was rejected
    # outright, an actual validation error with no per-row result at all).
    # This lines up with the 28-day figure now confirmed above
    # (DEFAULT_LOOKBACK_DAYS) — 30 apparently still fits under whatever the
    # real API-enforced boundary is (the UI's documented 28-day preset and
    # the API's own hard limit need not be identical), while 92 clearly
    # doesn't. As a defensive fallback for accounts without Premium Plus
    # (which may have a smaller allowance than 28, undocumented) or in case
    # the real API boundary is tighter than the UI number, the sync also
    # discovers the actual accepted window empirically per run when needed
    # (see search_query_details_sync_service._fetch_with_period_shrink): it
    # keeps date_to fixed and halves the span until Ozon accepts it or this
    # floor is reached. Below this floor a further shrink stops helping much
    # and the error is almost certainly something else, so it's surfaced as-is.
    SEARCH_QUERY_STATS_MIN_PERIOD_DAYS: int = 3
    SEARCH_QUERY_STATS_SCHEDULER_ENABLED: bool = True
    SEARCH_QUERY_STATS_SCHEDULER_HOUR_UTC: int = 4  # once a day, off-peak, staggered after advertising's own job

    # Automatic AI-generated advertising-campaign review (see
    # app.services.advertising_ai_review_service) — analyzes the same daily
    # statistics ADVERTISING_STATS_* above already collects (spend,
    # impressions, clicks, CTR; orders/ДРР deliberately excluded for now,
    # see that module's own docstring) using the same AIProvider as review
    # analysis. LOOKBACK_DAYS is an editorial choice (enough days for a
    # spend/CTR trend to be visible), not an Ozon-side limit like the
    # SEARCH_QUERY_STATS_* ones above.
    ADVERTISING_AI_REVIEW_LOOKBACK_DAYS: int = 14
    ADVERTISING_AI_REVIEW_SCHEDULER_ENABLED: bool = True
    # This app has no job-chaining/orchestration — every scheduled sync is an
    # independent daily cron job (see app.services.advertising_ai_review_
    # scheduler's own docstring). HOUR_UTC:30 is chosen to run after BOTH the
    # 3:00 advertising-stats sync and the 4:00 search-query-stats sync, as a
    # fixed daily slot rather than a literal "run right after" trigger.
    ADVERTISING_AI_REVIEW_SCHEDULER_HOUR_UTC: int = 4

    # Automatic orders sync (app.services.order_daily_sync_service) — Ozon
    # Seller API postings (FBO/FBS), see that module's own docstring for the
    # confirmed contract and its limits.
    ORDER_STATS_DEFAULT_LOOKBACK_DAYS: int = 7
    ORDER_STATS_SCHEDULER_ENABLED: bool = True
    # Runs after the advertising-stats (3:00) and search-query-stats (4:00)
    # jobs, same fixed-daily-slot approximation as those — this app has no
    # job-chaining primitive (see advertising_ai_review_scheduler's own
    # docstring for why "run right after X" is always a fixed time here).
    ORDER_STATS_SCHEDULER_HOUR_UTC: int = 3
    ORDER_STATS_SCHEDULER_MINUTE_UTC: int = 30

    # Store-wide daily dashboard (app.services.dashboard_service) — how many
    # days back the default period covers, compared against the preceding
    # period of the same length. An editorial choice (a month is the usual
    # window a seller/owner checks trends over), not an Ozon-side limit.
    DASHBOARD_DEFAULT_LOOKBACK_DAYS: int = 30

    CORS_ORIGINS: str = "http://localhost:5173"

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
