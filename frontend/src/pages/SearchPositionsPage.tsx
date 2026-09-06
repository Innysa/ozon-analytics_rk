import { ChangeEvent, useEffect, useMemo, useState } from "react";
import { api, ApiError } from "../api/client";
import { useResizableColumns } from "../hooks/useResizableColumns";
import { useStore } from "../store/StoreContext";
import type { ImportSummary, QueryCompetitorsReport, SearchQueryPosition, SyncRun } from "../types";

const COLUMNS = ["Товар", "Запрос", "Текущая позиция", "Позиция в прошлый раз", "Изменение", "Показы", "Заказы"];
const DEFAULT_WIDTHS = [220, 240, 130, 150, 150, 90, 90];

function fmtPosition(v: number | null): string {
  return v === null ? "—" : v.toLocaleString("ru-RU", { maximumFractionDigits: 0 });
}

export function SearchPositionsPage() {
  const { currentStore } = useStore();
  const [tab, setTab] = useState<"positions" | "competitors">("positions");
  const [items, setItems] = useState<SearchQueryPosition[] | null>(null);
  const [productFilter, setProductFilter] = useState("");
  const [queryFilter, setQueryFilter] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const [syncing, setSyncing] = useState(false);
  const { widths, startResize } = useResizableColumns(DEFAULT_WIDTHS);

  const load = () => {
    if (!currentStore) return;
    api
      .get<{ items: SearchQueryPosition[]; total: number }>(`/stores/${currentStore.id}/search-queries/positions`)
      .then((d) => setItems(d.items));
  };

  useEffect(load, [currentStore]);

  if (!currentStore) return null;

  const uploadReport = async (e: ChangeEvent<HTMLInputElement>) => {
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

  const pollSyncRun = async (runId: string, attempt = 0): Promise<SyncRun | null> => {
    if (!currentStore) return null;
    const runs = await api.get<SyncRun[]>(`/stores/${currentStore.id}/sync/runs`);
    const run = runs.find((r) => r.id === runId) ?? null;
    if (!run || run.status !== "running" || attempt >= 60) return run;
    await new Promise((resolve) => setTimeout(resolve, 5000));
    return pollSyncRun(runId, attempt + 1);
  };

  const syncViaApi = async () => {
    if (!currentStore) return;
    setSyncing(true);
    setNotice("Запуск автосбора позиций через Ozon Seller API...");
    try {
      const run = await api.post<SyncRun>(`/stores/${currentStore.id}/sync/ozon-search-query-statistics`);
      setNotice("Сбор данных выполняется в фоне...");
      const finished = await pollSyncRun(run.id);
      if (!finished) {
        setNotice("Не удалось получить статус синхронизации — обновите страницу и проверьте журнал синхронизаций.");
      } else if (finished.status === "failed") {
        setNotice(`Автосбор не удался: ${finished.error_message ?? "неизвестная ошибка"}`);
      } else if (finished.status === "running") {
        setNotice("Синхронизация всё ещё выполняется — проверьте журнал синхронизаций на странице «Подключение к Ozon» позже.");
      } else {
        setNotice(
          `Готово: получено ${finished.items_fetched}, создано ${finished.items_created}, обновлено ${finished.items_skipped_duplicate}.` +
            (finished.error_message ? ` Предупреждения: ${finished.error_message}` : "")
        );
      }
      load();
    } catch (err) {
      setNotice(err instanceof ApiError ? err.message : "Ошибка автосбора данных");
    } finally {
      setSyncing(false);
    }
  };

  const tabButtonClass = (active: boolean) =>
    `rounded-md px-2.5 py-1 text-xs font-medium ${active ? "bg-indigo-100 text-indigo-700" : "bg-slate-100 text-slate-600 hover:bg-slate-200"}`;

  const filtered = useMemo(() => {
    if (!items) return [];
    const productNeedle = productFilter.trim().toLowerCase();
    const queryNeedle = queryFilter.trim().toLowerCase();
    return items.filter((i) => {
      const matchesProduct =
        !productNeedle ||
        (i.product_name ?? "").toLowerCase().includes(productNeedle) ||
        i.ozon_sku.toLowerCase().includes(productNeedle);
      const matchesQuery = !queryNeedle || i.query_text.toLowerCase().includes(queryNeedle);
      return matchesProduct && matchesQuery;
    });
  }, [items, productFilter, queryFilter]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-lg font-semibold text-slate-800">Позиции в поиске — {currentStore.name}</h1>
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex gap-1">
            <button className={tabButtonClass(tab === "positions")} onClick={() => setTab("positions")}>
              Позиции по товарам
            </button>
            <button className={tabButtonClass(tab === "competitors")} onClick={() => setTab("competitors")}>
              Конкуренты по запросу
            </button>
          </div>
          {tab === "positions" && (
            <>
              <button
                onClick={syncViaApi}
                disabled={syncing}
                className="rounded-md bg-indigo-100 px-3 py-1.5 text-xs font-medium text-indigo-700 hover:bg-indigo-200 disabled:opacity-50"
                title="Автоматически собрать данные через Ozon Seller API (POST /v1/analytics/product-queries/details) — без ручной загрузки XLSX"
              >
                {syncing ? "Сбор данных..." : "Обновить данные (авто)"}
              </button>
              <label className="cursor-pointer rounded-md bg-slate-100 px-3 py-1.5 text-xs font-medium hover:bg-slate-200">
                Загрузить отчёт (XLSX)
                <input type="file" accept=".csv,.xlsx" className="hidden" onChange={uploadReport} />
              </label>
            </>
          )}
        </div>
      </div>

      {tab === "competitors" ? (
        <CompetitorsTab storeId={currentStore.id} />
      ) : (
        <>
      <div className="rounded-md border border-dashed border-slate-300 bg-white p-4 text-sm text-slate-500">
        Данные — из официального экспорта Ozon «Аналитика → Товары в поиске → Запросы моего товара» (тот же файл, что
        и на странице карточки товара). Каждая загрузка добавляет новый срез по дате конца периода отчёта, не
        перезаписывая предыдущие — «Изменение» сравнивает последний загруженный срез с предыдущим для этой же пары
        товар+запрос.
      </div>

      {notice && <div className="rounded-md bg-slate-50 p-2 text-xs text-slate-600">{notice}</div>}

      <div className="flex flex-wrap gap-2">
        <input
          value={productFilter}
          onChange={(e) => setProductFilter(e.target.value)}
          placeholder="Поиск по товару (название или SKU)"
          className="w-64 rounded-md border border-slate-300 px-2 py-1 text-sm"
        />
        <input
          value={queryFilter}
          onChange={(e) => setQueryFilter(e.target.value)}
          placeholder="Поиск по запросу"
          className="w-64 rounded-md border border-slate-300 px-2 py-1 text-sm"
        />
      </div>

      {items === null ? (
        <div className="text-slate-500">Загрузка...</div>
      ) : items.length === 0 ? (
        <div className="rounded-md border border-slate-200 bg-white p-6 text-center text-slate-500">
          Нет данных. Загрузите отчёт «Запросы моего товара» из личного кабинета Ozon (XLSX).
        </div>
      ) : filtered.length === 0 ? (
        <div className="text-slate-500">Ничего не найдено по фильтру.</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="text-left text-sm" style={{ tableLayout: "fixed", width: widths.reduce((a, b) => a + b, 0) }}>
            <colgroup>
              {widths.map((w, i) => (
                <col key={i} style={{ width: w }} />
              ))}
            </colgroup>
            <thead>
              <tr className="border-b border-slate-200 text-xs text-slate-500">
                {COLUMNS.map((label, i) => (
                  <th key={label} className="relative select-none overflow-hidden text-ellipsis whitespace-nowrap py-1 pr-2">
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
              {filtered.map((i) => (
                <tr key={`${i.ozon_sku}::${i.query_text}`} className="border-b border-slate-100">
                  <td
                    className="overflow-hidden text-ellipsis whitespace-nowrap py-1 pr-2"
                    title={i.product_name ?? i.ozon_sku}
                  >
                    {i.product_name ?? i.ozon_sku}
                  </td>
                  <td className="overflow-hidden text-ellipsis whitespace-nowrap pr-2" title={i.query_text}>
                    {i.query_text}
                  </td>
                  <td className="overflow-hidden text-ellipsis whitespace-nowrap pr-2" title={`на ${i.report_date}`}>
                    {fmtPosition(i.position)}
                  </td>
                  <td
                    className="overflow-hidden text-ellipsis whitespace-nowrap pr-2"
                    title={i.previous_report_date ? `на ${i.previous_report_date}` : undefined}
                  >
                    {fmtPosition(i.previous_position)}
                  </td>
                  <td className="overflow-hidden text-ellipsis whitespace-nowrap pr-2">
                    <PositionChange item={i} />
                  </td>
                  <td className="overflow-hidden text-ellipsis whitespace-nowrap pr-2">
                    {i.people_saw !== null ? i.people_saw.toLocaleString("ru-RU") : "—"}
                  </td>
                  <td className="overflow-hidden text-ellipsis whitespace-nowrap pr-2">
                    {i.ordered_units_by_query !== null ? i.ordered_units_by_query.toLocaleString("ru-RU") : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
        </>
      )}
    </div>
  );
}

function PositionChange({ item }: { item: SearchQueryPosition }) {
  if (item.previous_position === null) {
    return <span className="text-xs italic text-slate-400">нет данных для сравнения</span>;
  }
  if (item.position_change_direction === null) {
    return <span className="text-slate-500">без изменений</span>;
  }
  const isUp = item.position_change_direction === "up";
  const color = isUp ? "text-green-600" : "text-red-600";
  const arrow = isUp ? "▲" : "▼";
  const magnitude = item.position_change !== null ? Math.abs(item.position_change) : 0;
  return (
    <span className={`font-medium ${color}`}>
      {arrow} {magnitude.toLocaleString("ru-RU", { maximumFractionDigits: 0 })}
    </span>
  );
}

const COMPETITOR_COLUMNS = [
  "Позиция", "Товар", "Продавец", "Статус", "Ставка за клик", "Соответствие", "Отзывы", "Цена", "Индекс цен",
];
const COMPETITOR_DEFAULT_WIDTHS = [80, 260, 180, 130, 110, 100, 130, 100, 90];

function CompetitorsTab({ storeId }: { storeId: string }) {
  const [report, setReport] = useState<QueryCompetitorsReport | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const { widths, startResize } = useResizableColumns(COMPETITOR_DEFAULT_WIDTHS);

  const uploadReport = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setNotice("Загрузка отчёта...");
    setReport(null);
    try {
      const result = await api.upload<QueryCompetitorsReport>(
        `/stores/${storeId}/search-queries/competitors/preview`,
        file
      );
      setReport(result);
      setNotice(null);
    } catch (err) {
      setNotice(err instanceof ApiError ? err.message : "Ошибка загрузки файла");
    }
  };

  return (
    <div className="space-y-4">
      <div className="rounded-md border border-dashed border-slate-300 bg-white p-4 text-sm text-slate-500">
        Простой просмотр официального экспорта Ozon «Аналитика → Товары в поиске → Результаты по запросу» — полная
        выдача по одному запросу с конкурентами (позиция, продавец, цена, отзывы, ставки). Ничего не сохраняется в
        базе — это разовый просмотр загруженного файла, отдельный от истории позиций на вкладке «Позиции по товарам».
      </div>

      <label className="inline-block cursor-pointer rounded-md bg-slate-100 px-3 py-1.5 text-xs font-medium hover:bg-slate-200">
        Загрузить отчёт (XLSX)
        <input type="file" accept=".csv,.xlsx" className="hidden" onChange={uploadReport} />
      </label>

      {notice && <div className="rounded-md bg-slate-50 p-2 text-xs text-slate-600">{notice}</div>}

      {report && (
        <>
          <div className="text-sm text-slate-600">
            Запрос: <span className="font-medium text-slate-800">«{report.query_text}»</span>
            {report.region ? ` · Регион: ${report.region}` : ""}
            {report.generated_at ? ` · Данные на: ${report.generated_at}` : ""}
            {report.positions_in_results !== null ? ` · Всего позиций в выдаче: ${report.positions_in_results}` : ""}
          </div>

          <div className="overflow-x-auto">
            <table className="text-left text-sm" style={{ tableLayout: "fixed", width: widths.reduce((a, b) => a + b, 0) }}>
              <colgroup>
                {widths.map((w, i) => (
                  <col key={i} style={{ width: w }} />
                ))}
              </colgroup>
              <thead>
                <tr className="border-b border-slate-200 text-xs text-slate-500">
                  {COMPETITOR_COLUMNS.map((label, i) => (
                    <th key={label} className="relative select-none overflow-hidden text-ellipsis whitespace-nowrap py-1 pr-2">
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
                {report.rows.map((r) => (
                  <tr key={r.position} className="border-b border-slate-100">
                    <td className="py-1 pr-2">{r.position}</td>
                    <td className="overflow-hidden text-ellipsis whitespace-nowrap pr-2" title={r.product_name ?? undefined}>
                      {r.product_name ?? "—"}
                    </td>
                    <td className="overflow-hidden text-ellipsis whitespace-nowrap pr-2" title={r.seller_name ?? undefined}>
                      {r.seller_name ?? "—"}
                    </td>
                    <td className="overflow-hidden text-ellipsis whitespace-nowrap pr-2">{r.status ?? "—"}</td>
                    <td className="overflow-hidden text-ellipsis whitespace-nowrap pr-2">
                      {r.cpc_bid_rub !== null ? `${r.cpc_bid_rub.toLocaleString("ru-RU")} ₽` : "—"}
                    </td>
                    <td className="overflow-hidden text-ellipsis whitespace-nowrap pr-2">
                      {r.relevance_pct !== null ? `${r.relevance_pct}%` : "—"}
                    </td>
                    <td className="overflow-hidden text-ellipsis whitespace-nowrap pr-2">{r.reviews_text ?? "—"}</td>
                    <td className="overflow-hidden text-ellipsis whitespace-nowrap pr-2">
                      {r.price_rub !== null ? `${r.price_rub.toLocaleString("ru-RU")} ₽` : "—"}
                    </td>
                    <td className="overflow-hidden text-ellipsis whitespace-nowrap pr-2">
                      {r.price_index_pct !== null ? `${r.price_index_pct}%` : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {!report && !notice && (
        <div className="rounded-md border border-slate-200 bg-white p-6 text-center text-slate-500">
          Загрузите отчёт «Результаты по запросу», чтобы увидеть выдачу.
        </div>
      )}
    </div>
  );
}
