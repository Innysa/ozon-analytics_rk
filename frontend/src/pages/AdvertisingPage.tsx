import { ChangeEvent, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { useStore } from "../store/StoreContext";
import type {
  AdvertisingAiReview,
  AdvertisingAiReviewInsight,
  AdvertisingAiReviewListResponse,
  AdvertisingAnalytics,
  AdvertisingCampaign,
  AdvertisingDailyStatistic,
  AdvertisingStatistic,
  CampaignDetail,
  ImportSummary,
  MetricComparison,
  PerformanceCredentialsStatus,
  SyncRun,
} from "../types";

// Ozon campaign_type values that are referral/blogger promotion, not regular
// paid advertising — kept separate per user request, verified against real
// campaign_type values already seen in this store's own data.
const REFERRAL_CAMPAIGN_TYPES = new Set(["REF_VK", "REF_BLOGGER"]);
const ARCHIVED_STATE = "CAMPAIGN_STATE_ARCHIVED";

function fmtRub(v: number | null): string {
  if (v === null) return "Нет данных";
  return `${v.toLocaleString("ru-RU", { maximumFractionDigits: 2 })} ₽`;
}

function fmtPct(v: number | null): string {
  if (v === null) return "Нет данных";
  return `${v.toLocaleString("ru-RU", { maximumFractionDigits: 2 })}%`;
}

function isoDate(d: Date): string {
  return d.toISOString().slice(0, 10);
}

function defaultStatsDateFrom(): string {
  const d = new Date();
  d.setDate(d.getDate() - 6);
  return isoDate(d);
}

function defaultStatsDateTo(): string {
  return isoDate(new Date());
}


export function AdvertisingPage() {
  const { currentStore } = useStore();
  const [campaigns, setCampaigns] = useState<AdvertisingCampaign[] | null>(null);
  const [perfStatus, setPerfStatus] = useState<PerformanceCredentialsStatus | null>(null);
  const [analytics, setAnalytics] = useState<AdvertisingAnalytics | null>(null);
  const [statistics, setStatistics] = useState<AdvertisingStatistic[]>([]);
  const [aiReviews, setAiReviews] = useState<AdvertisingAiReview[] | null>(null);
  const [generatingAiReview, setGeneratingAiReview] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [syncingStats, setSyncingStats] = useState(false);
  const [statsDateFrom, setStatsDateFrom] = useState(defaultStatsDateFrom);
  const [statsDateTo, setStatsDateTo] = useState(defaultStatsDateTo);

  const load = () => {
    if (!currentStore) return;
    api.get<AdvertisingCampaign[]>(`/stores/${currentStore.id}/advertising/campaigns`).then(setCampaigns);
    api.get<PerformanceCredentialsStatus>(`/stores/${currentStore.id}/ozon/performance/credentials`).then(setPerfStatus);
    api.get<AdvertisingAnalytics>(`/stores/${currentStore.id}/advertising/analytics`).then(setAnalytics);
    api
      .get<{ items: AdvertisingStatistic[]; total: number }>(`/stores/${currentStore.id}/advertising/statistics`)
      .then((d) => setStatistics(d.items.slice(0, 50)));
    api
      .get<AdvertisingAiReviewListResponse>(`/stores/${currentStore.id}/advertising/ai-review`)
      .then((d) => setAiReviews(d.items));
  };

  useEffect(load, [currentStore]);

  if (!currentStore) return null;

  const syncCampaigns = async () => {
    setSyncing(true);
    setNotice("Синхронизация кампаний с Ozon Performance API...");
    try {
      const run = await api.post<{ status: string; error_message: string | null; items_created: number; items_skipped_duplicate: number }>(
        `/stores/${currentStore.id}/sync/ozon-advertising`
      );
      if (run.status === "failed") {
        setNotice(`Синхронизация не удалась: ${run.error_message}`);
      } else {
        setNotice(`Готово: новых кампаний ${run.items_created}, обновлено ${run.items_skipped_duplicate}.`);
      }
      load();
    } catch (err) {
      setNotice(err instanceof ApiError ? err.message : "Ошибка синхронизации");
    } finally {
      setSyncing(false);
    }
  };

  const pollSyncRun = async (runId: string, attempt = 0): Promise<SyncRun | null> => {
    if (!currentStore) return null;
    const runs = await api.get<SyncRun[]>(`/stores/${currentStore.id}/sync/runs`);
    const run = runs.find((r) => r.id === runId) ?? null;
    if (!run || run.status !== "running" || attempt >= 60) return run;
    // The sync runs in the background and can take several minutes for many
    // active campaigns (Ozon allows only 1 statistics report in flight per
    // account, so campaigns are processed in sequential batches) — poll
    // every 5s rather than assuming it finishes instantly.
    await new Promise((resolve) => setTimeout(resolve, 5000));
    return pollSyncRun(runId, attempt + 1);
  };

  const syncDailyStatistics = async () => {
    if (!currentStore) return;
    if (statsDateFrom > statsDateTo) {
      setNotice("Дата «от» не может быть позже даты «до».");
      return;
    }
    setSyncingStats(true);
    setNotice("Запуск автосбора статистики рекламы через Ozon Performance API...");
    try {
      const query = new URLSearchParams({ date_from: statsDateFrom, date_to: statsDateTo }).toString();
      const run = await api.post<SyncRun>(`/stores/${currentStore.id}/sync/ozon-advertising-statistics?${query}`);
      setNotice("Сбор статистики выполняется в фоне — это может занять несколько минут...");
      const finished = await pollSyncRun(run.id);
      if (!finished) {
        setNotice("Не удалось получить статус синхронизации — обновите страницу и проверьте журнал синхронизаций.");
      } else if (finished.status === "failed") {
        setNotice(`Автосбор статистики не удался: ${finished.error_message ?? "неизвестная ошибка"}`);
      } else if (finished.status === "running") {
        setNotice("Синхронизация всё ещё выполняется — проверьте журнал синхронизаций на странице «Подключение к Ozon» позже.");
      } else {
        setNotice(
          `Готово: получено ${finished.items_fetched}, создано ${finished.items_created}, обновлено ${finished.items_skipped_duplicate}.` +
            (finished.error_message ? ` Предупреждения: ${finished.error_message}` : "")
        );
      }
      load();
    } catch (err) {
      setNotice(err instanceof ApiError ? err.message : "Ошибка автосбора статистики");
    } finally {
      setSyncingStats(false);
    }
  };

  const uploadStatistics = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setNotice("Загрузка отчёта...");
    try {
      const result = await api.upload<ImportSummary>(`/stores/${currentStore.id}/advertising/statistics/upload`, file);
      setNotice(
        `Загружено: получено ${result.fetched}, создано ${result.created}, дублей пропущено ${result.skipped_duplicate}` +
          (result.errors.length ? `. Примечания: ${result.errors.slice(0, 3).join("; ")}` : "")
      );
      load();
    } catch (err) {
      setNotice(err instanceof ApiError ? err.message : "Ошибка загрузки файла");
    }
  };

  const generateAiReview = async () => {
    if (!currentStore) return;
    setGeneratingAiReview(true);
    setNotice("Формирование AI-обзора рекламных кампаний...");
    try {
      await api.post<AdvertisingAiReview>(`/stores/${currentStore.id}/advertising/ai-review/generate`);
      setNotice("AI-обзор сформирован.");
      load();
    } catch (err) {
      setNotice(err instanceof ApiError ? err.message : "Ошибка формирования AI-обзора");
    } finally {
      setGeneratingAiReview(false);
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-lg font-semibold text-slate-800">Реклама — {currentStore.name}</h1>
        <div className="flex flex-wrap gap-2">
          <label className="cursor-pointer rounded-md bg-slate-100 px-3 py-1.5 text-xs font-medium hover:bg-slate-200">
            Загрузить статистику (CSV/XLSX)
            <input type="file" accept=".csv,.xlsx" className="hidden" onChange={uploadStatistics} />
          </label>
          <button
            onClick={syncCampaigns}
            disabled={syncing || !perfStatus?.configured}
            className="rounded-md bg-slate-100 px-3 py-1.5 text-xs font-medium hover:bg-slate-200 disabled:opacity-50"
          >
            {syncing ? "Синхронизация..." : "Синхронизировать кампании"}
          </button>
          <div className="flex items-center gap-1 rounded-md border border-slate-200 bg-white px-2 py-1">
            <input
              type="date"
              value={statsDateFrom}
              onChange={(e) => setStatsDateFrom(e.target.value)}
              max={statsDateTo}
              className="text-xs text-slate-600 outline-none"
              aria-label="Период автосбора: от"
            />
            <span className="text-xs text-slate-400">—</span>
            <input
              type="date"
              value={statsDateTo}
              onChange={(e) => setStatsDateTo(e.target.value)}
              min={statsDateFrom}
              className="text-xs text-slate-600 outline-none"
              aria-label="Период автосбора: до"
            />
          </div>
          <button
            onClick={syncDailyStatistics}
            disabled={syncingStats || !perfStatus?.configured}
            className="rounded-md bg-indigo-100 px-3 py-1.5 text-xs font-medium text-indigo-700 hover:bg-indigo-200 disabled:opacity-50"
            title="Автоматически собрать клики, показы, расход и другие показатели через Ozon Performance API за выбранный период — без ручной загрузки CSV"
          >
            {syncingStats ? "Сбор статистики..." : "Обновить статистику (авто)"}
          </button>
          <button
            onClick={generateAiReview}
            disabled={generatingAiReview}
            className="rounded-md bg-violet-100 px-3 py-1.5 text-xs font-medium text-violet-700 hover:bg-violet-200 disabled:opacity-50"
            title="Сформировать AI-обзор рекламных кампаний по уже собранной статистике (расход, показы, клики, CTR) — не требует новой синхронизации"
          >
            {generatingAiReview ? "Формирование обзора..." : "Обновить AI-обзор"}
          </button>
        </div>
      </div>

      {!perfStatus?.configured && (
        <div className="rounded-md border border-amber-300 bg-amber-50 p-4 text-sm text-amber-800">
          Ключи Ozon Performance API не заданы — синхронизация списка кампаний недоступна. Перейдите в{" "}
          <Link to="/ozon-settings" className="underline">
            «Подключение к Ozon»
          </Link>
          , чтобы их указать. Загрузка статистики (кнопка выше) работает независимо от этого — файл экспортируется
          прямо из личного кабинета Ozon: «Продвижение → Статистика → Скачать отчёт».
        </div>
      )}

      {notice && <div className="rounded-md bg-slate-50 p-2 text-xs text-slate-600">{notice}</div>}

      <AdvertisingAiReviewSection reviews={aiReviews} />

      <div className="rounded-md border border-dashed border-slate-300 bg-white p-4 text-sm text-slate-500">
        ДРР и ROAS ниже рассчитаны этим приложением (расход / продажи и продажи / расход, суммарно по загруженным
        строкам) — они отделены от процентов ДРР, которые Ozon указывает в самом отчёте построчно (их нельзя корректно
        усреднять при агрегации). Данные загружаются из официального экспорта Ozon за выбранный период — если нужна
        именно ежедневная разбивка, загружайте отдельный отчёт за каждый день.
      </div>

      {analytics && analytics.has_data ? (
        <>
          <div className="text-xs text-slate-500">
            Период данных: {analytics.period_start} — {analytics.period_end}
          </div>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Stat label="Расход (факт)" value={fmtRub(analytics.total_spend_rub)} />
            <Stat label="Продажи в продвижении (факт)" value={fmtRub(analytics.total_sales_promo_rub)} />
            <Stat label="ДРР (рассчитано)" value={fmtPct(analytics.drr_calculated_pct)} />
            <Stat label="ROAS (рассчитано)" value={analytics.roas_calculated !== null ? `×${analytics.roas_calculated.toLocaleString("ru-RU")}` : "Нет данных"} />
            <Stat label="Показы (факт)" value={analytics.total_impressions.toLocaleString("ru-RU")} />
            <Stat label="Клики (факт)" value={analytics.total_clicks.toLocaleString("ru-RU")} />
            <Stat label="CTR (рассчитано)" value={fmtPct(analytics.ctr_calculated_pct)} />
            <Stat label="Средний CPC (рассчитано)" value={fmtRub(analytics.avg_cpc_calculated_rub)} />
          </div>

          <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
            <div>
              <h3 className="mb-2 text-sm font-semibold text-slate-700">По кампаниям (топ по расходу)</h3>
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="border-b border-slate-200 text-xs text-slate-500">
                    <th className="py-1">Кампания</th>
                    <th>Расход</th>
                    <th>ДРР</th>
                    <th>ROAS</th>
                  </tr>
                </thead>
                <tbody>
                  {analytics.by_campaign.map((c) => (
                    <tr key={c.campaign_id} className="border-b border-slate-100">
                      <td className="py-1">{c.campaign_name}</td>
                      <td>{fmtRub(c.spend_rub)}</td>
                      <td>{fmtPct(c.drr_calculated_pct)}</td>
                      <td>{c.roas_calculated !== null ? `×${c.roas_calculated}` : "Нет данных"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div>
              <h3 className="mb-2 text-sm font-semibold text-slate-700">По товарам (топ по расходу)</h3>
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="border-b border-slate-200 text-xs text-slate-500">
                    <th className="py-1">Товар</th>
                    <th>Расход</th>
                    <th>ДРР</th>
                    <th>ROAS</th>
                  </tr>
                </thead>
                <tbody>
                  {analytics.by_product.map((p) => (
                    <tr key={p.product_id} className="border-b border-slate-100">
                      <td className="py-1">{p.product_name}</td>
                      <td>{fmtRub(p.spend_rub)}</td>
                      <td>{fmtPct(p.drr_calculated_pct)}</td>
                      <td>{p.roas_calculated !== null ? `×${p.roas_calculated}` : "Нет данных"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      ) : (
        <div className="rounded-md border border-slate-200 bg-white p-6 text-center text-slate-500">
          Нет данных. Загрузите отчёт «Продвижение → Статистика» из личного кабинета Ozon (CSV/XLSX).
        </div>
      )}

      {statistics.length > 0 && (
        <div>
          <h3 className="mb-2 text-sm font-semibold text-slate-700">Строки отчёта (последние загруженные, до 50)</h3>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-slate-200 text-xs text-slate-500">
                  <th className="py-1">Товар</th>
                  <th>Кампания</th>
                  <th>Период</th>
                  <th>Расход</th>
                  <th>Продажи</th>
                  <th>ДРР (Ozon)</th>
                  <th>ДРР (расчёт)</th>
                  <th>ROAS (расчёт)</th>
                </tr>
              </thead>
              <tbody>
                {statistics.map((s) => (
                  <tr key={s.id} className="border-b border-slate-100">
                    <td className="py-1">{s.product_name ?? s.product_sku}</td>
                    <td>{s.campaign_name ?? s.ozon_campaign_id}</td>
                    <td>
                      {s.period_start} — {s.period_end}
                    </td>
                    <td>{fmtRub(s.spend_rub)}</td>
                    <td>{fmtRub(s.sales_promo_rub)}</td>
                    <td>{fmtPct(s.drr_promo_pct_ozon)}</td>
                    <td>{fmtPct(s.drr_calculated_pct)}</td>
                    <td>{s.roas_calculated !== null ? `×${s.roas_calculated}` : "Нет данных"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {campaigns === null ? (
        <div className="text-slate-500">Загрузка кампаний...</div>
      ) : (
        <CampaignsSection storeId={currentStore.id} campaigns={campaigns} />
      )}
    </div>
  );
}

const AI_ASSESSMENT_STYLES: Record<AdvertisingAiReviewInsight["assessment"], string> = {
  strong: "bg-green-100 text-green-700",
  weak: "bg-red-100 text-red-700",
  neutral: "bg-slate-100 text-slate-600",
};
const AI_ASSESSMENT_LABELS: Record<AdvertisingAiReviewInsight["assessment"], string> = {
  strong: "Сильная",
  weak: "Слабая",
  neutral: "Нейтральная",
};

function AdvertisingAiReviewSection({ reviews }: { reviews: AdvertisingAiReview[] | null }) {
  const [showHistory, setShowHistory] = useState(false);

  if (reviews === null) {
    return <div className="text-sm text-slate-500">Загрузка AI-обзора...</div>;
  }
  if (reviews.length === 0) {
    return (
      <div className="rounded-md border border-dashed border-violet-300 bg-violet-50 p-4 text-sm text-violet-800">
        AI-обзор рекламных кампаний ещё не сформирован. Он собирается автоматически раз в день (после сбора статистики
        рекламы), либо нажмите «Обновить AI-обзор» выше — но сначала должна быть собрана статистика рекламы (кнопка
        «Обновить статистику (авто)» или загрузка CSV/XLSX).
      </div>
    );
  }

  const [latest, ...history] = reviews;

  return (
    <div className="space-y-3 rounded-md border border-violet-200 bg-violet-50/40 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-violet-800">AI-обзор рекламных кампаний</h3>
        <span className="text-xs text-slate-500">
          Период: {latest.period_start} — {latest.period_end}
          {latest.model_used ? ` · модель: ${latest.model_used}` : ""}
        </span>
      </div>
      <p className="text-sm text-slate-700">{latest.overview}</p>

      {latest.insights.length > 0 && (
        <div>
          <h4 className="mb-1 text-xs font-semibold text-slate-600">По кампаниям</h4>
          <ul className="space-y-1 text-sm">
            {latest.insights.map((i) => (
              <li key={i.ozon_campaign_id} className="flex items-start gap-2">
                <span className={`shrink-0 rounded px-1.5 py-0.5 text-xs font-medium ${AI_ASSESSMENT_STYLES[i.assessment]}`}>
                  {AI_ASSESSMENT_LABELS[i.assessment]}
                </span>
                <span>
                  <strong>{i.campaign_name}</strong>
                  {i.note ? ` — ${i.note}` : ""}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {latest.anomalies.length > 0 && (
        <div>
          <h4 className="mb-1 text-xs font-semibold text-slate-600">Аномалии и тренды</h4>
          <ul className="list-disc space-y-0.5 pl-4 text-sm text-slate-700">
            {latest.anomalies.map((a, idx) => (
              <li key={idx}>{a}</li>
            ))}
          </ul>
        </div>
      )}

      {latest.recommendations.length > 0 && (
        <div>
          <h4 className="mb-1 text-xs font-semibold text-slate-600">Рекомендации</h4>
          <ul className="list-disc space-y-0.5 pl-4 text-sm text-slate-700">
            {latest.recommendations.map((r, idx) => (
              <li key={idx}>{r}</li>
            ))}
          </ul>
        </div>
      )}

      {history.length > 0 && (
        <div className="pt-1">
          <button onClick={() => setShowHistory((v) => !v)} className="text-xs font-medium text-violet-700 underline">
            {showHistory ? "Скрыть историю обзоров" : `Показать историю обзоров (${history.length})`}
          </button>
          {showHistory && (
            <ul className="mt-2 space-y-2 border-t border-violet-200 pt-2">
              {history.map((r) => (
                <li key={r.id} className="text-xs text-slate-600">
                  <span className="font-medium text-slate-700">
                    {r.period_start} — {r.period_end}:
                  </span>{" "}
                  {r.overview}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

function CampaignsSection({ storeId, campaigns }: { storeId: string; campaigns: AdvertisingCampaign[] }) {
  const [tab, setTab] = useState<"active" | "referral" | "archived">("active");

  const isReferral = (c: AdvertisingCampaign) => !!c.campaign_type && REFERRAL_CAMPAIGN_TYPES.has(c.campaign_type);
  const referral = campaigns.filter(isReferral);
  const archived = campaigns.filter((c) => !isReferral(c) && c.state === ARCHIVED_STATE);
  const active = campaigns.filter((c) => !isReferral(c) && c.state !== ARCHIVED_STATE);
  const shown = tab === "active" ? active : tab === "referral" ? referral : archived;

  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-slate-700">Кампании (метаданные из Ozon Performance API)</h3>
        <div className="flex gap-1">
          <TabButton active={tab === "active"} onClick={() => setTab("active")} label={`Активные (${active.length})`} />
          <TabButton
            active={tab === "referral"}
            onClick={() => setTab("referral")}
            label={`Рефералка/блогеры (${referral.length})`}
          />
          <TabButton active={tab === "archived"} onClick={() => setTab("archived")} label={`Архив (${archived.length})`} />
        </div>
      </div>

      {campaigns.length === 0 ? (
        <div className="text-slate-500">Нет данных. Синхронизируйте кампании, чтобы увидеть список.</div>
      ) : shown.length === 0 ? (
        <div className="text-slate-500">Нет кампаний в этой группе.</div>
      ) : (
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="border-b border-slate-200 text-xs text-slate-500">
              <th className="py-1" />
              <th className="py-1">Название</th>
              <th>Тип</th>
              <th>Статус</th>
              <th>Дневной бюджет</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((c) => (
              <CampaignRow key={c.id} storeId={storeId} campaign={c} />
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function TabButton({ active, onClick, label }: { active: boolean; onClick: () => void; label: string }) {
  return (
    <button
      onClick={onClick}
      className={`rounded-md px-2.5 py-1 text-xs font-medium ${
        active ? "bg-indigo-100 text-indigo-700" : "bg-slate-100 text-slate-600 hover:bg-slate-200"
      }`}
    >
      {label}
    </button>
  );
}

interface CampaignAutoDayRow {
  date: string;
  impressions: number;
  clicks: number;
  ctrPct: number | null;
  spendRub: number;
  orders: number;
  revenueRub: number;
  drrPct: number | null;
}

// The campaign-detail endpoint's auto_daily only carries totals + a
// last-two-days comparison; the actual per-day breakdown (what Ozon's own
// cabinet shows for a campaign) comes from the already-existing
// /advertising/daily-statistics listing, filtered to this campaign, summed
// across its SKUs per date — a campaign row here should read like Ozon's own
// day-by-day table, not just a single total.
function aggregateAutoDailyByDate(rows: AdvertisingDailyStatistic[]): CampaignAutoDayRow[] {
  const byDate = new Map<string, Omit<CampaignAutoDayRow, "ctrPct" | "drrPct">>();
  for (const r of rows) {
    const acc = byDate.get(r.date) ?? { date: r.date, impressions: 0, clicks: 0, spendRub: 0, orders: 0, revenueRub: 0 };
    acc.impressions += r.impressions ?? 0;
    acc.clicks += r.clicks ?? 0;
    acc.spendRub += r.spend_rub ?? 0;
    acc.orders += r.orders ?? 0;
    acc.revenueRub += r.revenue_rub ?? 0;
    byDate.set(r.date, acc);
  }
  return [...byDate.values()]
    .map((acc) => ({
      ...acc,
      ctrPct: acc.impressions > 0 ? (acc.clicks / acc.impressions) * 100 : null,
      drrPct: acc.revenueRub > 0 ? (acc.spendRub / acc.revenueRub) * 100 : null,
    }))
    .sort((a, b) => (a.date < b.date ? 1 : -1));
}

// --- Anomaly highlighting for the per-day auto-collected table -------------
// "Anomaly" here means a metric moved sharply vs. the previous day (or, when
// there's no previous day / the move vs. it is small, vs. the campaign's own
// average) — day-to-day noise on small numbers is deliberately not flagged
// (minBaseline) so a jump from 1 to 3 clicks doesn't light up as "+200%".

type MetricKey = "impressions" | "clicks" | "ctrPct" | "spendRub" | "orders" | "revenueRub" | "drrPct";

const DEVIATION_THRESHOLD_PCT = 40;

interface MetricConfig {
  minBaseline: number;
  // Which direction of a large move is "good" for this metric — colors the
  // highlight green when the move matches it, red when it doesn't. null
  // means neither direction is inherently good/bad (just worth noting).
  goodDirection: "up" | "down" | null;
}

const METRIC_CONFIG: Record<MetricKey, MetricConfig> = {
  impressions: { minBaseline: 50, goodDirection: null },
  clicks: { minBaseline: 3, goodDirection: "up" },
  ctrPct: { minBaseline: 0.5, goodDirection: "up" },
  spendRub: { minBaseline: 20, goodDirection: "down" },
  orders: { minBaseline: 1, goodDirection: "up" },
  revenueRub: { minBaseline: 50, goodDirection: "up" },
  drrPct: { minBaseline: 1, goodDirection: "down" },
};

interface Deviation {
  direction: "up" | "down";
  label: string;
}

function deviationAgainst(value: number, baseline: number | null, baselineLabel: string, minBaseline: number): Deviation | null {
  if (baseline === null) return null;
  if (baseline < minBaseline) {
    // Baseline itself is ~zero — a plain percentage would be meaningless
    // (or a divide-by-zero); only flag a clear appearance-from-nothing.
    if (baseline === 0 && value >= minBaseline) {
      return { direction: "up", label: `рост с нуля (${baselineLabel})` };
    }
    return null;
  }
  const pct = ((value - baseline) / baseline) * 100;
  if (Math.abs(pct) < DEVIATION_THRESHOLD_PCT) return null;
  const sign = pct > 0 ? "+" : "";
  return { direction: pct > 0 ? "up" : "down", label: `${sign}${Math.round(pct)}% ${baselineLabel}` };
}

function classifyMetric(metric: MetricKey, value: number | null, previous: number | null, avg: number | null): Deviation | null {
  if (value === null) return null;
  const { minBaseline } = METRIC_CONFIG[metric];
  const devPrev = previous === null ? null : deviationAgainst(value, previous, "к пред. дню", minBaseline);
  return devPrev ?? deviationAgainst(value, avg, "к среднему по кампании", minBaseline);
}

function average(values: number[]): number | null {
  return values.length > 0 ? values.reduce((a, b) => a + b, 0) / values.length : null;
}

interface AnnotatedDayRow extends CampaignAutoDayRow {
  deviations: Partial<Record<MetricKey, Deviation>>;
}

const ALL_METRICS: MetricKey[] = ["impressions", "clicks", "ctrPct", "spendRub", "orders", "revenueRub", "drrPct"];

function annotateWithAnomalies(rowsDesc: CampaignAutoDayRow[]): AnnotatedDayRow[] {
  const rowsAsc = [...rowsDesc].sort((a, b) => (a.date < b.date ? -1 : 1));
  const averages = Object.fromEntries(
    ALL_METRICS.map((m) => [m, average(rowsAsc.map((r) => r[m]).filter((v): v is number => v !== null))])
  ) as Record<MetricKey, number | null>;

  const annotatedAsc: AnnotatedDayRow[] = rowsAsc.map((row, i) => {
    const previousRow = i > 0 ? rowsAsc[i - 1] : null;
    const deviations: Partial<Record<MetricKey, Deviation>> = {};
    for (const m of ALL_METRICS) {
      const dev = classifyMetric(m, row[m], previousRow ? previousRow[m] : null, averages[m]);
      if (dev) deviations[m] = dev;
    }
    // The specific pattern called out explicitly: spend jumped but clicks
    // didn't follow — make that visible in the tooltip, not just "spend up".
    if (deviations.spendRub?.direction === "up" && deviations.clicks?.direction !== "up") {
      deviations.spendRub = { ...deviations.spendRub, label: `${deviations.spendRub.label}, без роста кликов` };
    }
    return { ...row, deviations };
  });

  const byDate = new Map(annotatedAsc.map((r) => [r.date, r]));
  return rowsDesc.map((r) => byDate.get(r.date)!);
}

function anomalyCellClass(direction: "up" | "down", goodDirection: "up" | "down" | null): string {
  if (goodDirection === null) return "bg-amber-50 text-amber-700";
  return direction === goodDirection ? "bg-green-50 text-green-700" : "bg-red-50 text-red-700";
}

function AnomalyCell({ value, deviation, goodDirection }: { value: string; deviation: Deviation | undefined; goodDirection: "up" | "down" | null }) {
  if (!deviation) return <td>{value}</td>;
  const arrow = deviation.direction === "up" ? "▲" : "▼";
  return (
    <td className={`rounded px-1 ${anomalyCellClass(deviation.direction, goodDirection)}`} title={deviation.label}>
      {value} {arrow}
    </td>
  );
}

function CampaignRow({ storeId, campaign }: { storeId: string; campaign: AdvertisingCampaign }) {
  const [expanded, setExpanded] = useState(false);
  const [detail, setDetail] = useState<CampaignDetail | null>(null);
  const [autoDailyRows, setAutoDailyRows] = useState<AdvertisingDailyStatistic[] | null>(null);
  const [loading, setLoading] = useState(false);

  const toggle = () => {
    const next = !expanded;
    setExpanded(next);
    if (next && detail === null && !loading) {
      setLoading(true);
      Promise.all([
        api.get<CampaignDetail>(`/stores/${storeId}/advertising/campaigns/${campaign.id}/detail`),
        api.get<{ items: AdvertisingDailyStatistic[]; total: number }>(
          `/stores/${storeId}/advertising/daily-statistics?campaign_id=${campaign.id}`
        ),
      ])
        .then(([detailResult, dailyResult]) => {
          setDetail(detailResult);
          setAutoDailyRows(dailyResult.items);
        })
        .finally(() => setLoading(false));
    }
  };

  return (
    <>
      <tr onClick={toggle} className="cursor-pointer border-b border-slate-100 hover:bg-slate-50">
        <td className="w-4 py-1 text-slate-400">{expanded ? "▾" : "▸"}</td>
        <td className="py-1">{campaign.name ?? "Без названия"}</td>
        <td>{campaign.campaign_type ?? "—"}</td>
        <td>{campaign.state ?? "—"}</td>
        <td>{campaign.daily_budget_rub !== null ? fmtRub(campaign.daily_budget_rub) : "Нет данных"}</td>
      </tr>
      {expanded && (
        <tr className="border-b border-slate-100 bg-slate-50">
          <td colSpan={5} className="p-3">
            {loading || !detail ? (
              <div className="text-slate-500">Загрузка...</div>
            ) : !detail.has_data && !detail.auto_daily.has_data ? (
              <div className="text-slate-500">
                Нет данных для этой кампании — ни загруженных вручную (CSV/XLSX «Продвижение → Статистика»), ни
                автоматически собранных через Performance API (кнопка «Обновить статистику (авто)» выше).
              </div>
            ) : (
              <div className="space-y-4">
                {detail.has_data && (
                  <div className="space-y-3">
                    <h4 className="text-xs font-semibold text-slate-700">Загружено вручную (CSV/XLSX)</h4>
                    <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                      <Stat label="Расход (факт)" value={fmtRub(detail.total_spend_rub)} />
                      <Stat label="Показы (факт)" value={detail.total_impressions.toLocaleString("ru-RU")} />
                      <Stat label="Клики (факт)" value={detail.total_clicks.toLocaleString("ru-RU")} />
                      <Stat label="Продажи (факт)" value={fmtRub(detail.total_sales_promo_rub)} />
                      <Stat label="ДРР (рассчитано)" value={fmtPct(detail.drr_calculated_pct)} />
                      <Stat
                        label="ROAS (рассчитано)"
                        value={detail.roas_calculated !== null ? `×${detail.roas_calculated}` : "Нет данных"}
                      />
                    </div>
                    <div className="text-xs text-slate-500">
                      Период всех загруженных данных: {detail.period_start} — {detail.period_end}
                    </div>

                    {detail.daily_comparison ? (
                      <div>
                        <h5 className="mb-1 text-xs font-semibold text-slate-600">
                          Сравнение {detail.daily_comparison.date_today} с {detail.daily_comparison.date_yesterday}
                        </h5>
                        <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
                          <ComparisonStat label="Расход" comparison={detail.daily_comparison.spend_rub} format={fmtRub} />
                          <ComparisonStat
                            label="Показы"
                            comparison={detail.daily_comparison.impressions}
                            format={(v) => v.toLocaleString("ru-RU")}
                          />
                          <ComparisonStat
                            label="Клики"
                            comparison={detail.daily_comparison.clicks}
                            format={(v) => v.toLocaleString("ru-RU")}
                          />
                          <ComparisonStat label="Продажи" comparison={detail.daily_comparison.sales_promo_rub} format={fmtRub} />
                        </div>
                      </div>
                    ) : (
                      <div className="text-xs italic text-slate-500">{detail.daily_comparison_unavailable_reason}</div>
                    )}
                  </div>
                )}

                {detail.auto_daily.has_data && (
                  <div className={`space-y-3 ${detail.has_data ? "border-t border-slate-200 pt-3" : ""}`}>
                    <h4 className="text-xs font-semibold text-slate-700">
                      Автоматически собрано (Ozon Performance API, по дням)
                    </h4>
                    <div className="text-xs text-slate-500">
                      Период автосбора: {detail.auto_daily.period_start} — {detail.auto_daily.period_end}
                    </div>

                    <CampaignAutoDailyTable rows={autoDailyRows ?? []} />
                  </div>
                )}
              </div>
            )}
          </td>
        </tr>
      )}
    </>
  );
}

function CampaignAutoDailyTable({ rows }: { rows: AdvertisingDailyStatistic[] }) {
  const byDate = aggregateAutoDailyByDate(rows);
  if (byDate.length === 0) {
    return <div className="text-xs text-slate-500">Нет данных по дням.</div>;
  }
  const annotated = annotateWithAnomalies(byDate);
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="border-b border-slate-200 text-xs text-slate-500">
            <th className="py-1">Дата</th>
            <th>Показы</th>
            <th>Клики</th>
            <th>CTR</th>
            <th>Расход</th>
            <th>Заказы</th>
            <th>Выручка</th>
            <th>ДРР</th>
          </tr>
        </thead>
        <tbody>
          {annotated.map((d) => (
            <tr key={d.date} className="border-b border-slate-100">
              <td className="py-1">{d.date}</td>
              <AnomalyCell
                value={d.impressions.toLocaleString("ru-RU")}
                deviation={d.deviations.impressions}
                goodDirection={METRIC_CONFIG.impressions.goodDirection}
              />
              <AnomalyCell
                value={d.clicks.toLocaleString("ru-RU")}
                deviation={d.deviations.clicks}
                goodDirection={METRIC_CONFIG.clicks.goodDirection}
              />
              <AnomalyCell value={fmtPct(d.ctrPct)} deviation={d.deviations.ctrPct} goodDirection={METRIC_CONFIG.ctrPct.goodDirection} />
              <AnomalyCell
                value={fmtRub(d.spendRub)}
                deviation={d.deviations.spendRub}
                goodDirection={METRIC_CONFIG.spendRub.goodDirection}
              />
              <AnomalyCell
                value={d.orders.toLocaleString("ru-RU")}
                deviation={d.deviations.orders}
                goodDirection={METRIC_CONFIG.orders.goodDirection}
              />
              <AnomalyCell
                value={fmtRub(d.revenueRub)}
                deviation={d.deviations.revenueRub}
                goodDirection={METRIC_CONFIG.revenueRub.goodDirection}
              />
              <AnomalyCell value={fmtPct(d.drrPct)} deviation={d.deviations.drrPct} goodDirection={METRIC_CONFIG.drrPct.goodDirection} />
            </tr>
          ))}
        </tbody>
      </table>
      <div className="mt-1 text-xs text-slate-400">
        Подсветка = отклонение ≥ {DEVIATION_THRESHOLD_PCT}% к предыдущему дню (или к среднему по кампании) — наведите
        на значение, чтобы увидеть, насколько именно.
      </div>
    </div>
  );
}

function ComparisonStat({
  label,
  comparison,
  format,
}: {
  label: string;
  comparison: MetricComparison;
  format: (v: number) => string;
}) {
  const color =
    comparison.direction === "up" ? "text-green-600" : comparison.direction === "down" ? "text-red-600" : "text-slate-500";
  const arrow = comparison.direction === "up" ? "▲" : comparison.direction === "down" ? "▼" : "—";
  return (
    <div className="rounded-md border border-slate-200 bg-white p-2">
      <div className="text-xs text-slate-500">{label}</div>
      <div className="mt-0.5 text-sm font-semibold text-slate-800">{format(comparison.today)}</div>
      <div className={`text-xs font-medium ${color}`}>
        {arrow} {format(Math.abs(comparison.delta))}
        {comparison.delta_pct !== null ? ` (${comparison.delta_pct > 0 ? "+" : ""}${comparison.delta_pct}%)` : ""}
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-slate-200 bg-white p-4">
      <div className="text-xs text-slate-500">{label}</div>
      <div className="mt-1 text-xl font-semibold text-slate-800">{value}</div>
    </div>
  );
}
