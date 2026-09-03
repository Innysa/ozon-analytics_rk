import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { useStore } from "../store/StoreContext";
import type { Product } from "../types";

export function ProductsPage() {
  const { currentStore } = useStore();
  const [products, setProducts] = useState<Product[]>([]);

  useEffect(() => {
    if (!currentStore) return;
    api.get<Product[]>(`/stores/${currentStore.id}/products`).then(setProducts);
  }, [currentStore]);

  if (!currentStore) return null;

  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold text-slate-800">Товары — {currentStore.name}</h1>
      {products.length === 0 ? (
        <div className="rounded-md border border-slate-200 bg-white p-6 text-center text-slate-500">
          Нет данных. Товары появятся после загрузки или синхронизации отзывов.
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
