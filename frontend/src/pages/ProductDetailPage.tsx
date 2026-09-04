import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import { useStore } from "../store/StoreContext";
import type { Product, Review, ReviewAnalytics, ReviewListResponse } from "../types";
import { ReviewCard } from "../components/ReviewCard";
import { ChangeHistoryPanel } from "../components/ChangeHistoryPanel";

type Tab = "overview" | "reviews" | "analytics" | "ads" | "sales" | "search" | "history" | "recommendations";

const TABS: { key: Tab; label: string; planned?: boolean }[] = [
  { key: "overview", label: "Обзор" },
  { key: "reviews", label: "Отзывы" },
  { key: "analytics", label: "Аналитика отзывов" },
  { key: "ads", label: "Реклама", planned: true },
  { key: "sales", label: "Продажи", planned: true },
  { key: "search", label: "Поисковые запросы", planned: true },
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
        {tab === "overview" && <OverviewTab product={product} />}
        {tab === "reviews" && <ProductReviewsTab storeId={currentStore.id} productId={productId} />}
        {tab === "analytics" && <ProductAnalyticsTab storeId={currentStore.id} productId={productId} />}
        {tab === "ads" && (
          <div className="rounded-md border border-dashed border-slate-300 bg-white p-6 text-center text-slate-500">
            Список кампаний магазина синхронизируется на отдельной странице{" "}
            <Link to="/advertising" className="text-indigo-600 underline">
              «Реклама»
            </Link>
            . Привязка кампаний к конкретному товару и статистика по дням (показы, клики, расход) пока не реализованы.
          </div>
        )}
        {tab === "sales" && <PlannedTab text="Модуль аналитики продаж запланирован." />}
        {tab === "search" && <PlannedTab text="Модуль поисковых запросов запланирован." />}
        {tab === "history" && <ChangeHistoryPanel storeId={currentStore.id} productId={productId} />}
        {tab === "recommendations" && <ProductAnalyticsTab storeId={currentStore.id} productId={productId} recommendationsOnly />}
      </div>
    </div>
  );
}

function OverviewTab({ product }: { product: Product | null }) {
  if (!product) return <div className="text-slate-500">Загрузка...</div>;
  return (
    <div className="rounded-md border border-slate-200 bg-white p-4 text-sm text-slate-700">
      <dl className="grid grid-cols-2 gap-2">
        <dt className="text-slate-500">Название</dt>
        <dd>{product.name}</dd>
        <dt className="text-slate-500">SKU</dt>
        <dd>{product.ozon_sku}</dd>
        <dt className="text-slate-500">Артикул продавца</dt>
        <dd>{product.offer_id ?? "Нет данных"}</dd>
      </dl>
    </div>
  );
}

function PlannedTab({ text }: { text: string }) {
  return <div className="rounded-md border border-dashed border-slate-300 bg-white p-6 text-center text-slate-500">{text}</div>;
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

  useEffect(() => {
    api.get<ReviewAnalytics>(`/stores/${storeId}/analytics/reviews?product_id=${productId}`).then(setData);
  }, [storeId, productId]);

  if (!data) return <div className="text-slate-500">Загрузка...</div>;
  if (!data.has_data) return <div className="text-slate-500">Нет данных</div>;

  if (recommendationsOnly) {
    return (
      <div className="space-y-4 rounded-md border border-slate-200 bg-white p-4">
        <p className="text-xs italic text-slate-500">
          Рекомендации сформированы ИИ на основе анализа отзывов и являются гипотезами, требующими проверки человеком —
          не воспринимайте их как доказанные факты.
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
