import { ChangeEvent, useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../api/client";
import { useStore } from "../store/StoreContext";
import type { BulkPlanEntry as BulkPlanEntryType, ImportSummary, ProductPlannerOut, ProductPlannerRow, SuggestedPlan, SyncRun } from "../types";
import type { MetricPlanFactActual } from "../types";

const MONTH_NAMES = [
  "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
  "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
];

function fmtRub(v: number | null): string {
  if (v === null) return "—";
  return `${v.toLocaleString("ru-RU", { maximumFractionDigits: 2 })} ₽`;
}

function fmtNum(v: number | null): string {
  if (v === null) return "—";
  return v.toLocaleString("ru-RU", { maximumFractionDigits: 1 });
}

function fmtPct(v: number | null): string {
  if (v === null) return "—";
  return `${v.toLocaleString("ru-RU", { maximumFractionDigits: 2 })}%`;
}

type ViewMode = "cards" | "bulk";

export function RnpTovaryPage() {
  const { currentStore } = useStore();
  const today = new Date();
  const [year, setYear] = useState(today.getFullYear());
  const [month, setMonth] = useState(today.getMonth() + 1);
  const [data, setData] = useState<ProductPlannerOut | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [view, setView] = useState<ViewMode>("cards");
  const [syncingLocalization, setSyncingLocalization] = useState(false);
  const [syncingOrders, setSyncingOrders] = useState(false);

  const load = useCallback(async () => {
    if (!currentStore) return;
    try {
      const resp = await api.get<ProductPlannerOut>(`/stores/${currentStore.id}/product-planner?year=${year}&month=${month}`);
      setData(resp);
    } catch (err) {
      setNotice(err instanceof ApiError ? err.message : "Ошибка загрузки");
    }
  }, [currentStore, year, month]);

  useEffect(() => {
    load();
  }, [load]);

  if (!currentStore) return null;

  const changeMonth = (delta: number) => {
    let m = month + delta;
    let y = year;
    if (m < 1) {
      m = 12;
      y -= 1;
    } else if (m > 12) {
      m = 1;
      y += 1;
    }
    setYear(y);
    setMonth(m);
  };

  const syncLocalization = async () => {
    setSyncingLocalization(true);
    setNotice("Запрос % локализации по магазину через Ozon Seller API...");
    try {
      const run = await api.post<SyncRun>(`/stores/${currentStore.id}/sync/ozon-rating-summary`);
      if (run.status === "failed") {
        setNotice(`Не удалось обновить % локализации: ${run.error_message ?? "неизвестная ошибка"}`);
      } else if (run.error_message) {
        setNotice(run.error_message);
      } else {
        setNotice("% локализации по магазину обновлён.");
      }
      load();
    } catch (err) {
      setNotice(err instanceof ApiError ? err.message : "Ошибка обновления % локализации");
    } finally {
      setSyncingLocalization(false);
    }
  };

  // «Заказано» здесь теперь берётся из Ozon Analytics API (воронка,
  // ProductAnalyticsDailyStatistic) там, где она уже собрана — см. докстринг
  // app.services.product_planner_service._aggregate_month. Обычно она
  // обновляется автоматически ночью; эта кнопка даёт обновить прямо сейчас
  // (использует тот же роут, что и «Воронка карточки» на странице товара).
  // Роут работает в фоне (лимит Ozon — 1 запрос/мин), поэтому статус
  // опрашивается, как и в РНП (RnpPage.tsx) — сразу после запуска он ещё
  // "running".
  const pollSyncRun = async (runId: string, attempt = 0): Promise<SyncRun | null> => {
    if (!currentStore) return null;
    const runs = await api.get<SyncRun[]>(`/stores/${currentStore.id}/sync/runs`);
    const run = runs.find((r) => r.id === runId) ?? null;
    if (!run || run.status !== "running" || attempt >= 60) return run;
    await new Promise((resolve) => setTimeout(resolve, 5000));
    return pollSyncRun(runId, attempt + 1);
  };

  const syncOrdersFunnel = async () => {
    setSyncingOrders(true);
    setNotice("Запрос данных «Заказано» через Ozon Analytics API (воронка)...");
    try {
      const run = await api.post<SyncRun>(`/stores/${currentStore.id}/sync/ozon-product-analytics`);
      setNotice("Обновление выполняется в фоне (лимит Ozon — 1 запрос/мин, может занять несколько минут)...");
      const finished = await pollSyncRun(run.id);
      if (!finished) {
        setNotice("Не удалось получить статус обновления — обновите страницу.");
      } else if (finished.status === "failed") {
        setNotice(`Не удалось обновить «Заказано»: ${finished.error_message ?? "неизвестная ошибка"}`);
      } else if (finished.status === "running") {
        setNotice("Обновление всё ещё выполняется — проверьте журнал синхронизаций позже.");
      } else {
        setNotice(
          `«Заказано» обновлено из Ozon Analytics API: получено ${finished.items_fetched}.` +
            (finished.error_message ? ` Предупреждения: ${finished.error_message}` : "")
        );
      }
      load();
    } catch (err) {
      setNotice(err instanceof ApiError ? err.message : "Ошибка обновления «Заказано»");
    } finally {
      setSyncingOrders(false);
    }
  };

  // Единственный подтверждённый источник локализации ПО ТОВАРАМ — см.
  // app.services.product_localization_import: Seller API отдаёт только
  // общий % на магазин, а per-product разбивка живёт в отдельном отчёте
  // кабинета «Планирование поставок → Локальность продаж», без API —
  // только ручная (пере)загрузка XLSX.
  const uploadLocalization = async (e: ChangeEvent<HTMLInputElement>) => {
    if (!currentStore) return;
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setNotice("Загрузка отчёта «Локальность продаж»...");
    try {
      const result = await api.upload<ImportSummary>(`/stores/${currentStore.id}/products/upload-localization`, file);
      setNotice(
        `Загружено: обновлено товаров ${result.created}` +
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
        <h1 className="text-lg font-semibold text-slate-800">РНП Товары — {currentStore.name}</h1>
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex items-center rounded-md border border-slate-200 bg-white p-0.5">
            <button
              onClick={() => setView("cards")}
              className={`rounded px-3 py-1 text-xs font-medium ${view === "cards" ? "bg-indigo-100 text-indigo-700" : "text-slate-500 hover:bg-slate-100"}`}
            >
              По карточкам
            </button>
            <button
              onClick={() => setView("bulk")}
              className={`rounded px-3 py-1 text-xs font-medium ${view === "bulk" ? "bg-indigo-100 text-indigo-700" : "text-slate-500 hover:bg-slate-100"}`}
            >
              Массовый ввод плана
            </button>
          </div>
          <div className="flex items-center gap-2 rounded-md border border-slate-200 bg-white px-2 py-1">
            <button onClick={() => changeMonth(-1)} className="px-2 text-slate-500 hover:text-slate-800" aria-label="Предыдущий месяц">
              ←
            </button>
            <span className="min-w-[140px] text-center text-sm font-medium text-slate-700">
              {MONTH_NAMES[month - 1]} {year}
            </span>
            <button onClick={() => changeMonth(1)} className="px-2 text-slate-500 hover:text-slate-800" aria-label="Следующий месяц">
              →
            </button>
          </div>
          <button
            onClick={syncOrdersFunnel}
            disabled={syncingOrders}
            className="rounded-md bg-indigo-100 px-3 py-1.5 text-xs font-medium text-indigo-700 hover:bg-indigo-200 disabled:opacity-50"
            title="Обновить «Заказано» через Ozon Analytics API — требуется Premium Plus/Premium Pro (POST /v1/analytics/data)"
          >
            {syncingOrders ? "Обновление..." : "Обновить «Заказано»"}
          </button>
          <button
            onClick={syncLocalization}
            disabled={syncingLocalization}
            className="rounded-md bg-indigo-100 px-3 py-1.5 text-xs font-medium text-indigo-700 hover:bg-indigo-200 disabled:opacity-50"
            title="Обновить % локализации по магазину через Ozon Seller API (POST /v1/rating/summary)"
          >
            {syncingLocalization ? "Обновление..." : "Обновить локализацию"}
          </button>
          <label
            className="cursor-pointer rounded-md bg-indigo-100 px-3 py-1.5 text-xs font-medium text-indigo-700 hover:bg-indigo-200"
            title="Кабинет Ozon → Планирование поставок → FBO → Локальность продаж → вкладка «По товарам» → «Скачать XLSX»"
          >
            Загрузить локализацию по товарам (XLSX)
            <input type="file" accept=".xlsx" className="hidden" onChange={uploadLocalization} />
          </label>
        </div>
      </div>

      {notice && <div className="rounded-md bg-slate-50 p-2 text-xs text-slate-600">{notice}</div>}

      <div className="rounded-md border border-dashed border-slate-300 bg-white p-4 text-sm text-slate-500">
        План вводится только по «Заказы» и «Рекламный бюджет» — «Выкупы» и «Прибыль» показывают только прогноз и
        факт. «Прогноз мес.» — линейная экстраполяция факта на основе прошедших дней месяца. «Хватит на» — по темпу
        продаж текущего календарного месяца. «Заказано, шт/₽» берётся из Ozon Analytics API (та же «воронка», что и
        на карточке товара) для дней, где она уже собрана — это число, которое считает сам Ozon, а не то, что
        собирает это приложение из отгрузок; обновляется автоматически ночью, либо сразу кнопкой «Обновить
        «Заказано»» (нужен Premium Plus/Premium Pro). «Выкупы» продолжают считаться из отгрузок Ozon Seller API — по
        ним у Ozon Analytics API аналога нет. «Локализация» по магазину («Итого») — из Ozon Seller API (кнопка
        «Обновить локализацию»). Локализация по каждому товару — Seller API её не отдаёт вообще, поэтому она берётся
        из отдельного отчёта кабинета Ozon: «Планирование поставок» → «Локальность продаж» → вкладка «По товарам» →
        «Скачать XLSX» — загрузите этот файл кнопкой «Загрузить локализацию по товарам» выше, чтобы обновить (Ozon
        считает эту долю отдельно по каждому кластеру доставки — здесь показан взвешенный по продажам средний %).
      </div>

      {!data ? (
        <div className="text-slate-500">Загрузка...</div>
      ) : !data.has_data ? (
        <div className="rounded-md border border-slate-200 bg-white p-6 text-center text-slate-500">
          В магазине нет товаров.
        </div>
      ) : view === "bulk" ? (
        <BulkPlanTable rows={data.rows} storeId={currentStore.id} year={year} month={month} onSaved={load} />
      ) : (
        <div className="space-y-3">
          {data.total && <ProductPlannerCard row={data.total} isTotal />}
          {data.rows.map((row) => (
            <ProductPlannerCard
              key={row.product_id}
              row={row}
              storeId={currentStore.id}
              year={year}
              month={month}
              onSaved={load}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function MetricGroupRow({
  label,
  metric,
  unit,
  plannable,
  planMode = "rub",
  editSumValue,
  editUnitsValue,
  editPctValue,
  onEditSum,
  onEditUnits,
  onEditPct,
  forecastPct,
  actualPct,
  pctLabel,
}: {
  label: string;
  metric: MetricPlanFactActual;
  unit: "sum_and_units" | "sum_only";
  plannable: boolean;
  planMode?: "rub" | "pct";
  editSumValue?: string;
  editUnitsValue?: string;
  editPctValue?: string;
  onEditSum?: (v: string) => void;
  onEditUnits?: (v: string) => void;
  onEditPct?: (v: string) => void;
  // Доп. строка «X%» под Прогноз/Факт — для «Рекламный бюджет» (ДРР % =
  // расход / выкупы за тот же месяц) и «Заказы» (% от плана в штуках).
  forecastPct?: number | null;
  actualPct?: number | null;
  pctLabel?: string; // подпись перед %, чтобы не путать ДРР% с % от плана
}) {
  return (
    <div
      className={`grid grid-cols-2 gap-2 border-b border-slate-100 py-2 last:border-b-0 ${plannable ? "md:grid-cols-6" : "md:grid-cols-4"}`}
    >
      <div className="text-xs font-medium text-slate-600 md:col-span-1">{label}</div>
      {plannable && planMode === "rub" && (
        <>
          <div className="text-xs text-slate-500">
            План день
            <div className="font-medium text-slate-700">{fmtRub(metric.plan_day_rub)}</div>
            {unit === "sum_and_units" && <div className="text-slate-400">{fmtNum(metric.plan_day_units)} шт</div>}
          </div>
          <div className="text-xs text-slate-500">
            План месяц
            {unit === "sum_and_units" && (
              <input
                type="number"
                value={editUnitsValue ?? ""}
                onChange={(e) => onEditUnits?.(e.target.value)}
                className="mt-0.5 w-full max-w-[90px] rounded border border-slate-300 px-1.5 py-0.5 text-xs"
                placeholder="шт"
              />
            )}
          </div>
        </>
      )}
      {plannable && planMode === "pct" && (
        <div className="text-xs text-slate-500 md:col-span-2">
          План ДРР, %
          <input
            type="number"
            value={editPctValue ?? ""}
            onChange={(e) => onEditPct?.(e.target.value)}
            className="mt-0.5 w-full max-w-[120px] rounded border border-slate-300 px-1.5 py-0.5 text-xs"
            placeholder="%"
          />
          {metric.plan_month_rub !== null && <div className="mt-0.5 text-slate-400">≈ {fmtRub(metric.plan_month_rub)}/мес</div>}
        </div>
      )}
      <div className="text-xs text-slate-500">
        Прогноз мес.
        <div className="font-medium text-slate-700">{fmtRub(metric.forecast_month_rub)}</div>
        {unit === "sum_and_units" && <div className="text-slate-400">{fmtNum(metric.forecast_month_units)} шт</div>}
        {forecastPct !== undefined && (
          <div className="text-slate-400">
            {pctLabel ? `${pctLabel} ${fmtPct(forecastPct ?? null)}` : fmtPct(forecastPct ?? null)}
          </div>
        )}
      </div>
      <div className="text-xs text-slate-500">
        Факт мес.
        <div className="font-medium text-slate-700">{fmtRub(metric.actual_month_rub)}</div>
        {unit === "sum_and_units" && <div className="text-slate-400">{fmtNum(metric.actual_month_units)} шт</div>}
        {actualPct !== undefined && (
          <div className="text-slate-400">
            {pctLabel ? `${pctLabel} ${fmtPct(actualPct ?? null)}` : fmtPct(actualPct ?? null)}
          </div>
        )}
      </div>
    </div>
  );
}

function ProductPlannerCard({
  row,
  isTotal,
  storeId,
  year,
  month,
  onSaved,
}: {
  row: ProductPlannerRow;
  isTotal?: boolean;
  storeId?: string;
  year?: number;
  month?: number;
  onSaved?: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [showDaily, setShowDaily] = useState(false);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [edit, setEdit] = useState({
    orders_units: row.orders.plan_month_units?.toString() ?? "",
    orders_sum: row.orders.plan_month_rub?.toString() ?? "",
    ad_budget_pct: row.ad_budget.plan_pct?.toString() ?? "",
  });

  const parseNum = (v: string): number | null => (v.trim() === "" ? null : Number(v));

  const savePlan = async () => {
    if (!storeId || !row.product_id) return;
    setSaving(true);
    setNotice(null);
    try {
      await api.put(`/stores/${storeId}/product-planner/products/${row.product_id}/plan?year=${year}&month=${month}`, {
        plan_orders_units: parseNum(edit.orders_units),
        plan_orders_sum_rub: parseNum(edit.orders_sum),
        plan_ad_budget_pct: parseNum(edit.ad_budget_pct),
      });
      setNotice("План сохранён.");
      onSaved?.();
    } catch (err) {
      setNotice(err instanceof ApiError ? err.message : "Ошибка сохранения плана");
    } finally {
      setSaving(false);
    }
  };

  const suggestPlan = async () => {
    if (!storeId || !row.product_id) return;
    setNotice("Загрузка предложенного плана...");
    try {
      const suggestion = await api.get<SuggestedPlan>(
        `/stores/${storeId}/product-planner/products/${row.product_id}/suggest-plan?year=${year}&month=${month}`
      );
      if (suggestion.based_on_months === 0) {
        setNotice("Недостаточно истории продаж для этого товара, чтобы предложить план.");
        return;
      }
      setEdit({
        orders_units: suggestion.suggested_orders_units?.toString() ?? "",
        orders_sum: suggestion.suggested_orders_sum_rub?.toString() ?? "",
        ad_budget_pct: suggestion.suggested_ad_budget_pct?.toString() ?? "",
      });
      setNotice(`Предложено по среднему за ${suggestion.based_on_months} мес. — проверьте и сохраните, если подходит.`);
    } catch (err) {
      setNotice(err instanceof ApiError ? err.message : "Ошибка получения предложенного плана");
    }
  };

  return (
    <div className="rounded-md border border-slate-200 bg-white p-3">
      <button onClick={() => setExpanded((v) => !v)} className="flex w-full items-center gap-3 text-left">
        {!isTotal &&
          (row.product_image_url ? (
            <img src={row.product_image_url} alt="" className="h-12 w-12 shrink-0 rounded object-cover" />
          ) : (
            <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded bg-slate-100 text-[10px] text-slate-400">
              нет фото
            </div>
          ))}
        <div className="min-w-0 flex-1">
          <div className="truncate font-medium text-slate-800">{row.product_name}</div>
          {!isTotal && row.product_sku && <div className="text-xs text-slate-400">SKU {row.product_sku}</div>}
        </div>
        <div className="hidden shrink-0 gap-4 text-xs text-slate-500 md:flex">
          <span>КРПП: {fmtPct(row.krpp_pct)}</span>
          <span>Маржа с ДРР: {fmtPct(row.margin_after_ad_pct)}</span>
          <span>Хватит на: {row.days_of_stock_remaining !== null ? `${fmtNum(row.days_of_stock_remaining)} дн.` : "—"}</span>
        </div>
        <span className="shrink-0 text-slate-400">{expanded ? "▲" : "▼"}</span>
      </button>

      {expanded && (
        <div className="mt-3 border-t border-slate-100 pt-3">
          <div className="mb-3 grid grid-cols-2 gap-2 md:grid-cols-5">
            {isTotal ? (
              <IndicatorTile
                label="Локализация (по магазину)"
                value={row.localization_pct !== null ? fmtPct(row.localization_pct) : "нет данных"}
                sub={
                  row.localization_calculation_date
                    ? `на ${row.localization_calculation_date}`
                    : "нажмите «Обновить локализацию»"
                }
                muted={row.localization_pct === null}
              />
            ) : (
              <IndicatorTile
                label="Локализация"
                value={row.localization_pct !== null ? fmtPct(row.localization_pct) : "нет данных"}
                sub={
                  row.localization_calculation_date
                    ? `на ${row.localization_calculation_date}`
                    : "загрузите отчёт «Локальность продаж» кнопкой выше"
                }
                muted={row.localization_pct === null}
              />
            )}
            <IndicatorTile
              label="Остатки"
              value={row.stock_total_units !== null ? `${row.stock_total_units} шт` : "нет данных"}
              sub={row.stock_fbo_units !== null ? `FBO ${row.stock_fbo_units} / FBS ${row.stock_fbs_units ?? 0}` : undefined}
            />
            <IndicatorTile
              label="Хватит на"
              value={row.days_of_stock_remaining !== null ? `${fmtNum(row.days_of_stock_remaining)} дн.` : "—"}
            />
            <IndicatorTile label="КРПП" value={fmtPct(row.krpp_pct)} sub="Прибыль с ДРР / Прибыль до ДРР" />
            <IndicatorTile label="Маржа до/с ДРР" value={`${fmtPct(row.margin_before_ad_pct)} / ${fmtPct(row.margin_after_ad_pct)}`} />
          </div>

          {!row.cost_known && !isTotal && (
            <p className="mb-2 text-xs text-amber-600">
              Себестоимость не введена для этого товара — Прибыль/КРПП/Маржа не показаны.
            </p>
          )}

          <div className="rounded-md border border-slate-100">
            <MetricGroupRow
              label="Заказы"
              metric={row.orders}
              unit="sum_and_units"
              plannable={!isTotal}
              editSumValue={edit.orders_sum}
              editUnitsValue={edit.orders_units}
              onEditSum={(v) => setEdit((s) => ({ ...s, orders_sum: v }))}
              onEditUnits={(v) => setEdit((s) => ({ ...s, orders_units: v }))}
              // % выполнения плана — от штук (план всегда вводится в штуках,
              // рублёвый план сейчас нечем ввести на этой странице, см.
              // editSumValue — принимается, но своего поля ввода не имеет).
              // Прогноз/план показывает, куда придёт месяц при текущем
              // темпе; факт/план — сколько уже выполнено на сегодня.
              forecastPct={
                row.orders.forecast_month_units && row.orders.plan_month_units
                  ? (row.orders.forecast_month_units / row.orders.plan_month_units) * 100
                  : null
              }
              actualPct={
                row.orders.actual_month_units && row.orders.plan_month_units
                  ? (row.orders.actual_month_units / row.orders.plan_month_units) * 100
                  : null
              }
              pctLabel="от плана"
            />
            <MetricGroupRow label="Выкупы" metric={row.buyouts} unit="sum_and_units" plannable={false} />
            <MetricGroupRow
              label={`Рекламный бюджет (ДРР ${fmtPct(row.drr_pct_actual)})`}
              metric={row.ad_budget}
              unit="sum_only"
              plannable={!isTotal}
              planMode="pct"
              editPctValue={edit.ad_budget_pct}
              onEditPct={(v) => setEdit((s) => ({ ...s, ad_budget_pct: v }))}
              // ДРР % = расход / выкупы за тот же месяц — прогноз и факт
              // считаются от соответствующих значений «Выкупы» (та же пара
              // Прогноз/Факт), а не только факт, который уже был в заголовке.
              forecastPct={
                row.buyouts.forecast_month_rub && row.ad_budget.forecast_month_rub !== null
                  ? (row.ad_budget.forecast_month_rub / row.buyouts.forecast_month_rub) * 100
                  : null
              }
              actualPct={row.drr_pct_actual}
            />
            <MetricGroupRow label="Прибыль (с ДРР)" metric={row.profit} unit="sum_only" plannable={false} />
          </div>

          {!isTotal && (
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <button
                onClick={savePlan}
                disabled={saving}
                className="rounded-md bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
              >
                {saving ? "Сохранение..." : "Сохранить план"}
              </button>
              <button
                onClick={suggestPlan}
                className="rounded-md bg-slate-100 px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-200"
              >
                Предложить план
              </button>
              <button
                onClick={() => setShowDaily((v) => !v)}
                className="rounded-md bg-slate-100 px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-200"
              >
                {showDaily ? "Скрыть по дням" : "Показать по дням"}
              </button>
              {notice && <span className="text-xs text-slate-500">{notice}</span>}
            </div>
          )}

          {showDaily && <DailyBreakdownTable row={row} />}
        </div>
      )}
    </div>
  );
}

function dailyComparisonClass(actual: number, planPerDay: number | null): string {
  if (planPerDay === null) return "";
  if (actual < planPerDay) return "bg-red-50";
  if (actual > planPerDay) return "bg-green-50";
  return "";
}

function DailyBreakdownTable({ row }: { row: ProductPlannerRow }) {
  if (row.daily.length === 0) {
    return <div className="mt-3 rounded-md border border-dashed border-slate-200 p-3 text-center text-xs text-slate-400">Нет данных за этот месяц.</div>;
  }
  const planOrdersPerDay = row.orders.plan_day_units;
  return (
    <div className="mt-3 overflow-x-auto">
      <table className="text-left text-xs">
        <thead>
          <tr className="border-b border-slate-200 text-slate-500">
            <th className="py-1 pr-3">Показатель</th>
            {row.daily.map((d) => (
              <th key={d.date} className="whitespace-nowrap px-2 text-center">
                {d.date}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          <tr className="border-b border-slate-100">
            <td className="py-1 pr-3 font-medium text-slate-600">Заказы, шт</td>
            {row.daily.map((d) => (
              <td key={d.date} className={`whitespace-nowrap px-2 text-center ${dailyComparisonClass(d.orders_units, planOrdersPerDay)}`}>
                {d.orders_units}
              </td>
            ))}
          </tr>
          <tr className="border-b border-slate-100">
            <td className="py-1 pr-3 font-medium text-slate-600">Заказы, ₽</td>
            {row.daily.map((d) => (
              <td key={d.date} className="whitespace-nowrap px-2 text-center">
                {fmtRub(d.orders_sum_rub)}
              </td>
            ))}
          </tr>
          <tr className="border-b border-slate-100">
            <td className="py-1 pr-3 font-medium text-slate-600">СПП (расчёт)</td>
            {row.daily.map((d) => {
              // База «Ваша цена» (см. DailyBreakdownEntry-докстринг в
              // frontend/src/types/index.ts) — НЕ orders_sum_rub (Цена до
              // скидки), которая давала завышенный СПП на РНП (~75% вместо
              // реальных ~40-53%, см. RnpPage.tsx). sppKnown отражает,
              // покрыты ли ВСЕ заказанные штуки за день известной ценой
              // продавца.
              const sppKnown = d.orders_units_with_known_seller_price >= d.orders_units && d.orders_units > 0;
              const sppPct =
                d.orders_sum_seller_price_rub > 0
                  ? ((d.orders_sum_seller_price_rub - d.orders_sum_discounted_for_known_seller_price_rub) / d.orders_sum_seller_price_rub) * 100
                  : null;
              return (
                <td
                  key={d.date}
                  className="whitespace-nowrap px-2 text-center"
                  title={sppKnown || sppPct === null ? undefined : "Текущая цена продавца известна не для всех заказанных товаров — СПП посчитан только по известным"}
                >
                  {sppPct === null ? "—" : `${sppKnown ? "" : "≈"}${fmtPct(sppPct)}`}
                </td>
              );
            })}
          </tr>
          <tr className="border-b border-slate-100">
            <td className="py-1 pr-3 font-medium text-slate-600">Выкупы, шт</td>
            {row.daily.map((d) => (
              <td key={d.date} className="whitespace-nowrap px-2 text-center">
                {d.buyouts_units}
              </td>
            ))}
          </tr>
          <tr className="border-b border-slate-100">
            <td className="py-1 pr-3 font-medium text-slate-600">Выкупы, ₽</td>
            {row.daily.map((d) => (
              <td key={d.date} className="whitespace-nowrap px-2 text-center">
                {fmtRub(d.buyouts_sum_rub)}
              </td>
            ))}
          </tr>
          <tr className="border-b border-slate-100">
            <td className="py-1 pr-3 font-medium text-slate-600">CTR</td>
            {row.daily.map((d) => (
              <td key={d.date} className="whitespace-nowrap px-2 text-center">
                {fmtPct(d.ad_impressions > 0 ? (d.ad_clicks / d.ad_impressions) * 100 : null)}
              </td>
            ))}
          </tr>
          <tr className="border-b border-slate-100">
            <td className="py-1 pr-3 font-medium text-slate-600">Рекламный бюджет, ДРР %</td>
            {row.daily.map((d) => (
              <td key={d.date} className="whitespace-nowrap px-2 text-center">
                {fmtPct(d.buyouts_sum_rub > 0 ? (d.ad_spend_rub / d.buyouts_sum_rub) * 100 : null)}
              </td>
            ))}
          </tr>
          <tr>
            <td className="py-1 pr-3 font-medium text-slate-600">Прибыль</td>
            {row.daily.map((d) => (
              <td key={d.date} className="whitespace-nowrap px-2 text-center">
                {fmtRub(d.profit_rub)}
              </td>
            ))}
          </tr>
        </tbody>
      </table>
    </div>
  );
}

function IndicatorTile({ label, value, sub, muted }: { label: string; value: string; sub?: string; muted?: boolean }) {
  return (
    <div className="rounded-md border border-slate-200 bg-slate-50 p-2">
      <div className="text-[11px] text-slate-500">{label}</div>
      <div className={`text-sm font-medium ${muted ? "text-slate-400" : "text-slate-800"}`}>{value}</div>
      {sub && <div className="text-[10px] text-slate-400">{sub}</div>}
    </div>
  );
}

function BulkPlanTable({
  rows,
  storeId,
  year,
  month,
  onSaved,
}: {
  rows: ProductPlannerRow[];
  storeId: string;
  year: number;
  month: number;
  onSaved: () => void;
}) {
  const [edits, setEdits] = useState<Record<string, { orders_units: string; ad_budget_pct: string }>>(() =>
    Object.fromEntries(
      rows.map((r) => [
        r.product_id as string,
        {
          orders_units: r.orders.plan_month_units?.toString() ?? "",
          ad_budget_pct: r.ad_budget.plan_pct?.toString() ?? "",
        },
      ])
    )
  );
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const setField = (productId: string, field: "orders_units" | "ad_budget_pct", value: string) => {
    setEdits((prev) => ({ ...prev, [productId]: { ...prev[productId], [field]: value } }));
  };

  const parseNum = (v: string): number | null => (v.trim() === "" ? null : Number(v));

  const saveAll = async () => {
    setSaving(true);
    setNotice(null);
    try {
      // plan_orders_sum_rub is deliberately omitted here (not just left
      // undefined-but-present) — this screen doesn't show/edit it at all,
      // and the backend's PATCH semantics (see _upsert_plan's own
      // docstring) leave an omitted field untouched rather than wiping a
      // rubles plan entered earlier via the per-product card.
      const entries: BulkPlanEntryType[] = rows.map((r) => ({
        product_id: r.product_id as string,
        plan_orders_units: parseNum(edits[r.product_id as string]?.orders_units ?? ""),
        plan_ad_budget_pct: parseNum(edits[r.product_id as string]?.ad_budget_pct ?? ""),
      }));
      await api.put(`/stores/${storeId}/product-planner/plans/bulk?year=${year}&month=${month}`, { entries });
      setNotice(`План сохранён для ${entries.length} товаров.`);
      onSaved();
    } catch (err) {
      setNotice(err instanceof ApiError ? err.message : "Ошибка сохранения плана");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="rounded-md border border-slate-200 bg-white p-3">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-slate-500">
          Проставьте план по заказам и рекламному бюджету сразу для всех товаров, затем сохраните одной кнопкой.
        </p>
        <button
          onClick={saveAll}
          disabled={saving}
          className="shrink-0 rounded-md bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
        >
          {saving ? "Сохранение..." : "Сохранить план для всех"}
        </button>
      </div>
      {notice && <p className="mb-2 text-xs text-slate-500">{notice}</p>}
      <div className="overflow-x-auto">
        <table className="w-full min-w-[720px] text-left text-xs">
          <thead>
            <tr className="border-b border-slate-200 text-slate-500">
              <th className="py-1">Товар</th>
              <th>План заказов, шт</th>
              <th>План ДРР, %</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const productId = r.product_id as string;
              const e = edits[productId] ?? { orders_units: "", ad_budget_pct: "" };
              return (
                <tr key={productId} className="border-b border-slate-100">
                  <td className="max-w-[240px] truncate py-1.5 pr-2">
                    <div className="truncate font-medium text-slate-700">{r.product_name}</div>
                    {r.product_sku && <div className="text-[10px] text-slate-400">SKU {r.product_sku}</div>}
                  </td>
                  <td className="pr-2">
                    <input
                      type="number"
                      value={e.orders_units}
                      onChange={(ev) => setField(productId, "orders_units", ev.target.value)}
                      className="w-24 rounded border border-slate-300 px-1.5 py-0.5"
                    />
                  </td>
                  <td>
                    <input
                      type="number"
                      value={e.ad_budget_pct}
                      onChange={(ev) => setField(productId, "ad_budget_pct", ev.target.value)}
                      className="w-20 rounded border border-slate-300 px-1.5 py-0.5"
                    />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
