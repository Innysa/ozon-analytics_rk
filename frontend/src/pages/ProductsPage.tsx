import { ChangeEvent, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { useStore } from "../store/StoreContext";
import type { ImportSummary, Product } from "../types";

export function ProductsPage() {
  const { currentStore } = useStore();
  const [products, setProducts] = useState<Product[]>([]);
  const [notice, setNotice] = useState<string | null>(null);

  const load = () => {
    if (!currentStore) return;
    api.get<Product[]>(`/stores/${currentStore.id}/products`).then(setProducts);
  };

  useEffect(load, [currentStore]);

  if (!currentStore) return null;

  const uploadAnalytics = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setNotice("Загрузка отчёта...");
    try {
      const result = await api.upload<ImportSummary>(`/stores/${currentStore.id}/product-analytics/upload`, file);
      setNotice(
        `Загружено: получено ${result.fetched}, создано ${result.created}, дублей пропущено ${result.skipped_duplicate}` +
          (result.errors.length ? `. Примечания: ${result.errors.slice(0, 3).join("; ")}` : "")
      );
      load();
    } catch (err) {
      setNotice(err instanceof ApiError ? err.message : "Ошибка загрузки файла");
    }
  };

  const uploadSearchQueries = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setNotice("Загрузка отчёта...");
    try {
      const result = await api.upload<ImportSummary>(`/stores/${currentStore.id}/search-queries/upload`, file);
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
        <h1 className="text-lg font-semibold text-slate-800">Товары — {currentStore.name}</h1>
        <div className="flex flex-wrap gap-2">
          <label className="cursor-pointer rounded-md bg-slate-100 px-3 py-1.5 text-xs font-medium hover:bg-slate-200">
            Загрузить аналитику карточек (CSV/XLSX)
            <input type="file" accept=".csv,.xlsx" className="hidden" onChange={uploadAnalytics} />
          </label>
          <label className="cursor-pointer rounded-md bg-slate-100 px-3 py-1.5 text-xs font-medium hover:bg-slate-200">
            Загрузить поисковые запросы (CSV/XLSX)
            <input type="file" accept=".csv,.xlsx" className="hidden" onChange={uploadSearchQueries} />
          </label>
        </div>
      </div>

      <p className="text-xs text-slate-500">
        Загрузите отчёт «Аналитика → Товары» из личного кабинета Ozon — воронка продаж, конверсии, остатки, ДРР по дням
        появятся на вкладке «Продажи» карточки товара. Отчёт «Аналитика → Запросы» — на вкладке «Поисковые запросы».
      </p>

      {notice && <div className="rounded-md bg-slate-50 p-2 text-xs text-slate-600">{notice}</div>}

      {products.length === 0 ? (
        <div className="rounded-md border border-slate-200 bg-white p-6 text-center text-slate-500">
          Нет данных. Товары появятся после загрузки или синхронизации отзывов либо загрузки аналитики.
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {products.map((p) => (
            <Link
              key={p.id}
              to={`/products/${p.id}`}
              className="flex items-center gap-3 rounded-md border border-slate-200 bg-white p-3 hover:border-indigo-300"
            >
              {p.image_url ? (
                <img src={p.image_url} alt="" className="h-12 w-12 rounded object-cover" />
              ) : (
                <div className="flex h-12 w-12 items-center justify-center rounded bg-slate-100 text-xs text-slate-400">нет фото</div>
              )}
              <div>
                <div className="text-sm font-medium text-slate-800">{p.name}</div>
                <div className="text-xs text-slate-500">SKU {p.ozon_sku}</div>
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
