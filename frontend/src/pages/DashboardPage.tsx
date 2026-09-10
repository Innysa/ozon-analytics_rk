import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { useStore } from "../store/StoreContext";
import type { Dashboard, DashboardMetric, MarginBlock as MarginBlockType } from "../types";

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
  return d.toISOString().slice(0, 10);
}

function defaultDateTo(): string {
  return isoDate(new Date());
}

function defaultDateFrom(): string {
  const d = new Date();
  d.setDate(d.getDate() - 29);
  return isoDate(d);
}

export function DashboardPage() {
  const { currentStore } = useStore();
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [loading, setLoading] = useState(false);
  const [dateFrom, setDateFrom] = useState(defaultDateFrom);
  const [dateTo, setDateTo] = useState(defaultDateTo);

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
            <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
              <MetricCard label="Заказы, шт." metric={dashboard.orders_revenue.orders} format={fmtInt} />
              <MetricCard label="Выручка" metric={dashboard.orders_revenue.revenue_rub} format={fmtRub} />
              <Stat label="Средний чек" value={fmtRub(dashboard.orders_revenue.avg_order_value_rub)} />
            </div>
            <p className="mt-2 text-xs text-slate-400">
              Источник:{" "}
              {dashboard.orders_revenue.source === "ozon_seller_api"
                ? "автоматически, Ozon Seller API (та же синхронизация, что и «РНП»/«Маржа»)."
                : "отчёт «Аналитика → Товары» (CSV/XLSX), загруженный вручную."}{" "}
              Показано «заказано» (на момент заказа), а не «выкуплено» — выкуп и его маржу с учётом себестоимости
              смотрите в блоке «Маржа» ниже, эти цифры не обязаны совпадать.
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
              <MetricCard label="Расход (авто, Performance API)" metric={dashboard.advertising.spend_auto_rub} format={fmtRub} />
              <MetricCard label="Расход (загружено вручную)" metric={dashboard.advertising.spend_manual_rub} format={fmtRub} />
              <Stat label="Доля расходов на рекламу в выручке" value={fmtPct(dashboard.advertising.spend_share_of_revenue_pct)} />
            </div>
            <p className="mt-2 text-xs text-slate-400">
              «Доля расходов на рекламу в выручке» — это весь расход на рекламу (оба источника выше), делённый на всю
              выручку магазина за период (блок «Заказы и выручка» выше), а не только на продажи в продвижении — это
              главный показатель, за которым обычно следят руководители, но именно поэтому его нельзя напрямую
              сравнивать с ДРР по отдельным кампаниям на странице «Реклама». Настоящую маржу/ROI с учётом
              себестоимости смотрите в блоке «Маржа» ниже.
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
