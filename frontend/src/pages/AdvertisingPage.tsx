import { ChangeEvent, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { useStore } from "../store/StoreContext";
import type {
  AdvertisingAnalytics,
  AdvertisingCampaign,
  AdvertisingStatistic,
  CampaignDetail,
  ImportSummary,
  MetricComparison,
  PerformanceCredentialsStatus,
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

export function AdvertisingPage() {
  const { currentStore } = useStore();
  const [campaigns, setCampaigns] = useState<AdvertisingCampaign[] | null>(null);
  const [perfStatus, setPerfStatus] = useState<PerformanceCredentialsStatus | null>(null);
  const [analytics, setAnalytics] = useState<AdvertisingAnalytics | null>(null);
  const [statistics, setStatistics] = useState<AdvertisingStatistic[]>([]);
  const [notice, setNotice] = useState<string | null>(null);
  const [syncing, setSyncing] = useState(false);

  const load = () => {
    if (!currentStore) return;
    api.get<AdvertisingCampaign[]>(`/stores/${currentStore.id}/advertising/campaigns`).then(setCampaigns);
    api.get<PerformanceCredentialsStatus>(`/stores/${currentStore.id}/ozon/performance/credentials`).then(setPerfStatus);
    api.get<AdvertisingAnalytics>(`/stores/${currentStore.id}/advertising/analytics`).then(setAnalytics);
    api
      .get<{ items: AdvertisingStatistic[]; total: number }>(`/stores/${currentStore.id}/advertising/statistics`)
      .then((d) => setStatistics(d.items.slice(0, 50)));
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

function CampaignRow({ storeId, campaign }: { storeId: string; campaign: AdvertisingCampaign }) {
  const [expanded, setExpanded] = useState(false);
  const [detail, setDetail] = useState<CampaignDetail | null>(null);
  const [loading, setLoading] = useState(false);

  const toggle = () => {
    const next = !expanded;
    setExpanded(next);
    if (next && detail === null && !loading) {
      setLoading(true);
      api
        .get<CampaignDetail>(`/stores/${storeId}/advertising/campaigns/${campaign.id}/detail`)
        .then(setDetail)
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
            ) : !detail.has_data ? (
              <div className="text-slate-500">
                Нет загруженной статистики для этой кампании. Она появится после загрузки отчёта «Продвижение →
                Статистика», если в файле есть строки с этим ID кампании.
              </div>
            ) : (
              <div className="space-y-3">
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
                    <h4 className="mb-1 text-xs font-semibold text-slate-600">
                      Сравнение {detail.daily_comparison.date_today} с {detail.daily_comparison.date_yesterday}
                    </h4>
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
          </td>
        </tr>
      )}
    </>
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
