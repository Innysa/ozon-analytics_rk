import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { useStore } from "../store/StoreContext";
import type { AdvertisingDailyStatistic, OrderDailyStatistic, SyncRun } from "../types";

function fmtRub(v: number): string {
  return `${v.toLocaleString("ru-RU", { maximumFractionDigits: 2 })} ₽`;
}

function fmtPct(v: number | null): string {
  if (v === null) return "—";
  return `${v.toLocaleString("ru-RU", { maximumFractionDigits: 1 })}%`;
}

function isoDate(d: Date): string {
  // Local calendar date, NOT toISOString().slice(0, 10) — that converts to
  // UTC first, which silently shifts the 1st of the month back to the last
  // day of the PREVIOUS month for any timezone ahead of UTC (e.g. Moscow,
  // UTC+3: local midnight Sept 1 is Aug 31 21:00 UTC). CONFIRMED as the
  // actual reason defaultDateFrom() below still wasn't landing on the 1st
  // for a real user despite already computing "1st of this month" (2026-09-11).
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function defaultDateTo(): string {
  return isoDate(new Date());
}

function defaultDateFrom(): string {
  // 1-е число текущего месяца — подтверждено пользователем (2026-09-11):
  // открывать страницу сразу с начала месяца, без ручной перестановки дат.
  const d = new Date();
  return isoDate(new Date(d.getFullYear(), d.getMonth(), 1));
}

interface DayRow {
  date: string;
  orderedUnits: number;
  orderedSumRub: number;
  orderedSumDiscountedRub: number;
  deliveredUnits: number;
  deliveredSumRub: number;
  costOfDeliveredRub: number;
  costOfDeliveredKnownUnits: number;
  cancelledUnits: number;
  cancelledSumRub: number;
  unfinishedUnits: number;
  commissionRub: number;
  adSpendRub: number;
}

function emptyDay(date: string): DayRow {
  return {
    date, orderedUnits: 0, orderedSumRub: 0, orderedSumDiscountedRub: 0,
    deliveredUnits: 0, deliveredSumRub: 0, costOfDeliveredRub: 0, costOfDeliveredKnownUnits: 0,
    cancelledUnits: 0, cancelledSumRub: 0, unfinishedUnits: 0, commissionRub: 0, adSpendRub: 0,
  };
}

function combineByDate(orderRows: OrderDailyStatistic[], adRows: AdvertisingDailyStatistic[]): DayRow[] {
  const byDate = new Map<string, DayRow>();
  for (const r of orderRows) {
    const acc = byDate.get(r.date) ?? emptyDay(r.date);
    acc.orderedUnits += r.ordered_units;
    acc.orderedSumRub += r.ordered_sum_rub;
    acc.orderedSumDiscountedRub += r.ordered_sum_discounted_rub;
    acc.deliveredUnits += r.delivered_units;
    acc.deliveredSumRub += r.delivered_sum_rub;
    acc.costOfDeliveredRub += r.cost_of_delivered_rub;
    acc.costOfDeliveredKnownUnits += r.cost_of_delivered_known_units;
    acc.cancelledUnits += r.cancelled_units;
    acc.cancelledSumRub += r.cancelled_sum_rub;
    acc.unfinishedUnits += r.unfinished_units;
    acc.commissionRub += r.commission_rub;
    byDate.set(r.date, acc);
  }
  for (const r of adRows) {
    const acc = byDate.get(r.date) ?? emptyDay(r.date);
    acc.adSpendRub += r.spend_rub ?? 0;
    byDate.set(r.date, acc);
  }
  return [...byDate.values()].sort((a, b) => (a.date < b.date ? 1 : -1));
}

function sumRows(rows: DayRow[]): DayRow {
  const total = emptyDay("Итого");
  for (const r of rows) {
    total.orderedUnits += r.orderedUnits;
    total.orderedSumRub += r.orderedSumRub;
    total.orderedSumDiscountedRub += r.orderedSumDiscountedRub;
    total.deliveredUnits += r.deliveredUnits;
    total.deliveredSumRub += r.deliveredSumRub;
    total.costOfDeliveredRub += r.costOfDeliveredRub;
    total.costOfDeliveredKnownUnits += r.costOfDeliveredKnownUnits;
    total.cancelledUnits += r.cancelledUnits;
    total.cancelledSumRub += r.cancelledSumRub;
    total.unfinishedUnits += r.unfinishedUnits;
    total.commissionRub += r.commissionRub;
    total.adSpendRub += r.adSpendRub;
  }
  return total;
}

function RowCells({ row }: { row: DayRow }) {
  const totalUnits = row.orderedUnits;
  const sppPct = row.orderedSumRub > 0 ? ((row.orderedSumRub - row.orderedSumDiscountedRub) / row.orderedSumRub) * 100 : null;
  const avgCheckBefore = totalUnits > 0 ? row.orderedSumRub / totalUnits : null;
  const avgCheckAfter = totalUnits > 0 ? row.orderedSumDiscountedRub / totalUnits : null;
  const buyoutPct = totalUnits > 0 ? (row.deliveredUnits / totalUnits) * 100 : null;
  const cancelledPct = totalUnits > 0 ? (row.cancelledUnits / totalUnits) * 100 : null;
  const costKnown = row.costOfDeliveredKnownUnits >= row.deliveredUnits && row.deliveredUnits > 0;
  const costPct = row.deliveredSumRub > 0 && costKnown ? (row.costOfDeliveredRub / row.deliveredSumRub) * 100 : null;
  const drrPct = row.deliveredSumRub > 0 ? (row.adSpendRub / row.deliveredSumRub) * 100 : null;

  return (
    <>
      <td className="py-1 pl-3">{row.date}</td>
      <td>{row.orderedUnits.toLocaleString("ru-RU")}</td>
      <td>{fmtRub(row.orderedSumRub)}</td>
      <td>{fmtRub(row.orderedSumDiscountedRub)}</td>
      <td>{fmtPct(sppPct)}</td>
      <td>{avgCheckBefore !== null ? fmtRub(avgCheckBefore) : "—"}</td>
      <td>{avgCheckAfter !== null ? fmtRub(avgCheckAfter) : "—"}</td>
      <td>
        {row.deliveredUnits.toLocaleString("ru-RU")} ({fmtPct(buyoutPct)})
      </td>
      <td>{fmtRub(row.deliveredSumRub)}</td>
      <td title={costKnown ? undefined : "Себестоимость введена не для всех проданных товаров — укажите её на карточках товаров"}>
        {costKnown ? `${fmtRub(row.costOfDeliveredRub)} (${fmtPct(costPct)})` : "не введена"}
      </td>
      <td>
        {row.cancelledUnits.toLocaleString("ru-RU")} ({fmtPct(cancelledPct)})
      </td>
      <td>{row.unfinishedUnits.toLocaleString("ru-RU")}</td>
      <td>{fmtRub(row.commissionRub)}</td>
      <td>{fmtRub(row.adSpendRub)}</td>
      <td>{fmtPct(drrPct)}</td>
    </>
  );
}

export function RnpPage() {
  const { currentStore } = useStore();
  const [rows, setRows] = useState<DayRow[] | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [dateFrom, setDateFrom] = useState(defaultDateFrom);
  const [dateTo, setDateTo] = useState(defaultDateTo);

  const load = useCallback(async () => {
    if (!currentStore) return;
    const params = new URLSearchParams({ date_from: dateFrom, date_to: dateTo }).toString();
    const [orderResp, adResp] = await Promise.all([
      api.get<{ items: OrderDailyStatistic[]; total: number }>(`/stores/${currentStore.id}/orders/daily-statistics?${params}`),
      api.get<{ items: AdvertisingDailyStatistic[]; total: number }>(`/stores/${currentStore.id}/advertising/daily-statistics?${params}`),
    ]);
    setRows(combineByDate(orderResp.items, adResp.items));
  }, [currentStore, dateFrom, dateTo]);

  useEffect(() => {
    load();
  }, [load]);

  if (!currentStore) return null;

  const pollSyncRun = async (runId: string, attempt = 0): Promise<SyncRun | null> => {
    const runs = await api.get<SyncRun[]>(`/stores/${currentStore.id}/sync/runs`);
    const run = runs.find((r) => r.id === runId) ?? null;
    if (!run || run.status !== "running" || attempt >= 60) return run;
    await new Promise((resolve) => setTimeout(resolve, 5000));
    return pollSyncRun(runId, attempt + 1);
  };

  const syncOrders = async () => {
    setSyncing(true);
    setNotice("Запуск автосбора заказов через Ozon Seller API...");
    try {
      const params = new URLSearchParams({ date_from: dateFrom, date_to: dateTo }).toString();
      const run = await api.post<SyncRun>(`/stores/${currentStore.id}/sync/ozon-orders?${params}`);
      setNotice("Сбор заказов выполняется в фоне...");
      const finished = await pollSyncRun(run.id);
      if (!finished) {
        setNotice("Не удалось получить статус синхронизации — обновите страницу.");
      } else if (finished.status === "failed") {
        setNotice(`Автосбор заказов не удался: ${finished.error_message ?? "неизвестная ошибка"}`);
      } else if (finished.status === "running") {
        setNotice("Синхронизация всё ещё выполняется — проверьте журнал синхронизаций позже.");
      } else {
        setNotice(
          `Готово: получено ${finished.items_fetched}, создано ${finished.items_created}, обновлено ${finished.items_skipped_duplicate}.` +
            (finished.error_message ? ` Предупреждения: ${finished.error_message}` : "")
        );
      }
      load();
    } catch (err) {
      setNotice(err instanceof ApiError ? err.message : "Ошибка автосбора заказов");
    } finally {
      setSyncing(false);
    }
  };

  const total = rows ? sumRows(rows) : null;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-lg font-semibold text-slate-800">РНП — {currentStore.name}</h1>
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex items-center gap-1 rounded-md border border-slate-200 bg-white px-2 py-1">
            <input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} max={dateTo} className="text-xs text-slate-600 outline-none" />
            <span className="text-xs text-slate-400">—</span>
            <input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} min={dateFrom} className="text-xs text-slate-600 outline-none" />
          </div>
          <button
            onClick={syncOrders}
            disabled={syncing}
            className="rounded-md bg-indigo-100 px-3 py-1.5 text-xs font-medium text-indigo-700 hover:bg-indigo-200 disabled:opacity-50"
            title="Автоматически собрать заказы через Ozon Seller API (FBO/FBS) за выбранный период"
          >
            {syncing ? "Сбор заказов..." : "Обновить заказы (авто)"}
          </button>
        </div>
      </div>

      {notice && <div className="rounded-md bg-slate-50 p-2 text-xs text-slate-600">{notice}</div>}

      <div className="rounded-md border border-dashed border-slate-300 bg-white p-4 text-sm text-slate-500">
        Заказы, выкупы, отмены и комиссия — из Ozon Seller API (FBO/FBS), автоматически. Расход на рекламу и ДРР — из
        уже собранной статистики на странице «Реклама». Себестоимость выкупа — только если она указана на карточках{" "}
        <Link to="/products" className="underline">
          товаров
        </Link>
        . «Количество переходов в карточку / Положили в корзину / Конверсия» здесь пока НЕТ — Ozon не отдаёт эти
        данные через заказы, только через отдельный отчёт «Аналитика → Товары» (страница «Товары»). Логистику,
        хранение, штрафы и налоги показать пока нельзя — эта часть ответа Ozon ещё не подтверждена на реальных
        данных.
      </div>

      {!rows ? (
        <div className="text-slate-500">Загрузка...</div>
      ) : rows.length === 0 ? (
        <div className="rounded-md border border-slate-200 bg-white p-6 text-center text-slate-500">
          Нет данных за этот период. Нажмите «Обновить заказы (авто)» выше.
        </div>
      ) : (
        <div className="overflow-x-auto rounded-md border border-slate-200 bg-white">
          <table className="w-full min-w-[1400px] text-left text-sm">
            <thead>
              <tr className="border-b border-slate-200 text-xs text-slate-500">
                <th className="py-1 pl-3">Дата</th>
                <th>Заказано, шт</th>
                <th>Заказано на сумму</th>
                <th>Заказано с учётом скидки</th>
                <th>СПП (расчёт)</th>
                <th>Средний чек до скидки</th>
                <th>Средний чек после скидки</th>
                <th>Выкуплено, шт (%)</th>
                <th>Выкуплено на сумму</th>
                <th>Себестоимость выкупа (%)</th>
                <th>Отменено, шт (%)</th>
                <th>Незавершено, шт</th>
                <th>Комиссия Ozon</th>
                <th>Расход на рекламу</th>
                <th>ДРР (расчёт)</th>
              </tr>
            </thead>
            <tbody>
              {total && (
                <tr className="border-b border-slate-200 bg-slate-50 font-semibold">
                  <RowCells row={total} />
                </tr>
              )}
              {rows.map((r) => (
                <tr key={r.date} className="border-b border-slate-100">
                  <RowCells row={r} />
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
