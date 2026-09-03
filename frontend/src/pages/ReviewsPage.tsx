import { ChangeEvent, useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../api/client";
import { useStore } from "../store/StoreContext";
import type { ImportSummary, Review, ReviewListResponse } from "../types";
import { ReviewCard } from "../components/ReviewCard";

interface Filters {
  rating: string;
  sentiment: string;
  statuses: string;
  has_reply: string;
}

const STATUS_GROUPS: { value: string; label: string }[] = [
  { value: "", label: "Все статусы" },
  { value: "new", label: "Новые" },
  { value: "draft_created", label: "Черновик создан" },
  { value: "approved", label: "Одобрен" },
  { value: "published", label: "Опубликован" },
  { value: "publish_failed", label: "Ошибка публикации" },
  { value: "no_reply_needed", label: "Ответ не требуется" },
];

export function ReviewsPage() {
  const { currentStore, refreshStores } = useStore();
  const [reviews, setReviews] = useState<Review[]>([]);
  const [loading, setLoading] = useState(false);
  const [filters, setFilters] = useState<Filters>({ rating: "", sentiment: "", statuses: "", has_reply: "" });
  const [notice, setNotice] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const load = useCallback(async () => {
    if (!currentStore) return;
    setLoading(true);
    const params = new URLSearchParams();
    if (filters.rating) params.set("rating", filters.rating);
    if (filters.sentiment) params.set("sentiment", filters.sentiment);
    if (filters.statuses) params.set("statuses", filters.statuses);
    if (filters.has_reply) params.set("has_reply", filters.has_reply);
    try {
      const data = await api.get<ReviewListResponse>(`/stores/${currentStore.id}/reviews?${params.toString()}`);
      setReviews(data.items);
    } finally {
      setLoading(false);
    }
  }, [currentStore, filters]);

  useEffect(() => {
    load();
  }, [load]);

  if (!currentStore) return null;

  const onUpload = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setNotice("Загрузка файла...");
    try {
      const result = await api.upload<ImportSummary>(`/stores/${currentStore.id}/reviews/upload`, file);
      setNotice(
        `Загружено: получено ${result.fetched}, создано ${result.created}, дублей пропущено ${result.skipped_duplicate}` +
          (result.errors.length ? `. Ошибки: ${result.errors.slice(0, 3).join("; ")}` : "")
      );
      load();
      refreshStores();
    } catch (err) {
      setNotice(err instanceof ApiError ? err.message : "Ошибка загрузки файла");
    }
  };

  const syncFromOzon = async () => {
    setNotice("Синхронизация с Ozon...");
    try {
      const run = await api.post<{ status: string; error_message: string | null; reviews_created: number }>(
        `/stores/${currentStore.id}/sync/ozon-reviews`
      );
      if (run.status === "failed") {
        setNotice(`Синхронизация не удалась: ${run.error_message}`);
      } else {
        setNotice(`Синхронизация завершена: создано ${run.reviews_created} отзывов.`);
      }
      load();
      refreshStores();
    } catch (err) {
      setNotice(err instanceof ApiError ? err.message : "Ошибка синхронизации");
    }
  };

  const bulkGenerate = async () => {
    if (selected.size === 0) return;
    setNotice("Массовая генерация черновиков...");
    try {
      const result = await api.post<{ succeeded: number; failed: number }>(
        `/stores/${currentStore.id}/reviews/bulk/generate-drafts`,
        Array.from(selected)
      );
      setNotice(`Черновики созданы: успешно ${result.succeeded}, ошибок ${result.failed}`);
      setSelected(new Set());
      load();
    } catch (err) {
      setNotice(err instanceof ApiError ? err.message : "Ошибка массовой генерации");
    }
  };

  const toggleSelected = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-lg font-semibold text-slate-800">Отзывы — {currentStore.name}</h1>
        <div className="flex flex-wrap gap-2">
          <label className="cursor-pointer rounded-md bg-slate-100 px-3 py-1.5 text-xs font-medium hover:bg-slate-200">
            Загрузить CSV/XLSX
            <input type="file" accept=".csv,.xlsx" className="hidden" onChange={onUpload} />
          </label>
          <button onClick={syncFromOzon} className="rounded-md bg-slate-100 px-3 py-1.5 text-xs font-medium hover:bg-slate-200">
            Синхронизировать с Ozon
          </button>
          <button
            onClick={bulkGenerate}
            disabled={selected.size === 0}
            className="rounded-md bg-indigo-50 px-3 py-1.5 text-xs font-medium text-indigo-700 hover:bg-indigo-100 disabled:opacity-50"
          >
            Сгенерировать черновики ({selected.size})
          </button>
        </div>
      </div>

      {notice && <div className="rounded-md bg-slate-50 p-2 text-xs text-slate-600">{notice}</div>}

      <div className="flex flex-wrap gap-2 text-sm">
        <select
          value={filters.rating}
          onChange={(e) => setFilters((f) => ({ ...f, rating: e.target.value }))}
          className="rounded-md border border-slate-300 px-2 py-1"
        >
          <option value="">Все оценки</option>
          {[1, 2, 3, 4, 5].map((r) => (
            <option key={r} value={r}>
              {r} звёзд
            </option>
          ))}
        </select>
        <select
          value={filters.sentiment}
          onChange={(e) => setFilters((f) => ({ ...f, sentiment: e.target.value }))}
          className="rounded-md border border-slate-300 px-2 py-1"
        >
          <option value="">Любая тональность</option>
          <option value="positive">Положительные</option>
          <option value="neutral">Нейтральные</option>
          <option value="negative">Негативные</option>
        </select>
        <select
          value={filters.statuses}
          onChange={(e) => setFilters((f) => ({ ...f, statuses: e.target.value }))}
          className="rounded-md border border-slate-300 px-2 py-1"
        >
          {STATUS_GROUPS.map((g) => (
            <option key={g.value} value={g.value}>
              {g.label}
            </option>
          ))}
        </select>
        <select
          value={filters.has_reply}
          onChange={(e) => setFilters((f) => ({ ...f, has_reply: e.target.value }))}
          className="rounded-md border border-slate-300 px-2 py-1"
        >
          <option value="">С ответом и без</option>
          <option value="false">Без ответа</option>
          <option value="true">С ответом</option>
        </select>
      </div>

      {loading ? (
        <div className="text-slate-500">Загрузка...</div>
      ) : reviews.length === 0 ? (
        <div className="rounded-md border border-slate-200 bg-white p-6 text-center text-slate-500">
          Нет данных. Загрузите отзывы из файла или выполните синхронизацию с Ozon.
        </div>
      ) : (
        <div className="space-y-3">
          {reviews.map((r) => (
            <div key={r.id} className="flex items-start gap-2">
              <input
                type="checkbox"
                className="mt-6"
                checked={selected.has(r.id)}
                onChange={() => toggleSelected(r.id)}
              />
              <div className="flex-1">
                <ReviewCard review={r} storeId={currentStore.id} onChanged={load} />
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
