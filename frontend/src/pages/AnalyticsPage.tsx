import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useStore } from "../store/StoreContext";
import type { NamedCount, ReviewAnalytics } from "../types";

function EvidenceList({ items, title }: { items: NamedCount[]; title: string }) {
  const [openIndex, setOpenIndex] = useState<number | null>(null);
  if (items.length === 0) return <div className="text-sm text-slate-400">Нет данных</div>;
  return (
    <div>
      <h3 className="mb-2 text-sm font-semibold text-slate-700">{title}</h3>
      <ul className="space-y-1">
        {items.map((item, idx) => (
          <li key={idx} className="text-sm">
            <div className="flex items-center justify-between">
              <span className="text-slate-800">{item.label}</span>
              <span className="text-xs text-slate-500">
                {item.count} ·{" "}
                <button className="text-indigo-600 underline" onClick={() => setOpenIndex(openIndex === idx ? null : idx)}>
                  Показать отзывы-основания
                </button>
              </span>
            </div>
            {openIndex === idx && (
              <div className="mt-1 rounded bg-slate-50 p-2 text-xs text-slate-500">
                ID отзывов: {item.review_ids.join(", ")}
              </div>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

export function AnalyticsPage() {
  const { currentStore } = useStore();
  const [data, setData] = useState<ReviewAnalytics | null>(null);

  useEffect(() => {
    if (!currentStore) return;
    api.get<ReviewAnalytics>(`/stores/${currentStore.id}/analytics/reviews`).then(setData);
  }, [currentStore]);

  if (!currentStore) return null;
  if (!data) return <div className="text-slate-500">Загрузка...</div>;

  if (!data.has_data) {
    return (
      <div className="rounded-md border border-slate-200 bg-white p-6 text-center text-slate-500">
        Нет данных для аналитики. Загрузите или синхронизируйте отзывы.
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <h1 className="text-lg font-semibold text-slate-800">Аналитика отзывов — {currentStore.name}</h1>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Всего отзывов (факт)" value={data.total_reviews} />
        <Stat label="Средняя оценка (рассчитано)" value={data.average_rating ?? "—"} />
        <Stat label="Доля оценок 1–3 (рассчитано)" value={data.low_rating_share !== null ? `${Math.round(data.low_rating_share * 100)}%` : "—"} />
        <Stat label="Без ответа (факт)" value={data.reviews_without_reply} />
      </div>

      <div className="rounded-md border border-slate-200 bg-white p-4">
        <h3 className="mb-2 text-sm font-semibold text-slate-700">Распределение оценок (факт)</h3>
        <div className="flex items-end gap-3">
          {data.rating_distribution.map((b) => (
            <div key={b.rating} className="flex flex-col items-center">
              <div className="w-8 bg-indigo-500" style={{ height: `${b.count * 8 + 4}px` }} />
              <span className="mt-1 text-xs text-slate-500">{b.rating}★ ({b.count})</span>
            </div>
          ))}
        </div>
      </div>

      {data.products_with_rising_negativity.length > 0 && (
        <div className="rounded-md border border-red-200 bg-red-50 p-4 text-sm text-red-700">
          <b>Гипотеза ИИ:</b> у следующих товаров средняя оценка за последние 30 дней ниже, чем за предыдущие 30 дней
          (требует проверки): {data.products_with_rising_negativity.join(", ")}
        </div>
      )}

      <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
        <EvidenceList title="Частые преимущества (по отзывам)" items={data.top_advantages} />
        <EvidenceList title="Частые жалобы (по отзывам)" items={data.top_complaints} />
      </div>

      <div className="grid grid-cols-1 gap-6 md:grid-cols-3">
        <EvidenceList title="Рекомендации по товару (гипотезы ИИ)" items={data.product_improvement_ideas} />
        <EvidenceList title="Рекомендации по карточке (гипотезы ИИ)" items={data.card_improvement_ideas} />
        <EvidenceList title="Идеи для инфографики (гипотезы ИИ)" items={data.infographic_ideas} />
      </div>
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
