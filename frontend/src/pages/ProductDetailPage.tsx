import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { useStore } from "../store/StoreContext";
import type {
  AdvertisingAnalytics,
  Product,
  ProductAdCampaignBreakdown,
  ProductAdvertisingAutoDaily,
  ProductAnalyticsDailyStatistic,
  ProductAnalyticsDailyStatisticListResponse,
  ProductCampaignDailyListResponse,
  ProductCampaignDailyRow,
  ProductCardAnalytics,
  ProductCardStatisticListResponse,
  ProductOrderDailyStatistic,
  ProductOrderDailyStatisticListResponse,
  Review,
  ReviewAnalytics,
  ReviewListResponse,
  SearchQueryAnalytics,
} from "../types";

const AD_STATE_LABELS: Record<string, string> = {
  CAMPAIGN_STATE_RUNNING: "Активна",
  CAMPAIGN_STATE_INACTIVE: "На паузе",
  CAMPAIGN_STATE_ARCHIVED: "В архиве",
};
import { ReviewCard } from "../components/ReviewCard";
import { ChangeHistoryPanel } from "../components/ChangeHistoryPanel";

type Tab = "overview" | "reviews" | "analytics" | "ads" | "sales" | "search" | "history" | "recommendations";

const TABS: { key: Tab; label: string; planned?: boolean }[] = [
  { key: "overview", label: "Обзор" },
  { key: "reviews", label: "Отзывы" },
  { key: "analytics", label: "Аналитика отзывов" },
  { key: "ads", label: "Реклама" },
  { key: "sales", label: "Продажи" },
  { key: "search", label: "Поисковые запросы" },
  { key: "history", label: "История изменений" },
  { key: "recommendations", label: "Рекомендации ИИ" },
];

export function ProductDetailPage() {
  const { productId } = useParams<{ productId: string }>();
  const { currentStore } = useStore();
  const [tab, setTab] = useState<Tab>("overview");
  const [product, setProduct] = useState<Product | null>(null);

  useEffect(() => {
    if (!currentStore || !productId) return;
    api.get<Product>(`/stores/${currentStore.id}/products/${productId}`).then(setProduct);
  }, [currentStore, productId]);

  if (!currentStore || !productId) return null;

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        {product?.image_url ? (
          <img src={product.image_url} alt="" className="h-14 w-14 rounded object-cover" />
        ) : (
          <div className="flex h-14 w-14 items-center justify-center rounded bg-slate-100 text-xs text-slate-400">нет фото</div>
        )}
        <div>
          <h1 className="text-lg font-semibold text-slate-800">{product?.name ?? "Загрузка..."}</h1>
          <div className="text-xs text-slate-500">SKU {product?.ozon_sku} {product?.offer_id ? `· Артикул ${product.offer_id}` : ""}</div>
        </div>
      </div>

      <div className="flex flex-wrap gap-1 border-b border-slate-200">
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`rounded-t-md px-3 py-2 text-sm ${
              tab === t.key ? "border-b-2 border-indigo-600 font-medium text-indigo-700" : "text-slate-500 hover:text-slate-700"
            }`}
          >
            {t.label} {t.planned && <span className="text-xs text-slate-400">(запланировано)</span>}
          </button>
        ))}
      </div>

      <div>
        {tab === "overview" && (
          <OverviewTab product={product} storeId={currentStore.id} productId={productId} onChanged={setProduct} />
        )}
        {tab === "reviews" && <ProductReviewsTab storeId={currentStore.id} productId={productId} />}
        {tab === "analytics" && <ProductAnalyticsTab storeId={currentStore.id} productId={productId} />}
        {tab === "ads" && <ProductAdsTab storeId={currentStore.id} productId={productId} />}
        {tab === "sales" && <ProductSalesTab storeId={currentStore.id} productId={productId} />}
        {tab === "search" && <ProductSearchQueriesTab storeId={currentStore.id} productId={productId} />}
        {tab === "history" && <ChangeHistoryPanel storeId={currentStore.id} productId={productId} />}
        {tab === "recommendations" && <ProductAnalyticsTab storeId={currentStore.id} productId={productId} recommendationsOnly />}
      </div>
    </div>
  );
}

