import { ChangeEvent, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { useStore } from "../store/StoreContext";
import type { AdvertisingAnalytics, AdvertisingCampaign, AdvertisingStatistic, ImportSummary, PerformanceCredentialsStatus } from "../types";

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

      <div>
        <h3 className="mb-2 text-sm font-semibold text-slate-700">Кампании (метаданные из Ozon Performance API)</h3>
        {campaigns === null ? (
          <div className="text-slate-500">Загрузка...</div>
        ) : campaigns.length === 0 ? (
          <div className="text-slate-500">Нет данных. Синхронизируйте кампании, чтобы увидеть список.</div>
        ) : (
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-slate-200 text-xs text-slate-500">
                <th className="py-1">Название</th>
                <th>Тип</th>
                <th>Статус</th>
                <th>Дневной бюджет</th>
              </tr>
            </thead>
            <tbody>
              {campaigns.map((c) => (
                <tr key={c.id} className="border-b border-slate-100">
                  <td className="py-1">{c.name ?? "Без названия"}</td>
                  <td>{c.campaign_type ?? "—"}</td>
                  <td>{c.state ?? "—"}</td>
                  <td>{c.daily_budget_rub !== null ? fmtRub(c.daily_budget_rub) : "Нет данных"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
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
