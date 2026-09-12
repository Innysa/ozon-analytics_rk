import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { useStore } from "../store/StoreContext";
import type { Dashboard, DashboardMetric, MarginBlock as MarginBlockType, SyncRun } from "../types";

function fmtRub(v: number | null): string {
  if (v === null) return "Нет данных";
  return `${v.toLocaleString("ru-RU", { maximumFractionDigits: 2 })} ₽`;
}

function fmtPct(v: number | null): string {
  if (v === null) return "Нет данных";
  return `${v.toLocaleString("ru-RU", { maximumFractionDigits: 2 })}%`;
}

function fmtInt(v: number | null): string {
  if (v === null) return "Нет данных";
  return v.toLocaleString("ru-RU", { maximumFractionDigits: 0 });
}

function isoDate(d: Date): string {
  // Local calendar date, NOT toISOString().slice(0, 10) — that converts to
  // UTC first, which silently shifts the 1st of the month back to the last
  // day of the PREVIOUS month for any timezone ahead of UTC (e.g. Moscow,
  // UTC+3: local midnight Sept 1 is Aug 31 21:00 UTC). Confirmed as the
  // actual cause of defaultDateFrom() below not landing on the 1st for a
  // real user (2026-09-11).
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

export function DashboardPage() {
  const { currentStore } = useStore();
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [loading, setLoading] = useState(false);
  const [dateFrom, setDateFrom] = useState(defaultDateFrom);
  const [dateTo, setDateTo] = useState(defaultDateTo);
  const [syncingLogistics, setSyncingLogistics] = useState(false);
  const [logisticsNotice, setLogisticsNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!currentStore) return;
    setLoading(true);
    try {
      const params = new URLSearchParams({ date_from: dateFrom, date_to: dateTo });
      const data = await api.get<Dashboard>(`/stores/${currentStore.id}/dashboard?${params.toString()}`);
      setDashboard(data);
    } finally {
      setLoading(false);
    }
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

  const syncLogistics = async () => {
    setSyncingLogistics(true);
    setLogisticsNotice("Запуск автосбора логистики/услуг через Ozon Seller API...");
    try {
      const run = await api.post<SyncRun>(`/stores/${currentStore.id}/sync/ozon-cash-flow-statement`);
      setLogisticsNotice("Синхронизация выполняется в фоне...");
      const finished = await pollSyncRun(run.id);
      if (!finished) {
        setLogisticsNotice("Не удалось получить статус синхронизации — обновите страницу.");
      } else if (finished.status === "failed") {
        setLogisticsNotice(`Автосбор не удался: ${finished.error_message ?? "неизвестная ошибка"}`);
      } else if (finished.status === "running") {
        setLogisticsNotice("Синхронизация всё ещё выполняется — проверьте журнал синхронизаций позже.");
      } else {
        setLogisticsNotice(
          `Готово: получено периодов ${finished.items_fetched}, создано ${finished.items_created}, обновлено ${finished.items_skipped_duplicate}.` +
            (finished.error_message ? ` Предупреждения: ${finished.error_message}` : "")
        );
      }
      load();
    } catch (err) {
      setLogisticsNotice(err instanceof ApiError ? err.message : "Ошибка автосбора логистики/услуг");
    } finally {
      setSyncingLogistics(false);
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-lg font-semibold text-slate-800">Дашборд — {currentStore.name}</h1>
        <div className="flex items-center gap-1 rounded-md border border-slate-200 bg-white px-2 py-1">
          <input
            type="date"
            value={dateFrom}
            onChange={(e) => setDateFrom(e.target.value)}
            max={dateTo}
            className="text-xs text-slate-600 outline-none"
            aria-label="Период: от"
          />
          <span className="text-xs text-slate-400">—</span>
          <input
            type="date"
            value={dateTo}
            onChange={(e) => setDateTo(e.target.value)}
            min={dateFrom}
            className="text-xs text-slate-600 outline-none"
            aria-label="Период: до"
          />
        </div>
      </div>

      {loading || !dashboard ? (
        <div className="text-slate-500">Загрузка...</div>
      ) : (
        <>
          <div className="text-xs text-slate-500">
            Период: {dashboard.period_start} — {dashboard.period_end}. Сравнение — с таким же по длине предыдущим
            периодом ({dashboard.previous_period_start} — {dashboard.previous_period_end}).
          </div>

          {logisticsNotice && <div className="rounded-md bg-slate-50 p-2 text-xs text-slate-600">{logisticsNotice}</div>}

          <DashboardSection
            title="Заказы и выручка"
            hasData={dashboard.orders_revenue.has_data}
            emptyHint={
              <>
                Нет данных. Соберите заказы на странице{" "}
                <Link to="/rnp" className="underline">
                  «РНП»
                </Link>{" "}
                (кнопка «Обновить заказы (авто)») или загрузите отчёт «Аналитика → Товары» на странице{" "}
                <Link to="/products" className="underline">
                  «Товары»
                </Link>
                .
              </>
            }
          >
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
              <MetricCard label="Заказы, шт." metric={dashboard.orders_revenue.orders} format={fmtInt} />
              <MetricCard label="Выручка" metric={dashboard.orders_revenue.revenue_rub} format={fmtRub} />
              <Stat label="Средний чек" value={fmtRub(dashboard.orders_revenue.avg_order_value_rub)} />
              <Stat label="Процент выкупа" value={fmtPct(dashboard.orders_revenue.buyout_pct)} />
            </div>
            <p className="mt-2 text-xs text-slate-400">
              Источник:{" "}
              {dashboard.orders_revenue.source === "ozon_seller_api"
                ? "автоматически, Ozon Seller API (та же синхронизация, что и «РНП»/«Маржа»)."
                : "отчёт «Аналитика → Товары» (CSV/XLSX), загруженный вручную."}{" "}
              «Заказы», «Выручка» и «Средний чек» — это «заказано» (на момент заказа), а не «выкуплено»; «Процент
              выкупа» — единственная цифра здесь, которая уже учитывает и то, и другое (доля заказанных штук,
              реально дошедших до покупателя). Абсолютные суммы выкупа и маржу с учётом себестоимости смотрите в
              блоке «Маржа» ниже — эти цифры не обязаны совпадать с «Выручкой» выше.
            </p>
          </DashboardSection>

          <DashboardSection
            title="Реклама"
            hasData={dashboard.advertising.has_data}
            emptyHint={
              <>
                Нет данных. Соберите статистику или загрузите отчёт на странице{" "}
                <Link to="/advertising" className="underline">
                  «Реклама»
                </Link>
                .
              </>
            }
          >
            <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
              <MetricCard label="Расход на рекламу (авто, Performance API)" metric={dashboard.advertising.spend_auto_rub} format={fmtRub} />
              {dashboard.advertising.spend_manual_rub && (
                <MetricCard label="Расход (загружено вручную)" metric={dashboard.advertising.spend_manual_rub} format={fmtRub} />
              )}
              <Stat label="Доля расходов на рекламу в выручке" value={fmtPct(dashboard.advertising.spend_share_of_revenue_pct)} />
            </div>
            <p className="mt-2 text-xs text-slate-400">
              «Доля расходов на рекламу в выручке» — это весь расход на рекламу выше, делённый на всю выручку
              магазина за период (блок «Заказы и выручка» выше), а не только на продажи в продвижении — это главный
              показатель, за которым обычно следят руководители, но именно поэтому его нельзя напрямую сравнивать с
              ДРР по отдельным кампаниям на странице «Реклама». Настоящую маржу/ROI с учётом себестоимости смотрите
              в блоке «Маржа» ниже.
            </p>
          </DashboardSection>

          <DashboardSection
            title="Маржа"
            hasData={dashboard.margin.has_data}
            emptyHint={
              <>
                Нет данных. Соберите заказы на странице{" "}
                <Link to="/rnp" className="underline">
                  «РНП»
                </Link>{" "}
                (кнопка «Обновить заказы (авто)»).
              </>
            }
          >
            <MarginSection margin={dashboard.margin} />
          </DashboardSection>

          <DashboardSection
            title="Остатки товаров"
            hasData={dashboard.inventory.has_data}
            emptyHint={
              <>
                Нет данных. Синхронизируйте каталог на странице{" "}
                <Link to="/products" className="underline">
                  «Товары»
                </Link>{" "}
                (кнопка «Синхронизировать с Ozon»).
              </>
            }
          >
            <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
              <Stat label="Остаток, всего шт." value={fmtInt(dashboard.inventory.total_units)} />
              <Stat label="Остаток на FBO, шт." value={fmtInt(dashboard.inventory.fbo_units)} />
              <Stat label="Остаток на FBS, шт." value={fmtInt(dashboard.inventory.fbs_units)} />
            </div>
            <p className="mt-2 text-xs text-slate-400">
              Текущий остаток на складах Ozon (без учёта архивных товаров) — снимок на сейчас, не за выбранный период,
              из последней синхронизации каталога на странице «Товары».
            </p>
          </DashboardSection>

          <DashboardSection
            title="Логистика и услуги"
            hasData={dashboard.logistics.has_data}
            emptyHint={
              <>
                Нет данных.{" "}
                <button
                  onClick={syncLogistics}
                  disabled={syncingLogistics}
                  className="rounded-md bg-indigo-100 px-3 py-1.5 text-xs font-medium text-indigo-700 hover:bg-indigo-200 disabled:opacity-50"
                >
                  {syncingLogistics ? "Синхронизация..." : "Обновить логистику/услуги (авто)"}
                </button>
              </>
            }
          >
            <div className="mb-3 flex items-center justify-between gap-3">
              <p className="text-xs text-slate-400">
                Источник: автоматически, Ozon Seller API (отчёт ДДС, POST /v1/finance/cash-flow-statement/list) —
                заменил отключённый Ozon метод для финансовых операций.
              </p>
              <button
                onClick={syncLogistics}
                disabled={syncingLogistics}
                className="shrink-0 rounded-md bg-indigo-100 px-3 py-1.5 text-xs font-medium text-indigo-700 hover:bg-indigo-200 disabled:opacity-50"
              >
                {syncingLogistics ? "Синхронизация..." : "Обновить (авто)"}
              </button>
            </div>
            {dashboard.logistics.periods_summed > 0 ? (
              <>
                {dashboard.logistics.is_estimated && (
                  <p className="mb-2 rounded-md bg-amber-50 p-2 text-xs text-amber-700">
                    ≈ Оценка: выбранный период не совпадает с недельными периодами Ozon — часть сумм ниже посчитана
                    пропорционально дням, а не взята из точного отчёта Ozon.
                  </p>
                )}
                <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
                  <Stat label="Логистика" value={fmtRub(dashboard.logistics.logistics_rub)} />
                  <Stat label="Обработка возвратов" value={fmtRub(dashboard.logistics.returns_logistics_rub)} />
                  <Stat label="Хранение" value={fmtRub(dashboard.logistics.storage_rub)} />
                  <Stat label="Штрафы" value={fmtRub(dashboard.logistics.fines_rub)} />
                  <Stat label="Прочие удержания" value={fmtRub(dashboard.logistics.other_deductions_rub)} />
                  <Stat label="Прочие услуги" value={fmtRub(dashboard.logistics.other_services_rub)} />
                </div>
                {dashboard.logistics.other_services_top_item_name && (
                  <p className="mt-2 text-xs text-slate-500">
                    Крупнейшая отдельная статья внутри «Прочие услуги»: {dashboard.logistics.other_services_top_item_name} —{" "}
                    {fmtRub(dashboard.logistics.other_services_top_item_rub)}. Такие статьи (например, агентская
                    комиссия) у Ozon бывают крупными и волатильными в отдельные недели — это не обязательно ошибка.
                  </p>
                )}
                <p className="mt-2 text-xs text-slate-400">
                  {dashboard.logistics.period_note}. «Логистика» — доставка (последняя миля, приём в пункте
                  приёма, магистраль); «Обработка возвратов» — расходы на возврат товара (например, через пункт
                  выдачи); «Хранение» и «Штрафы» — статьи услуг Ozon, распознанные по названию (например,
                  «...TemporaryStorage...», «Fines...»); «Прочие удержания» — отдельная статья Ozon (например,
                  эквайринг, компенсации), не входящая в услуги; «Прочие услуги» — то, что осталось (реклама за
                  клик, страхование и др.) — новое, ранее не встречавшееся название статьи попадёт именно сюда, а
                  не будет угадано в одну из категорий выше. Ozon группирует эти цифры собственными периодами
                  (обычно неделя), которые не всегда совпадают с выбранным периодом дашборда — период, попавший в
                  диапазон лишь частично, всё равно учтён, но пропорционально дням (см. пометку «Оценка» выше).
                </p>
              </>
            ) : (
              <div className="rounded-md border border-dashed border-slate-300 bg-white p-4 text-center text-sm text-slate-500">
                {dashboard.logistics.period_note ?? "Нет периодов Ozon, пересекающихся с выбранным диапазоном."} Попробуйте
                более широкий период.
              </div>
            )}
          </DashboardSection>

          <DashboardSection
            title="Отзывы"
            hasData={dashboard.reviews.has_data}
            emptyHint={
              <>
                Нет данных. Загрузите отзывы или синхронизируйтесь с Ozon на странице{" "}
                <Link to="/reviews" className="underline">
                  «Отзывы»
                </Link>
                .
              </>
            }
          >
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
              <MetricCard label="Новых отзывов" metric={dashboard.reviews.new_count} format={fmtInt} />
              <Stat label="Средняя оценка за период" value={dashboard.reviews.avg_rating_current?.toFixed(2) ?? "Нет данных"} />
              <Stat
                label="Средняя оценка (пред. период)"
                value={dashboard.reviews.avg_rating_previous?.toFixed(2) ?? "Нет данных"}
              />
              <Stat label="Без ответа (всего)" value={fmtInt(dashboard.reviews.without_reply_count)} />
            </div>
          </DashboardSection>
        </>
      )}
    </div>
  );
}

function DashboardSection({
  title,
  hasData,
  emptyHint,
  children,
}: {
  title: string;
  hasData: boolean;
  emptyHint: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-md border border-slate-200 bg-white p-4">
      <h2 className="mb-3 text-sm font-semibold text-slate-700">{title}</h2>
      {hasData ? children : <div className="text-sm text-slate-500">{emptyHint}</div>}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-slate-200 bg-slate-50 p-3">
      <div className="text-xs text-slate-500">{label}</div>
      <div className="mt-1 text-lg font-semibold text-slate-800">{value}</div>
    </div>
  );
}

function MarginSection({ margin }: { margin: MarginBlockType }) {
  return (
    <div>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Выкуплено, шт." value={fmtInt(margin.delivered_units)} />
        <Stat label="Выручка (выкуп)" value={fmtRub(margin.delivered_sum_rub)} />
        <Stat label="Комиссия Ozon" value={fmtRub(margin.commission_rub)} />
        <Stat
          label="Себестоимость выкупа"
          value={margin.cost_known ? fmtRub(margin.cost_of_delivered_rub) : "Указана не для всех товаров"}
        />
        <Stat label="Маржа" value={margin.cost_known ? fmtRub(margin.margin_rub) : "Нет данных"} />
        <Stat label="Маржа, %" value={margin.cost_known ? fmtPct(margin.margin_pct) : "Нет данных"} />
      </div>
      {!margin.cost_known && (
        <p className="mt-2 text-xs text-slate-400">
          Чтобы увидеть маржу, укажите себестоимость на карточках{" "}
          <Link to="/products" className="underline">
            товаров
          </Link>
          , которые продавались в этом периоде — без неё расчёт был бы неверным, а не просто приблизительным, поэтому
          он не показывается вовсе.
        </p>
      )}
      <p className="mt-2 text-xs text-slate-400">
        Маржа = выручка (выкуп) − комиссия Ozon − себестоимость выкупленных товаров − расход на рекламу (оба
        источника выше). Источник — заказы FBO/FBS из Ozon Seller API, автоматически (страница «РНП»).
      </p>
    </div>
  );
}

function MetricCard({
  label,
  metric,
  format,
}: {
  label: string;
  metric: DashboardMetric | null;
  format: (v: number | null) => string;
}) {
  if (!metric) return <Stat label={label} value="Нет данных" />;
  const color =
    metric.direction === "up" ? "text-green-600" : metric.direction === "down" ? "text-red-600" : "text-slate-400";
  const arrow = metric.direction === "up" ? "▲" : metric.direction === "down" ? "▼" : "—";
  return (
    <div className="rounded-md border border-slate-200 bg-slate-50 p-3">
      <div className="text-xs text-slate-500">{label}</div>
      <div className="mt-1 text-lg font-semibold text-slate-800">{format(metric.current)}</div>
      <div className={`mt-0.5 text-xs ${color}`}>
        {arrow} {metric.delta_pct !== null ? `${metric.delta_pct > 0 ? "+" : ""}${metric.delta_pct}%` : "н/д"} к пред.
        периоду
      </div>
    </div>
  );
}