function OverviewTab({
  product,
  storeId,
  productId,
  onChanged,
}: {
  product: Product | null;
  storeId: string;
  productId: string;
  onChanged: (p: Product) => void;
}) {
  const [costPriceInput, setCostPriceInput] = useState("");
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [ratingSummary, setRatingSummary] = useState<ProductCardAnalytics | null>(null);

  useEffect(() => {
    setCostPriceInput(product?.cost_price_rub != null ? String(product.cost_price_rub) : "");
  }, [product?.id, product?.cost_price_rub]);

  useEffect(() => {
    api.get<ProductCardAnalytics>(`/stores/${storeId}/product-analytics/summary?product_id=${productId}`).then(setRatingSummary);
  }, [storeId, productId]);

  if (!product) return <div className="text-slate-500">Загрузка...</div>;

  const saveCostPrice = async () => {
    setSaving(true);
    setNotice(null);
    try {
      const value = costPriceInput.trim() === "" ? null : costPriceInput.trim();
      const updated = await api.put<Product>(`/stores/${storeId}/products/${product.id}/cost-price`, {
        cost_price_rub: value,
      });
      onChanged(updated);
      setNotice("Сохранено");
    } catch {
      setNotice("Не удалось сохранить — проверьте значение (должно быть числом ≥ 0)");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="rounded-md border border-slate-200 bg-white p-4 text-sm text-slate-700">
      <dl className="grid grid-cols-2 gap-2">
        <dt className="text-slate-500">Название</dt>
        <dd>{product.name}</dd>
        <dt className="text-slate-500">SKU</dt>
        <dd>{product.ozon_sku}</dd>
        <dt className="text-slate-500">Артикул продавца</dt>
        <dd>{product.offer_id ?? "Нет данных"}</dd>
        <dt className="text-slate-500">Цена (Ozon)</dt>
        <dd>{product.price_rub != null ? `${product.price_rub.toLocaleString("ru-RU")} ₽` : "Нет данных"}</dd>
      </dl>

      <RatingSection summary={ratingSummary} />

      <div className="mt-4 border-t border-slate-100 pt-4">
        <div className="text-xs font-semibold text-slate-700">Себестоимость</div>
        <p className="mt-1 text-xs text-slate-500">
          Ozon не отдаёт закупочную/производственную цену товара ни по одному API — это ваши собственные данные,
          укажите их здесь один раз, чтобы приложение могло считать маржу и ROI на Дашборде.
        </p>
        <div className="mt-2 flex items-center gap-2">
          <input
            type="number"
            min={0}
            step="0.01"
            value={costPriceInput}
            onChange={(e) => setCostPriceInput(e.target.value)}
            placeholder="Например, 450"
            className="w-40 rounded-md border border-slate-300 px-2 py-1 text-sm"
          />
          <span className="text-sm text-slate-500">₽</span>
          <button
            onClick={saveCostPrice}
            disabled={saving}
            className="rounded-md bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
          >
            {saving ? "Сохранение..." : "Сохранить"}
          </button>
          {notice && <span className="text-xs text-slate-500">{notice}</span>}
        </div>
      </div>
    </div>
  );
}

// Sourced from ProductCardStatistic (the "Аналитика → Товары" report Ozon
// itself generates) — this is Ozon's own reported rating/review count, not
// our own average of synced reviews. We checked whether the Ozon Seller API
// product-info endpoint used for daily product sync (/v3/product/info/list)
// carries a live rating field — it does not (see OzonProductInfoItem in
// backend/app/services/ozon/schemas.py). No other confirmed Ozon Seller API
// endpoint exposes it either, so there is currently no way to collect it
// automatically without the report upload. History still accumulates on its
// own, one point per uploaded day, without any extra code — every new
// report you upload adds another day to rating_trend below.
function RatingSection({ summary }: { summary: ProductCardAnalytics | null }) {
  if (!summary || !summary.has_data || summary.latest_rating === null) {
    return (
      <div className="mt-4 border-t border-slate-100 pt-4">
        <div className="text-xs font-semibold text-slate-700">Рейтинг товара</div>
        <p className="mt-1 text-xs text-slate-500">
          Нет данных. Ozon Seller API не отдаёт рейтинг товара напрямую — загрузите отчёт «Аналитика → Товары» на
          странице{" "}
          <Link to="/products" className="text-indigo-600 underline">
            «Товары»
          </Link>
          , в нём Ozon указывает рейтинг и количество отзывов по дням; при каждой новой загрузке история будет
          пополняться.
        </p>
      </div>
    );
  }

  const trend = summary.rating_trend.slice(-14);

  return (
    <div className="mt-4 border-t border-slate-100 pt-4">
      <div className="text-xs font-semibold text-slate-700">Рейтинг товара</div>
      <div className="mt-1 flex items-baseline gap-2">
        <span className="text-2xl font-semibold text-amber-500">★ {summary.latest_rating.toFixed(1)}</span>
        <span className="text-sm text-slate-500">
          {summary.latest_reviews_count !== null ? `${summary.latest_reviews_count.toLocaleString("ru-RU")} отзывов` : "количество отзывов неизвестно"}
        </span>
      </div>
      <p className="mt-1 text-xs text-slate-400">
        По данным отчёта Ozon «Аналитика → Товары» на {summary.date_to}. Собственного API для рейтинга у Ozon нет —
        значение накапливается по дням при каждой новой загрузке отчёта.
      </p>
      {trend.length > 1 && (
        <div className="mt-2 overflow-x-auto">
          <table className="text-left text-xs">
            <thead>
              <tr className="text-slate-500">
                {trend.map((p) => (
                  <th key={p.date} className="px-2 py-1 font-normal">
                    {p.date.slice(5)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              <tr>
                {trend.map((p) => (
                  <td key={p.date} className="px-2 py-1 font-medium text-slate-700">
                    {p.rating.toFixed(1)}
                  </td>
                ))}
              </tr>
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function ProductReviewsTab({ storeId, productId }: { storeId: string; productId: string }) {
  const [reviews, setReviews] = useState<Review[]>([]);
  const [loading, setLoading] = useState(true);

  const load = () => {
    setLoading(true);
    api
      .get<ReviewListResponse>(`/stores/${storeId}/reviews?product_id=${productId}`)
      .then((d) => setReviews(d.items))
      .finally(() => setLoading(false));
  };

  useEffect(load, [storeId, productId]);

  if (loading) return <div className="text-slate-500">Загрузка...</div>;
  if (reviews.length === 0) return <div className="text-slate-500">Нет данных</div>;

  return (
    <div className="space-y-3">
      {reviews.map((r) => (
        <ReviewCard key={r.id} review={r} storeId={storeId} onChanged={load} />
      ))}
    </div>
  );
}

function ProductAnalyticsTab({
  storeId,
  productId,
  recommendationsOnly,
}: {
  storeId: string;
  productId: string;
  recommendationsOnly?: boolean;
}) {
  const [data, setData] = useState<ReviewAnalytics | null>(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const load = () => {
    api.get<ReviewAnalytics>(`/stores/${storeId}/analytics/reviews?product_id=${productId}`).then(setData);
  };

  useEffect(load, [storeId, productId]);

  const analyzeProductReviews = async () => {
    setAnalyzing(true);
    setNotice("Анализ отзывов через ИИ...");
    try {
      const result = await api.post<{ succeeded: number; failed: number; skipped: number }>(
        `/stores/${storeId}/reviews/bulk/analyze-product?product_id=${productId}`
      );
      setNotice(
        `Готово: проанализировано ${result.succeeded}, ошибок ${result.failed}, уже было проанализировано ${result.skipped}.`
      );
      load();
    } catch (err) {
      setNotice(err instanceof ApiError ? err.message : "Ошибка анализа отзывов");
    } finally {
      setAnalyzing(false);
    }
  };

  if (!data) return <div className="text-slate-500">Загрузка...</div>;
  if (!data.has_data) return <div className="text-slate-500">Нет данных</div>;

  if (recommendationsOnly) {
    return (
      <div className="space-y-4 rounded-md border border-slate-200 bg-white p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-xs italic text-slate-500">
            Рекомендации сформированы ИИ на основе анализа отзывов и являются гипотезами, требующими проверки человеком —
            не воспринимайте их как доказанные факты.
          </p>
          <button
            onClick={analyzeProductReviews}
            disabled={analyzing}
            className="shrink-0 rounded-md bg-indigo-50 px-3 py-1.5 text-xs font-medium text-indigo-700 hover:bg-indigo-100 disabled:opacity-50"
          >
            {analyzing ? "Анализ..." : "Проанализировать отзывы товара (ИИ)"}
          </button>
        </div>
        {notice && <p className="text-xs text-slate-500">{notice}</p>}
        <p className="text-xs text-slate-400">
          Рекомендации строятся по уже проанализированным ИИ отзывам этого товара. Отзыв, к которому ещё не применяли
          анализ (ни здесь, ни кнопкой «Проанализировать» на странице «Отзывы»), в них не попадёт — нажмите кнопку выше,
          чтобы проанализировать сразу все непроанализированные отзывы товара.
        </p>
        <Section title="Рекомендации по товару" items={data.product_improvement_ideas} />
        <Section title="Рекомендации по карточке товара" items={data.card_improvement_ideas} />
        <Section title="Идеи для инфографики" items={data.infographic_ideas} />
      </div>
    );
  }

  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
      <Stat label="Отзывов" value={data.total_reviews} />
      <Stat label="Средняя оценка" value={data.average_rating ?? "—"} />
      <Stat label="Доля оценок 1–3" value={data.low_rating_share !== null ? `${Math.round(data.low_rating_share * 100)}%` : "—"} />
      <Stat label="Без ответа" value={data.reviews_without_reply} />
    </div>
  );
}

function fmtRub(v: number | null): string {
  if (v === null) return "Нет данных";
  return `${v.toLocaleString("ru-RU", { maximumFractionDigits: 2 })} ₽`;
}

function fmtPct(v: number | null): string {
  if (v === null) return "Нет данных";
  return `${v.toLocaleString("ru-RU", { maximumFractionDigits: 2 })}%`;
}

function ProductAdsTab({ storeId, productId }: { storeId: string; productId: string }) {
  const [auto, setAuto] = useState<ProductAdvertisingAutoDaily | null>(null);
  const [csv, setCsv] = useState<AdvertisingAnalytics | null>(null);

  useEffect(() => {
    api.get<ProductAdvertisingAutoDaily>(`/stores/${storeId}/advertising/product-auto-daily?product_id=${productId}`).then(setAuto);
    api.get<AdvertisingAnalytics>(`/stores/${storeId}/advertising/analytics?product_id=${productId}`).then(setCsv);
  }, [storeId, productId]);

  if (!auto || !csv) return <div className="text-slate-500">Загрузка...</div>;

  if (!auto.has_data && !csv.has_data) {
    return (
      <div className="rounded-md border border-slate-200 bg-white p-6 text-center text-slate-500">
        Нет данных о рекламе для этого товара. Соберите статистику или загрузите отчёт на странице{" "}
        <Link to="/advertising" className="text-indigo-600 underline">
          «Реклама»
        </Link>
        .
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {auto.has_data && (
        <div>
          <h3 className="mb-2 text-sm font-semibold text-slate-700">Автосбор (Ozon Performance API)</h3>
          <div className="mb-3 grid grid-cols-2 gap-3 md:grid-cols-4">
            <Stat label="Расход" value={fmtRub(auto.total_spend_rub)} />
            <Stat label="Показы" value={auto.total_impressions.toLocaleString("ru-RU")} />
            <Stat label="Клики" value={auto.total_clicks.toLocaleString("ru-RU")} />
            <Stat label="Заказы (в продвижении)" value={auto.total_orders.toLocaleString("ru-RU")} />
            <Stat label="Продажи (в продвижении)" value={fmtRub(auto.total_revenue_rub)} />
            <Stat label="ДРР (рассчитано)" value={fmtPct(auto.drr_calculated_pct)} />
            <Stat label="ROAS (рассчитано)" value={auto.roas_calculated !== null ? `×${auto.roas_calculated}` : "Нет данных"} />
          </div>
          {auto.daily_comparison_unavailable_reason && (
            <p className="mb-2 text-xs italic text-slate-400">{auto.daily_comparison_unavailable_reason}</p>
          )}
          {auto.by_campaign.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="border-b border-slate-200 text-xs text-slate-500">
                    <th className="w-4 py-1" />
                    <th>Кампания</th>
                    <th>Статус</th>
                    <th>Расход</th>
                    <th>Показы</th>
                    <th>Клики</th>
                    <th>Заказы</th>
                    <th>Продажи</th>
                    <th>ДРР</th>
                  </tr>
                </thead>
                <tbody>
                  {auto.by_campaign.map((c) => (
                    <ProductCampaignRow key={c.campaign_id} storeId={storeId} productId={productId} campaign={c} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {csv.has_data && (
        <div>
          <h3 className="mb-2 text-sm font-semibold text-slate-700">Загруженная статистика (CSV/XLSX)</h3>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Stat label="Расход" value={fmtRub(csv.total_spend_rub)} />
            <Stat label="Продажи (в продвижении)" value={fmtRub(csv.total_sales_promo_rub)} />
            <Stat label="Показы" value={csv.total_impressions.toLocaleString("ru-RU")} />
            <Stat label="Клики" value={csv.total_clicks.toLocaleString("ru-RU")} />
            <Stat label="ДРР (рассчитано)" value={fmtPct(csv.drr_calculated_pct)} />
            <Stat label="ROAS (рассчитано)" value={csv.roas_calculated !== null ? `×${csv.roas_calculated}` : "Нет данных"} />
          </div>
          {csv.by_campaign.length > 0 && (
            <div className="mt-3 overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="border-b border-slate-200 text-xs text-slate-500">
                    <th className="py-1">Кампания</th>
                    <th>Расход</th>
                    <th>Продажи</th>
                    <th>ДРР</th>
                  </tr>
                </thead>
                <tbody>
                  {csv.by_campaign.map((c) => (
                    <tr key={c.campaign_id} className="border-b border-slate-100">
                      <td className="py-1">{c.campaign_name}</td>
                      <td>{fmtRub(c.spend_rub)}</td>
                      <td>{fmtRub(c.sales_promo_rub)}</td>
                      <td>{fmtPct(c.drr_calculated_pct)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      <p className="text-xs italic text-slate-500">
        «Автосбор» и «Загруженная статистика» — независимые источники Ozon (см. страницу «Реклама»); их не
        складывают между собой, чтобы не задвоить расход и продажи.
      </p>
    </div>
  );
}

function ProductCampaignRow({
  storeId,
  productId,
  campaign,
}: {
  storeId: string;
  productId: string;
  campaign: ProductAdCampaignBreakdown;
}) {
  const [expanded, setExpanded] = useState(false);
  const [days, setDays] = useState<ProductCampaignDailyRow[] | null>(null);
  const [loading, setLoading] = useState(false);

  const toggle = () => {
    const next = !expanded;
    setExpanded(next);
    if (next && days === null && !loading) {
      setLoading(true);
      api
        .get<ProductCampaignDailyListResponse>(
          `/stores/${storeId}/advertising/product-campaign-daily?product_id=${productId}&ozon_campaign_id=${encodeURIComponent(campaign.campaign_id)}`
        )
        .then((d) => setDays(d.items))
        .finally(() => setLoading(false));
    }
  };

  return (
    <>
      <tr onClick={toggle} className="cursor-pointer border-b border-slate-100 hover:bg-slate-50">
        <td className="w-4 py-1 text-slate-400">{expanded ? "▾" : "▸"}</td>
        <td className="py-1">{campaign.campaign_name}</td>
        <td>{campaign.campaign_state ? AD_STATE_LABELS[campaign.campaign_state] ?? campaign.campaign_state : "—"}</td>
        <td>{fmtRub(campaign.spend_rub)}</td>
        <td>{campaign.impressions.toLocaleString("ru-RU")}</td>
        <td>{campaign.clicks.toLocaleString("ru-RU")}</td>
        <td>{campaign.orders.toLocaleString("ru-RU")}</td>
        <td>{fmtRub(campaign.revenue_rub)}</td>
        <td>{fmtPct(campaign.drr_calculated_pct)}</td>
      </tr>
      {expanded && (
        <tr className="border-b border-slate-100 bg-slate-50">
          <td colSpan={9} className="p-3">
            {loading || days === null ? (
              <div className="text-slate-500">Загрузка...</div>
            ) : days.length === 0 ? (
              <div className="text-slate-500">Нет данных по дням для этой кампании.</div>
            ) : (
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="border-b border-slate-200 text-xs text-slate-500">
                    <th className="py-1">Дата</th>
                    <th>Расход</th>
                    <th>Показы</th>
                    <th>Клики</th>
                    <th>Заказы</th>
                    <th>Продажи</th>
                    <th>ДРР</th>
                  </tr>
                </thead>
                <tbody>
                  {days.map((d) => (
                    <tr key={d.date} className="border-b border-slate-100">
                      <td className="py-1">{d.date}</td>
                      <td>{fmtRub(d.spend_rub)}</td>
                      <td>{d.impressions.toLocaleString("ru-RU")}</td>
                      <td>{d.clicks.toLocaleString("ru-RU")}</td>
                      <td>{d.orders.toLocaleString("ru-RU")}</td>
                      <td>{fmtRub(d.revenue_rub)}</td>
                      <td>{fmtPct(d.drr_calculated_pct)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </td>
        </tr>
      )}
    </>
  );
}

interface DayOrderTotal {
  date: string;
  orderedUnits: number;
  orderedSumRub: number;
  deliveredUnits: number;
  deliveredSumRub: number;
}

// Rows come back split by delivery_schema (FBO/FBS, never merged server-side
// — same convention as the "РНП" page); this tab needs one number per day
// per product, so sum them here.
function combineProductOrderRowsByDate(rows: ProductOrderDailyStatistic[]): DayOrderTotal[] {
  const byDate = new Map<string, DayOrderTotal>();
  for (const r of rows) {
    const acc = byDate.get(r.date) ?? { date: r.date, orderedUnits: 0, orderedSumRub: 0, deliveredUnits: 0, deliveredSumRub: 0 };
    acc.orderedUnits += r.ordered_units;
    acc.orderedSumRub += r.ordered_sum_discounted_rub;
    acc.deliveredUnits += r.delivered_units;
    acc.deliveredSumRub += r.delivered_sum_rub;
    byDate.set(r.date, acc);
  }
  return Array.from(byDate.values()).sort((a, b) => (a.date < b.date ? 1 : -1));
}

function ProductSalesTab({ storeId, productId }: { storeId: string; productId: string }) {
  const [summary, setSummary] = useState<ProductCardAnalytics | null>(null);
  const [rows, setRows] = useState<ProductCardStatisticListResponse | null>(null);
  const [orderRows, setOrderRows] = useState<ProductOrderDailyStatistic[] | null>(null);
  const [funnelRows, setFunnelRows] = useState<ProductAnalyticsDailyStatistic[] | null>(null);

  useEffect(() => {
    api.get<ProductCardAnalytics>(`/stores/${storeId}/product-analytics/summary?product_id=${productId}`).then(setSummary);
    api
      .get<ProductCardStatisticListResponse>(`/stores/${storeId}/product-analytics?product_id=${productId}`)
      .then(setRows);
    api
      .get<ProductOrderDailyStatisticListResponse>(`/stores/${storeId}/orders/product-daily-statistics?product_id=${productId}`)
      .then((d) => setOrderRows(d.items));
    api
      .get<ProductAnalyticsDailyStatisticListResponse>(`/stores/${storeId}/product-analytics/auto?product_id=${productId}`)
      .then((d) => setFunnelRows(d.items));
  }, [storeId, productId]);

  if (!summary || !orderRows || !funnelRows) return <div className="text-slate-500">Загрузка...</div>;

  const orderDays = combineProductOrderRowsByDate(orderRows);
  const orderTotals = orderDays.reduce(
    (acc, d) => ({
      orderedUnits: acc.orderedUnits + d.orderedUnits,
      orderedSumRub: acc.orderedSumRub + d.orderedSumRub,
      deliveredUnits: acc.deliveredUnits + d.deliveredUnits,
      deliveredSumRub: acc.deliveredSumRub + d.deliveredSumRub,
    }),
    { orderedUnits: 0, orderedSumRub: 0, deliveredUnits: 0, deliveredSumRub: 0 }
  );

  if (!summary.has_data && orderDays.length === 0 && funnelRows.length === 0) {
    return (
      <div className="rounded-md border border-slate-200 bg-white p-6 text-center text-slate-500">
        Нет данных. Загрузите отчёт «Аналитика → Товары» на странице{" "}
        <Link to="/products" className="text-indigo-600 underline">
          «Товары»
        </Link>{" "}
        или соберите заказы на странице{" "}
        <Link to="/rnp" className="text-indigo-600 underline">
          «РНП»
        </Link>
        .
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {orderDays.length > 0 && (
        <div>
          <h3 className="mb-2 text-sm font-semibold text-slate-700">Продажи (авто, Ozon Seller API)</h3>
          <div className="mb-3 grid grid-cols-2 gap-3 md:grid-cols-4">
            <Stat label="Заказано, шт" value={orderTotals.orderedUnits.toLocaleString("ru-RU")} />
            <Stat label="Заказано на сумму" value={fmtRub(orderTotals.orderedSumRub)} />
            <Stat label="Выкуплено, шт" value={orderTotals.deliveredUnits.toLocaleString("ru-RU")} />
            <Stat label="Выкуплено на сумму" value={fmtRub(orderTotals.deliveredSumRub)} />
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-slate-200 text-xs text-slate-500">
                  <th className="py-1">Дата</th>
                  <th>Заказано, шт</th>
                  <th>Заказано на сумму</th>
                  <th>Выкуплено, шт</th>
                  <th>Выкуплено на сумму</th>
                </tr>
              </thead>
              <tbody>
                {orderDays.map((d) => (
                  <tr key={d.date} className="border-b border-slate-100">
                    <td className="py-1">{d.date}</td>
                    <td>{d.orderedUnits.toLocaleString("ru-RU")}</td>
                    <td>{fmtRub(d.orderedSumRub)}</td>
                    <td>{d.deliveredUnits.toLocaleString("ru-RU")}</td>
                    <td>{fmtRub(d.deliveredSumRub)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-2 text-xs italic text-slate-500">
            Источник — заказы FBO/FBS из Ozon Seller API, автоматически (страница «РНП»). Не включает показы,
            переходы в карточку/корзину и конверсию — эти показатели Ozon через заказы не отдаёт, см. блок «Воронка»
            ниже.
          </p>
        </div>
      )}

      {funnelRows.length > 0 && (
        <div>
          <h3 className="mb-2 text-sm font-semibold text-slate-700">
            Воронка карточки (авто, Ozon Analytics API — требуется Premium Plus/Premium Pro)
          </h3>
          <div className="mb-3 grid grid-cols-2 gap-3 md:grid-cols-4">
            <Stat
              label="Показы карточки"
              value={funnelRows.reduce((sum, r) => sum + r.views_pdp, 0).toLocaleString("ru-RU")}
            />
            <Stat
              label="Уникальные посетители карточки"
              value={funnelRows.reduce((sum, r) => sum + r.sessions_pdp, 0).toLocaleString("ru-RU")}
            />
            <Stat
              label="Добавлено в корзину с карточки"
              value={funnelRows.reduce((sum, r) => sum + r.cart_adds_pdp, 0).toLocaleString("ru-RU")}
            />
            <Stat
              label="Заказано, шт (Ozon Analytics API)"
              value={funnelRows.reduce((sum, r) => sum + r.ordered_units, 0).toLocaleString("ru-RU")}
            />
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-slate-200 text-xs text-slate-500">
                  <th className="py-1">Дата</th>
                  <th>Показы карточки</th>
                  <th>Посетители</th>
                  <th>В корзину</th>
                  <th>Конверсия в корзину (Ozon)</th>
                  <th>Позиция в категории (Ozon)</th>
                  <th>Выручка (Ozon Analytics API)</th>
                  <th>Заказано, шт (Ozon Analytics API)</th>
                </tr>
              </thead>
              <tbody>
                {funnelRows.map((r) => (
                  <tr key={r.id} className="border-b border-slate-100">
                    <td className="py-1">{r.date}</td>
                    <td>{r.views_pdp.toLocaleString("ru-RU")}</td>
                    <td>{r.sessions_pdp.toLocaleString("ru-RU")}</td>
                    <td>{r.cart_adds_pdp.toLocaleString("ru-RU")}</td>
                    <td>{fmtPct(r.cart_conversion_pdp_pct)}</td>
                    <td>{r.position_category ?? "—"}</td>
                    <td>{fmtRub(r.revenue_rub)}</td>
                    <td>{r.ordered_units.toLocaleString("ru-RU")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-2 text-xs italic text-slate-500">
            Источник — Ozon Seller API, метод «Данные аналитики» (аналог раздела «Аналитика → Графики» в личном
            кабинете Ozon), собирается автоматически. Требует активной подписки Premium Plus/Premium Pro на
            аккаунте — без неё эти показатели Ozon не отдаёт. Это отдельный источник данных от отчёта «Аналитика →
            Товары» (CSV) ниже: методика подсчёта показателей у Ozon для них может отличаться, поэтому цифры не
            обязаны совпадать день в день. Конверсия и позиция — значения Ozon как есть, без пересчёта.
          </p>
        </div>
      )}

      {!summary.has_data ? (
        <div className="rounded-md border border-slate-200 bg-white p-4 text-center text-slate-500">
          Нет данных о показах/переходах/конверсиях. Загрузите отчёт «Аналитика → Товары» на странице{" "}
          <Link to="/products" className="text-indigo-600 underline">
            «Товары»
          </Link>
          .
        </div>
      ) : (
        <div>
      <div className="text-xs text-slate-500">
        Период данных: {summary.date_from} — {summary.date_to}
      </div>

      <div className="mt-3 grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Показы (факт)" value={summary.total_impressions.toLocaleString("ru-RU")} />
        <Stat label="Переходы в карточку (факт)" value={summary.total_card_visits.toLocaleString("ru-RU")} />
        <Stat label="Добавлено в корзину (факт)" value={summary.total_cart_adds.toLocaleString("ru-RU")} />
        <Stat label="Заказано, шт (факт)" value={summary.total_ordered_units.toLocaleString("ru-RU")} />
        <Stat label="Выкуплено, шт (факт)" value={summary.total_bought_out_units.toLocaleString("ru-RU")} />
        <Stat label="Отменено/возвращено (факт)" value={(summary.total_cancelled_units + summary.total_returned_units).toLocaleString("ru-RU")} />
        <Stat label="Заказано на сумму (факт)" value={fmtRub(summary.total_ordered_sum_rub)} />
        <Stat label="Остаток на конец периода (факт)" value={summary.latest_stock !== null ? summary.latest_stock.toLocaleString("ru-RU") : "Нет данных"} />
        <Stat label="Конверсия в корзину (рассчитано)" value={fmtPct(summary.cart_conversion_calculated_pct)} />
        <Stat label="Конверсия корзина→заказ (рассчитано)" value={fmtPct(summary.order_conversion_calculated_pct)} />
        <Stat label="Доля выкупа (рассчитано)" value={fmtPct(summary.buyout_rate_calculated_pct)} />
        <Stat label="Рейтинг (факт, последний день)" value={summary.latest_rating !== null ? summary.latest_rating.toString() : "Нет данных"} />
      </div>

      <p className="text-xs italic text-slate-500">
        Показатели «рассчитано» — это отношения сумм по загруженным дням (например, добавлено в корзину / показы),
        посчитанные этим приложением. Они не тождественны конверсиям, которые Ozon указывает построчно в самом
        отчёте, — точная методика их расчёта Ozon не публикует.
      </p>

      {rows && rows.items.length > 0 && (
        <div className="overflow-x-auto">
          <h3 className="mb-2 text-sm font-semibold text-slate-700">По дням</h3>
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-slate-200 text-xs text-slate-500">
                <th className="py-1">Дата</th>
                <th>Показы</th>
                <th>Переходы в карточку</th>
                <th>В корзину</th>
                <th>Заказано</th>
                <th>Выкуплено</th>
                <th>Цена</th>
                <th>Остаток</th>
                <th>Позиция (Ozon)</th>
                <th>ДРР (Ozon)</th>
                <th>Рейтинг</th>
              </tr>
            </thead>
            <tbody>
              {rows.items.map((r) => (
                <tr key={r.id} className="border-b border-slate-100">
                  <td className="py-1">{r.date}</td>
                  <td>{r.impressions_total ?? "—"}</td>
                  <td>{r.card_visits ?? "—"}</td>
                  <td>{r.cart_adds_total ?? "—"}</td>
                  <td>{r.ordered_units ?? "—"}</td>
                  <td>{r.bought_out_units ?? "—"}</td>
                  <td>{fmtRub(r.avg_price_rub)}</td>
                  <td>{r.stock_end_of_period ?? "—"}</td>
                  <td>{r.search_catalog_position_ozon ?? "—"}</td>
                  <td>{fmtPct(r.drr_pct_ozon !== null ? r.drr_pct_ozon * 100 : null)}</td>
                  <td>{r.rating ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
        </div>
      )}
    </div>
  );
}

function ProductSearchQueriesTab({ storeId, productId }: { storeId: string; productId: string }) {
  const [summary, setSummary] = useState<SearchQueryAnalytics | null>(null);

  useEffect(() => {
    api.get<SearchQueryAnalytics>(`/stores/${storeId}/search-queries/summary?product_id=${productId}`).then(setSummary);
  }, [storeId, productId]);

  if (!summary) return <div className="text-slate-500">Загрузка...</div>;
  if (!summary.has_data) {
    return (
      <div className="rounded-md border border-slate-200 bg-white p-6 text-center text-slate-500">
        Нет данных. Загрузите отчёт «Аналитика → Запросы» на странице{" "}
        <Link to="/products" className="text-indigo-600 underline">
          «Товары»
        </Link>
        .
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="text-xs text-slate-500">
        Период данных: {summary.period_start} — {summary.period_end} · Уникальных запросов: {summary.distinct_queries}
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Человек искало (факт)" value={summary.total_people_searched.toLocaleString("ru-RU")} />
        <Stat label="Человек увидело (факт)" value={summary.total_people_saw.toLocaleString("ru-RU")} />
        <Stat label="Заказано, шт (факт)" value={summary.total_ordered_units.toLocaleString("ru-RU")} />
        <Stat label="Заказано на сумму (факт)" value={fmtRub(summary.total_ordered_sum_rub)} />
        <Stat label="Средняя позиция (рассчитано)" value={summary.avg_position_calculated ?? "Нет данных"} />
        <Stat label="Конверсия в заказ (рассчитано)" value={fmtPct(summary.order_rate_calculated_pct)} />
      </div>

      <p className="text-xs italic text-slate-500">
        Показатель «Конверсия в заказ (рассчитано)» — это отношение заказанных штук к сумме «человек искало» по всем
        запросам, посчитанное этим приложением. Он НЕ равен построчной «Конверсии из поиска в заказ», которую Ozon
        указывает в самом отчёте для каждого запроса (эти значения различаются на порядки — методика Ozon явно
        учитывает что-то помимо количества поисков), поэтому в таблице ниже эти проценты показаны отдельно как
        значения Ozon.
      </p>

      <QueryTable title="Топ запросов по количеству искавших" items={summary.top_queries_by_searches} />
      <QueryTable title="Топ запросов по количеству заказов" items={summary.top_queries_by_orders} />
    </div>
  );
}

function QueryTable({ title, items }: { title: string; items: SearchQueryAnalytics["top_queries_by_searches"] }) {
  if (items.length === 0) return null;
  return (
    <div className="overflow-x-auto">
      <h3 className="mb-2 text-sm font-semibold text-slate-700">{title}</h3>
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="border-b border-slate-200 text-xs text-slate-500">
            <th className="py-1">Запрос</th>
            <th>Искало</th>
            <th>Увидело</th>
            <th>Позиция (Ozon)</th>
            <th>Заказано, шт</th>
            <th>Заказано на сумму</th>
          </tr>
        </thead>
        <tbody>
          {items.map((q) => (
            <tr key={q.query_text} className="border-b border-slate-100">
              <td className="py-1">{q.query_text}</td>
              <td>{q.people_searched ?? "—"}</td>
              <td>{q.people_saw ?? "—"}</td>
              <td>{q.position_ozon ?? "—"}</td>
              <td>{q.ordered_units_by_query ?? "—"}</td>
              <td>{fmtRub(q.ordered_sum_by_query_rub)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Section({ title, items }: { title: string; items: { label: string; count: number }[] }) {
  return (
    <div>
      <h3 className="mb-1 text-sm font-semibold text-slate-700">{title}</h3>
      {items.length === 0 ? (
        <div className="text-sm text-slate-400">Нет данных</div>
      ) : (
        <ul className="list-disc pl-5 text-sm text-slate-700">
          {items.map((i, idx) => (
            <li key={idx}>{i.label}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-md border border-slate-200 bg-white p-4">
      <div className="text-xs text-slate-500">{label}</div>
      <div className="mt-1 text-2xl font-semibold text-slate-800">{value}</div>
    </div>
  );
}
