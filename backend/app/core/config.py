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

    # Full-card AI analysis for ONE product (see app.services.product_card_
    # ai_review_service) — reviews + advertising + order trend together,
    # manual-trigger only (no scheduler — see that module's own docstring
    # for why). LOOKBACK_DAYS is the same kind of editorial choice as
    # ADVERTISING_AI_REVIEW_LOOKBACK_DAYS above, not an Ozon-side limit.
    PRODUCT_CARD_AI_REVIEW_LOOKBACK_DAYS: int = 14

    # Automatic orders sync (app.services.order_daily_sync_service) — Ozon
    # Seller API postings (FBO/FBS), see that module's own docstring for the
    # confirmed contract and its limits. 30 days is an editorial choice (a
    # month of history is what a seller/owner typically wants to see), not an
    # Ozon-side limit — /v2/posting/fbo/list and /v3/posting/fbs/list have no
    # confirmed max date-range per request; volume within the window is
    # already handled by has_next pagination (MAX_PAGES=50, PAGE_LIMIT=1000
    # postings). Every run re-fetches the FULL rolling window from scratch
    # (not just "since last sync"), so raising this value takes effect
    # immediately on the very next run — it backfills the whole new window in
    # one go, it does not grow gradually day by day.
    ORDER_STATS_DEFAULT_LOOKBACK_DAYS: int = 30
    # Splits the lookback window into smaller `since`/`to` chunks per
    # request instead of one call covering the whole window — added
    # 2026-09-11 after a real account's FBO sync (/v2/posting/fbo/list)
    # kept hitting sustained 429s that even an 8-attempt/Retry-After-aware
    # retry (see OzonSellerClient._post()) couldn't reliably ride out, on
    # SOME runs but not others (same account, same window, intermittent —
    # not a confirmed fixed quota). Ozon publishes no confirmed max
    # date-range or request-weight limit for this method, so this number is
    # a reasonable starting point (3-5 days), not a confirmed correct
    # value — if 429s persist even chunked this small, the next step is
    # investigating further with Ozon support, not shrinking this further
    # on a guess. Every chunk still goes through has_next pagination
    # (MAX_PAGES/PAGE_LIMIT) same as before; one chunk failing after
    # retries is recorded in SyncOutcome.errors and does NOT abort the
    # other chunks (each chunk's postings that DID fetch are still used).
    #
    # LOWERED 5 -> 3 (2026-09-22): CONFIRMED on a real account ("Комфорт
    # дом") that a 5-day chunk's true FBO volume had already grown past
    # PAGE_LIMIT=1000 (see _fetch_all_postings's own docstring for the
    # exact evidence) — every 5-day chunk silently lost postings beyond
    # page 1, for weeks, with no error shown (has_next simply never came
    # back true for this store's FBO at that volume). 3 days gives a real
    # margin (this store's own FBO rate was ~225/day at the time =~675 per
    # chunk, ~1/3 headroom below the 1000 cap) while keeping the total
    # chunk count (and therefore 429 exposure) well short of doubling.
    # _fetch_all_postings() now also detects and flags this specific
    # failure mode directly (truncated_chunks/truncated_pagination_note) —
    # this smaller default is a defense-in-depth mitigation on top of
    # that, not a substitute for it; a store growing past even this
    # margin gets a loud, per-chunk error instead of a silent undercount.
    ORDER_STATS_SYNC_CHUNK_DAYS: int = 3
    # Deliberate, ADAPTIVE pause between consecutive chunk requests (2026-09-11,
    # revised same day after a fixed 3s pause still 429'd — on yet another
    # random subset of chunks than either prior run, on the same store with
    # the same chunking: 08-18–08-22 + 08-28–09-01 the first time, then
    # 08-13–08-17 + 08-23–08-27 + 09-02–09-06, then 08-13–08-17 +
    # 08-23–08-27 + 09-07–09-11 — three DIFFERENT sets. A rate quota that a
    # flat 3s pause can still trip confirms it's not about picking the
    # exactly-right flat number; the pause needs to actually RESPOND when
    # it's clearly not enough yet, not sit at a fixed guess.
    #
    # ORDER_STATS_SYNC_CHUNK_PAUSE_SECONDS is now the STARTING pause before
    # any 429 (bumped 3s -> 10s, the low end of what was asked for). Every
    # time a chunk exhausts OzonSellerClient._post()'s own retry budget and
    # still comes back OzonRateLimited, sync_order_daily_statistics()
    # DOUBLES the pause used for every subsequent chunk in that same run
    # (see order_daily_sync_service.py), capped at
    # ORDER_STATS_SYNC_CHUNK_PAUSE_MAX_SECONDS — so a run that keeps
    # hitting 429 keeps backing off harder within itself, rather than
    # retrying at the same losing pace all the way through. Does not
    # persist across separate runs (each new sync starts back at the base
    # pause) — a later run may simply be luckier, which is consistent with
    # this being a rate quota that recovers over time, not a permanent
    # block.
    #
    # If 429s still show up even with this — genuinely no further tuning of
    # these two numbers on a guess. The honest next step at that point is
    # asking Ozon support for the real limit on this method, not another
    # round of adjusting constants blind.
    ORDER_STATS_SYNC_CHUNK_PAUSE_SECONDS: float = 10.0
    ORDER_STATS_SYNC_CHUNK_PAUSE_MAX_SECONDS: float = 60.0
    ORDER_STATS_SCHEDULER_ENABLED: bool = True
    # Moved to 00:00 UTC (confirmed 2026-09-11, explicit seller request) —
    # was 03:30 UTC ("after the advertising-stats/search-query-stats jobs",
    # same fixed-daily-slot approximation those still use), but 03:30 UTC =
    # 06:30 Moscow time left this seller looking at yesterday's numbers for
    # most of their morning in a timezone ahead of Moscow. No longer
    # sequenced after the other 3:00/4:00 UTC jobs — this app has no
    # job-chaining primitive anyway (see advertising_ai_review_scheduler's
    # own docstring), so that ordering was never guaranteed in the first
    # place, just a coincidence of the old fixed times.
    ORDER_STATS_SCHEDULER_HOUR_UTC: int = 0
    ORDER_STATS_SCHEDULER_MINUTE_UTC: int = 0

    # ADDED 2026-09-22 — a SEPARATE, more frequent, NARROW-window sync on top
    # of the full nightly one above (see order_daily_scheduler's own module
    # docstring for the full reasoning): the owner pushed back on "the last
    # day or two catches up eventually" requiring her to wait until the next
    # night or click the manual button herself — she wants it to keep
    # catching up on its own throughout the day, same as everything else in
    # this app. A 3-day window is small enough (one chunk each for FBO/FBS
    # at ORDER_STATS_SYNC_CHUNK_DAYS=5) that running it every 2 hours is a
    # small fraction of the full sync's own request volume, not a repeat of
    # the sustained-429 incident that job's chunking/backoff exists for.
    ORDER_STATS_RECENT_SYNC_ENABLED: bool = True
    ORDER_STATS_RECENT_SYNC_INTERVAL_HOURS: int = 2
    ORDER_STATS_RECENT_SYNC_LOOKBACK_DAYS: int = 3

    # Automatic per-product funnel sync (app.services.product_analytics_daily_
    # sync_service) — Ozon Seller API POST /v1/analytics/data, Premium Plus/Pro
    # only (see that module's own docstring). Unlike orders, this method has
    # NO confirmed pagination-volume cushion — a lookback window times a
    # store's SKU count can exceed the 1000-row page size, so the sync pages
    # through offset when needed (see PRODUCT_ANALYTICS_STATS_MAX_PAGES/
    # _RATE_LIMIT_SLEEP_SECONDS below). Every run re-fetches the FULL rolling
    # window from scratch, same as orders — raising this value backfills the
    # whole new window on the very next run, not gradually.
    # ИЗМЕНЕНО 2026-09-24: было 30 — CONFIRMED as the real cause of a real
    # complaint: the user found «Заказано» on «РНП Товары» simply not
    # updated at all for a specific SKU on several days, and separately
    # asked "нормально получается первые товары обновляются, а которые
    # дальше по списку нет что-ли?" — exactly the symptom of MAX_PAGES
    # truncation (see below): a big-catalog store's (sku, day) row count for
    # a 30-day window can run well past MAX_PAGES*1000, and whichever SKUs
    # Ozon returns LATER in the page order simply never get reached. Lowered
    # to 21 days (three weeks — still covers "this month" for most of it,
    # per «РНП Товары»'s own monthly-planner framing) so a store needs 3x
    # fewer SKUs before hitting the same per-run row cap.
    PRODUCT_ANALYTICS_STATS_DEFAULT_LOOKBACK_DAYS: int = 21
    # Ozon allows at most 1 request/minute to this specific method (confirmed
    # in the official docs — see get_analytics_data()'s own docstring). A
    # window needing more than one page (>1000 sku×day rows) must wait this
    # long between pages, not just retry-on-429. MAX_PAGES bounds how many
    # such 60s waits one store's sync can rack up in a single run — a store
    # whose real (sku, day) count exceeds MAX_PAGES * 1000 for its lookback
    # window gets a documented, non-silent partial result (outcome.errors
    # names how many rows were left unfetched) rather than an unbounded
    # background job.
    # ИЗМЕНЕНО 2026-09-24: было 10 (9 waits = 9 min worst case) — raised
    # alongside the lookback cut above, same real truncation finding. 16
    # pages = 15 waits = ~15 minutes worst case, which still comfortably
    # fits inside the ~20-minute gap between this app's own scheduled nightly
    # attempts (see PRODUCT_ANALYTICS_STATS_SCHEDULER_TIMES_UTC below) without
    # two attempts for the same store overlapping. Combined with the shorter
    # lookback: full per-run coverage now reaches roughly 750 SKUs
    # (16 000 rows / 21 days) instead of ~330 (10 000 / 30) before — a store
    # still past that many active SKUs gets the same documented partial
    # result as before, just at a meaningfully higher ceiling.
    PRODUCT_ANALYTICS_STATS_MAX_PAGES: int = 16
    PRODUCT_ANALYTICS_STATS_RATE_LIMIT_SLEEP_SECONDS: int = 60
    PRODUCT_ANALYTICS_STATS_SCHEDULER_ENABLED: bool = True
    # ИЗМЕНЕНО 2026-09-24: было ОДНО плановое обращение в сутки (03:45 UTC =
    # 06:45 МСК) — пользователь пожаловалась, что «Заказано» на РНП всё
    # ещё было неверным вечером того же дня, и явно попросила: "сделай
    # значить чтобы раза 2-3 обращался к озон запрос для верных данных...
    # надо чтобы примерно в 4 по мск все данные былы подтянуты за вчерашний
    # день". 06:45 МСК — это И позже её дедлайна в ~4 утра, И это было
    # единственной попыткой: если Ozon в этот момент ещё не досчитал
    # вчерашний день (когда именно Ozon завершает расчёт "воронки" за
    # прошедшие сутки — НЕ подтверждено официально), синка просто получала
    # неполные/старые данные и ждала следующих суток. Теперь — несколько
    # плановых попыток подряд рано утром по МСК, все до ~4 утра МСК; каждая
    # повторно перезаписывает (upsert, не добавляет дубли) тем же диапазоном
    # дат, так что более поздняя попытка с более полными данными от Ozon
    # просто перекрывает более раннюю. Формат — "ЧЧ:ММ" по UTC через запятую;
    # 00:15,00:35,00:55 UTC = 03:15,03:35,03:55 МСК.
    PRODUCT_ANALYTICS_STATS_SCHEDULER_TIMES_UTC: str = "00:15,00:35,00:55"

    # Automatic cash-flow-statement sync (app.services.cash_flow_statement_
    # sync_service) — Ozon Seller API POST /v1/finance/cash-flow-statement/
    # list, the confirmed replacement for the now-obsolete /v3/finance/
    # transaction/list (see CashFlowStatementPeriod's own docstring for the
    # full confirmed contract). Ozon returns its OWN fixed ~weekly periods
    # regardless of the requested date.from/date.to — 60 days is sized to
    # reliably cover about 8-9 of those periods per run, not because Ozon
    # documents any particular window limit for this method (none is
    # confirmed either way). No confirmed rate limit for this method (unlike
    # /v1/analytics/data's documented 1/minute) — pagination has no added
    # sleep, relying on the client's normal 429 retry as the only backstop.
    CASH_FLOW_STATEMENT_DEFAULT_LOOKBACK_DAYS: int = 60
    CASH_FLOW_STATEMENT_MAX_PAGES: int = 20
    CASH_FLOW_STATEMENT_SCHEDULER_ENABLED: bool = True
    CASH_FLOW_STATEMENT_SCHEDULER_HOUR_UTC: int = 4
    CASH_FLOW_STATEMENT_SCHEDULER_MINUTE_UTC: int = 15

    # Ozon's official monthly settlement report (POST /v2/finance/realization
    # — see RealizationReportMonth's own docstring). CONFIRMED 2026-09-13:
    # only available for a CLOSED calendar month (a real 404 "Report was not
    # found" for the current, in-progress month) — so this runs daily rather
    # than on a fixed "the month probably closed by now" date, simply
    # skipping any month it already has archived (idempotent) and any month
    # Ozon still says isn't ready. REALIZATION_REPORT_BACKFILL_MONTHS caps
    # how many past closed months it keeps retrying per store per day — 3 is
    # enough to cover a brief outage/deploy gap without unboundedly retrying
    # a month Ozon may simply never produce a report for (e.g. a store with
    # zero sales that month).
    REALIZATION_REPORT_SCHEDULER_ENABLED: bool = True
    REALIZATION_REPORT_SCHEDULER_HOUR_UTC: int = 5
    REALIZATION_REPORT_SCHEDULER_MINUTE_UTC: int = 0
    REALIZATION_REPORT_BACKFILL_MONTHS: int = 3

    # Ozon's true DAILY accrual total (POST /v1/finance/accrual/by-day —
    # see AccrualDailyStatistic's own docstring). CONFIRMED 2026-09-13: the
    # summed total matched Ozon's own cabinet total for the day exactly.
    # Re-syncs (not just fetches once) the last ACCRUAL_DAILY_TRAILING_DAYS
    # days every night — Ozon can revise a recent day's accruals after the
    # fact (e.g. a late return), so only re-fetching keeps them correct.
    ACCRUAL_DAILY_SCHEDULER_ENABLED: bool = True
    ACCRUAL_DAILY_SCHEDULER_HOUR_UTC: int = 3
    ACCRUAL_DAILY_SCHEDULER_MINUTE_UTC: int = 30
    ACCRUAL_DAILY_TRAILING_DAYS: int = 5

    # Automatic product catalog sync (app.services.product_catalog_sync_
    # service) — offer_id/sku/price/stocks + ProductPriceDailySnapshot, via
    # Ozon Seller API /v3/product/list + /v3/product/info/list. Added
    # 2026-09-22: previously only ran from the manual "Синхронизировать с
    # Ozon" button — every other sync in this app already runs automatically,
    # and the store owner expected this one to as well. Scheduled at 23:30
    # UTC, just before ORDER_STATS_SCHEDULER_HOUR_UTC (00:00 UTC), so that
    # day's ProductPriceDailySnapshot already exists by the time the order
    # sync looks up that day's own seller price (falls back to the flat
    # current price otherwise — see order_daily_sync_service's own docstring
    # — so this ordering is a freshness nicety, not a hard dependency).
    PRODUCT_CATALOG_SCHEDULER_ENABLED: bool = True
    PRODUCT_CATALOG_SCHEDULER_HOUR_UTC: int = 23
    PRODUCT_CATALOG_SCHEDULER_MINUTE_UTC: int = 30

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
