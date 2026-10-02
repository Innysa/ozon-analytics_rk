import { useEffect, useState } from "react";
import { api, ApiError } from "../api/client";
import { useResizableColumns } from "../hooks/useResizableColumns";
import { useStore } from "../store/StoreContext";
import type { ProductWarehouseStockSummary, SyncRun, WarehouseStockListResponse } from "../types";

function fmtNum(v: number | null): string {
  return v === null ? "—" : v.toLocaleString("ru-RU");
}

const COLUMNS = ["Товар", "FBO доступно", "Возвращается от покупателя", "FBO ожидается", "FBS", "В поставке (не отгружено)"];
const DEFAULT_WIDTHS = [320, 130, 150, 130, 90, 170];

export function WarehouseStocksPage() {
  const { currentStore } = useStore();
  const [data, setData] = useState<WarehouseStockListResponse | null>(null);
  const [search, setSearch] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [syncing, setSyncing] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const { widths, startResize } = useResizableColumns(DEFAULT_WIDTHS);

  const load = () => {
    if (!currentStore) return;
    api.get<WarehouseStockListResponse>(`/stores/${currentStore.id}/warehouse-stocks`).then(setData);
  };

  useEffect(load, [currentStore]);

  if (!currentStore) return null;

  const sync = async () => {
    setSyncing(true);
    setNotice("Запрос остатков по складам через Ozon API...");
    try {
      const run = await api.post<SyncRun>(`/stores/${currentStore.id}/sync/ozon-warehouse-stocks`);
      if (run.status === "failed") {
        setNotice(`Не удалось обновить остатки: ${run.error_message ?? "неизвестная ошибка"}`);
      } else {
        setNotice(
          `Обновлено: строк получено ${run.items_fetched}, сохранено ${run.items_created}.` +
            (run.error_message ? ` Предупреждения: ${run.error_message}` : "")
        );
      }
      load();
    } catch (err) {
      setNotice(err instanceof ApiError ? err.message : "Ошибка обновления остатков");
    } finally {
      setSyncing(false);
    }
  };

  const toggle = (sku: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(sku)) next.delete(sku);
      else next.add(sku);
      return next;
    });
  };

  const filtered: ProductWarehouseStockSummary[] =
    data?.items.filter((row) => {
      if (!search.trim()) return true;
      const q = search.trim().toLowerCase();
      return (
        row.ozon_sku.toLowerCase().includes(q) ||
        (row.offer_id ?? "").toLowerCase().includes(q) ||
        (row.name ?? "").toLowerCase().includes(q)
      );
    }) ?? [];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-lg font-semibold text-slate-800">Остатки — {currentStore.name}</h1>
        <div className="flex flex-wrap items-center gap-2">
          {data?.synced_at && (
            <span className="text-xs text-slate-500">
              Обновлено: {new Date(data.synced_at).toLocaleString("ru-RU")}
            </span>
          )}
          <button
            onClick={sync}
            disabled={syncing}
            className="rounded-md bg-indigo-100 px-3 py-1.5 text-xs font-medium text-indigo-700 hover:bg-indigo-200 disabled:opacity-50"
            title="Обновить остатки по складам через Ozon API (POST /v2/analytics/stock_on_warehouses)"
          >
            {syncing ? "Обновление..." : "Обновить остатки"}
          </button>
        </div>
      </div>

      <input
        type="text"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        placeholder="Поиск по SKU, артикулу или названию"
        className="w-full max-w-md rounded-md border border-slate-300 px-3 py-1.5 text-sm"
      />

      {notice && <div className="rounded-md bg-slate-50 p-2 text-xs text-slate-600">{notice}</div>}

      <div className="rounded-md border border-dashed border-slate-300 bg-white p-4 text-sm text-slate-500">
        «FBO доступно» — можно продать прямо сейчас. «Возвращается от покупателя» (поле Ozon называется
        reserved_amount) — рабочая гипотеза, сверена на двух реальных товарах с отчётом Ozon «Доступность товаров»:
        на одном совпало точно, на другом — близко, но не один в один (вероятно, из-за разницы во времени между
        последним «Обновить остатки» и моментом сверки). Оба показателя — из Ozon Seller API (POST /v2/analytics/
        stock_on_warehouses), по каждому складу отдельно — раскройте строку товара, чтобы увидеть разбивку. «FBO
        ожидается» — тоже рабочая гипотеза (поле promised_amount, официального описания не нашлось) — товар уже
        отгружен и едет на склад Ozon. «В поставке (не отгружено)» — отдельно подтверждённая цепочка методов Ozon
        (список заявок на поставку → состав каждой заявки) — сколько штук уже оформлено в заявку, но физически ещё
        не отгружено со склада продавца. «FBS» — из каталога (как и раньше на карточке товара). Данные Ozon не даёт
        историю остатков — это всегда текущий срез на момент последнего «Обновить остатки».
      </div>

      {!data ? (
        <div className="text-slate-500">Загрузка...</div>
      ) : data.items.length === 0 ? (
        <div className="rounded-md border border-slate-200 bg-white p-6 text-center text-slate-500">
          Остатков нет — нажмите «Обновить остатки», чтобы запросить данные у Ozon.
        </div>
      ) : filtered.length === 0 ? (
        <div className="rounded-md border border-slate-200 bg-white p-6 text-center text-slate-500">
          Ничего не найдено по запросу «{search}».
        </div>
      ) : (
        <div className="max-h-[70vh] overflow-auto rounded-md border border-slate-200 bg-white">
          <table className="text-sm" style={{ tableLayout: "fixed", width: widths.reduce((a, b) => a + b, 0) }}>
            <colgroup>
              {widths.map((w, i) => (
                <col key={i} style={{ width: w }} />
              ))}
            </colgroup>
            <thead className="sticky top-0 z-20 bg-slate-50 text-xs text-slate-500">
              <tr>
                {COLUMNS.map((label, i) => (
                  <th
                    key={label}
                    className={`relative select-none overflow-hidden text-ellipsis whitespace-nowrap bg-slate-50 px-3 py-2 ${i === 0 ? "text-left" : "text-right"}`}
                    title={
                      label === "Возвращается от покупателя"
                        ? "Рабочая гипотеза — сверено с отчётом Ozon «Доступность товаров», не идеально точно"
                        : label === "FBO ожидается"
                          ? "Рабочая гипотеза (Ozon не описывает это поле официально) — товар уже отгружен и едет на склад Ozon"
                          : label === "В поставке (не отгружено)"
                            ? "Суммарно по всем заявкам на поставку, которые ещё не отгружены (подтверждено: POST /v3/supply-order/list + /v3/supply-order/get + /v1/supply-order/bundle)"
                            : undefined
                    }
                  >
                    {label}
                    <span
                      onMouseDown={startResize(i)}
                      className="absolute right-0 top-0 h-full w-1.5 cursor-col-resize hover:bg-indigo-300"
                    />
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filtered.map((row) => (
                <>
                  <tr
                    key={row.ozon_sku}
                    className="cursor-pointer border-t border-slate-100 hover:bg-slate-50"
                    onClick={() => toggle(row.ozon_sku)}
                  >
                    <td className="overflow-hidden px-3 py-2">
                      <span className="mr-1 inline-block w-3 text-slate-400">{expanded.has(row.ozon_sku) ? "▾" : "▸"}</span>
                      <span className="overflow-hidden text-ellipsis whitespace-nowrap">{row.name ?? row.offer_id ?? row.ozon_sku}</span>
                      <div className="text-xs text-slate-400">SKU {row.ozon_sku}</div>
                    </td>
                    <td className="px-3 py-2 text-right">{fmtNum(row.fbo_free_to_sell_total)}</td>
                    <td className="px-3 py-2 text-right">{fmtNum(row.fbo_reserved_total)}</td>
                    <td className="px-3 py-2 text-right">{fmtNum(row.fbo_promised_total)}</td>
                    <td className="px-3 py-2 text-right">{fmtNum(row.fbs_stock)}</td>
                    <td className="px-3 py-2 text-right">{fmtNum(row.pending_supply_units)}</td>
                  </tr>
                  {expanded.has(row.ozon_sku) &&
                    row.warehouses.map((w) => (
                      <tr key={`${row.ozon_sku}-${w.warehouse_name}`} className="border-t border-slate-50 bg-slate-50/50 text-xs text-slate-600">
                        <td className="overflow-hidden text-ellipsis whitespace-nowrap px-3 py-1.5 pl-9">
                          {w.warehouse_name}
                          {w.cluster_name && <span className="text-slate-400"> · {w.cluster_name}</span>}
                        </td>
                        <td className="px-3 py-1.5 text-right">{fmtNum(w.free_to_sell_amount)}</td>
                        <td className="px-3 py-1.5 text-right">{fmtNum(w.reserved_amount)}</td>
                        <td className="px-3 py-1.5 text-right">{fmtNum(w.promised_amount)}</td>
                        <td className="px-3 py-1.5 text-right"></td>
                        <td className="px-3 py-1.5 text-right"></td>
                      </tr>
                    ))}
                </>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
