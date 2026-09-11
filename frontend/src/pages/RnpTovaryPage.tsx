import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../api/client";
import { useStore } from "../store/StoreContext";
import type { MetricPlanFactActual, ProductMonthlyPlanIn, ProductPlannerOut, ProductPlannerRow, SuggestedPlan } from "../types";

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

export function RnpTovaryPage() {
  const { currentStore } = useStore();
  const today = new Date();
  const [year, setYear] = useState(today.getFullYear());
  const [month, setMonth] = useState(today.getMonth() + 1);
  const [data, setData] = useState<ProductPlannerOut | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

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

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-lg font-semibold text-slate-800">РНП Товары — {currentStore.name}</h1>
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
      </div>

      {notice && <div className="rounded-md bg-slate-50 p-2 text-xs text-slate-600">{notice}</div>}

      <div className="rounded-md border border-dashed border-slate-300 bg-white p-4 text-sm text-slate-500">
        План вводится вручную по каждому товару (кнопка «Предложить план» подставит среднее за последние месяцы в
        поля — само по себе нажатие ничего не сохраняет, план нужно сохранить отдельно). «Прогноз мес.» — линейная
        экстраполяция факта на основе прошедших дней месяца. «Хватит на» — по темпу продаж текущего календарного
        месяца. «Локализация» пока не реализована — Ozon не подтвердил метод Seller API для получения доли локальных
        продаж по товару программно (в кабинете это раздел «Локальность продаж», без API-эквивалента в списке
        методов, доступных этому ключу).
      </div>

      {!data ? (
        <div className="text-slate-500">Загрузка...</div>
      ) : !data.has_data ? (
        <div className="rounded-md border border-slate-200 bg-white p-6 text-center text-slate-500">
          В магазине нет товаров.
        </div>
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
  editable,
  editSumValue,
  editUnitsValue,
  onEditSum,
  onEditUnits,
}: {
  label: string;
  metric: MetricPlanFactActual;
  unit: "sum_and_units" | "sum_only";
  editable: boolean;
  editSumValue?: string;
  editUnitsValue?: string;
  onEditSum?: (v: string) => void;
  onEditUnits?: (v: string) => void;
}) {
  return (
    <div className="grid grid-cols-2 gap-2 border-b border-slate-100 py-2 last:border-b-0 md:grid-cols-6">
      <div className="text-xs font-medium text-slate-600 md:col-span-1">{label}</div>
      <div className="text-xs text-slate-500">
        План день
        <div className="font-medium text-slate-700">{fmtRub(metric.plan_day_rub)}</div>
        {unit === "sum_and_units" && <div className="text-slate-400">{fmtNum(metric.plan_day_units)} шт</div>}
      </div>
      <div className="text-xs text-slate-500">
        {editable ? (
          <>
            План месяц
            <input
              type="number"
              value={editSumValue ?? ""}
              onChange={(e) => onEditSum?.(e.target.value)}
              className="mt-0.5 w-full rounded border border-slate-300 px-1.5 py-0.5 text-xs"
              placeholder="сумма"
            />
            {unit === "sum_and_units" && (
              <input
                type="number"
                value={editUnitsValue ?? ""}
                onChange={(e) => onEditUnits?.(e.target.value)}
                className="mt-1 w-full rounded border border-slate-300 px-1.5 py-0.5 text-xs"
                placeholder="шт"
              />
            )}
          </>
        ) : (
          <>
            План месяц
            <div className="font-medium text-slate-700">{fmtRub(metric.plan_month_rub)}</div>
            {unit === "sum_and_units" && <div className="text-slate-400">{fmtNum(metric.plan_month_units)} шт</div>}
          </>
        )}
      </div>
      <div className="text-xs text-slate-500">
        Прогноз мес.
        <div className="font-medium text-slate-700">{fmtRub(metric.forecast_month_rub)}</div>
        {unit === "sum_and_units" && <div className="text-slate-400">{fmtNum(metric.forecast_month_units)} шт</div>}
      </div>
      <div className="text-xs text-slate-500">
        Факт мес.
        <div className="font-medium text-slate-700">{fmtRub(metric.actual_month_rub)}</div>
        {unit === "sum_and_units" && <div className="text-slate-400">{fmtNum(metric.actual_month_units)} шт</div>}
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
    buyouts_units: row.buyouts.plan_month_units?.toString() ?? "",
    buyouts_sum: row.buyouts.plan_month_rub?.toString() ?? "",
    ad_budget: row.ad_budget.plan_month_rub?.toString() ?? "",
    profit: row.profit.plan_month_rub?.toString() ?? "",
  });

  const parseNum = (v: string): number | null => (v.trim() === "" ? null : Number(v));

  const savePlan = async () => {
    if (!storeId || !row.product_id) return;
    setSaving(true);
    setNotice(null);
    try {
      const payload: ProductMonthlyPlanIn = {
        plan_orders_units: parseNum(edit.orders_units),
        plan_orders_sum_rub: parseNum(edit.orders_sum),
        plan_buyouts_units: parseNum(edit.buyouts_units),
        plan_buyouts_sum_rub: parseNum(edit.buyouts_sum),
        plan_ad_budget_rub: parseNum(edit.ad_budget),
        plan_profit_rub: parseNum(edit.profit),
      };
      await api.put(`/stores/${storeId}/product-planner/products/${row.product_id}/plan?year=${year}&month=${month}`, payload);
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
        buyouts_units: suggestion.suggested_buyouts_units?.toString() ?? "",
        buyouts_sum: suggestion.suggested_buyouts_sum_rub?.toString() ?? "",
        ad_budget: suggestion.suggested_ad_budget_rub?.toString() ?? "",
        profit: suggestion.suggested_profit_rub?.toString() ?? "",
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
            <IndicatorTile label="Локализация" value="не реализовано" muted />
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
              editable={!isTotal}
              editSumValue={edit.orders_sum}
              editUnitsValue={edit.orders_units}
              onEditSum={(v) => setEdit((s) => ({ ...s, orders_sum: v }))}
              onEditUnits={(v) => setEdit((s) => ({ ...s, orders_units: v }))}
            />
            <MetricGroupRow
              label="Выкупы"
              metric={row.buyouts}
              unit="sum_and_units"
              editable={!isTotal}
              editSumValue={edit.buyouts_sum}
              editUnitsValue={edit.buyouts_units}
              onEditSum={(v) => setEdit((s) => ({ ...s, buyouts_sum: v }))}
              onEditUnits={(v) => setEdit((s) => ({ ...s, buyouts_units: v }))}
            />
            <MetricGroupRow
              label={`Рекламный бюджет (ДРР ${fmtPct(row.drr_pct_actual)})`}
              metric={row.ad_budget}
              unit="sum_only"
              editable={!isTotal}
              editSumValue={edit.ad_budget}
              onEditSum={(v) => setEdit((s) => ({ ...s, ad_budget: v }))}
            />
            <MetricGroupRow
              label="Прибыль (с ДРР)"
              metric={row.profit}
              unit="sum_only"
              editable={!isTotal}
              editSumValue={edit.profit}
              onEditSum={(v) => setEdit((s) => ({ ...s, profit: v }))}
            />
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

          {showDaily && (
            <div className="mt-3 overflow-x-auto">
              <table className="w-full min-w-[900px] text-left text-xs">
                <thead>
                  <tr className="border-b border-slate-200 text-slate-500">
                    <th className="py-1">Дата</th>
                    <th>Заказы, шт (сумма)</th>
                    <th>Выкупы, шт (сумма)</th>
                    <th>Реклама</th>
                    <th>Прибыль</th>
                  </tr>
                </thead>
                <tbody>
                  {row.daily.map((d) => (
                    <tr key={d.date} className="border-b border-slate-100">
                      <td className="py-1">{d.date}</td>
                      <td>
                        {d.orders_units} ({fmtRub(d.orders_sum_rub)})
                      </td>
                      <td>
                        {d.buyouts_units} ({fmtRub(d.buyouts_sum_rub)})
                      </td>
                      <td>{fmtRub(d.ad_spend_rub)}</td>
                      <td>{fmtRub(d.profit_rub)}</td>
                    </tr>
                  ))}
                  {row.daily.length === 0 && (
                    <tr>
                      <td colSpan={5} className="py-2 text-center text-slate-400">
                        Нет данных за этот месяц.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
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
