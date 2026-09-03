import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { useStore } from "../store/StoreContext";
import type { AdvertisingCampaign, PerformanceCredentialsStatus } from "../types";

export function AdvertisingPage() {
  const { currentStore } = useStore();
  const [campaigns, setCampaigns] = useState<AdvertisingCampaign[] | null>(null);
  const [perfStatus, setPerfStatus] = useState<PerformanceCredentialsStatus | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [syncing, setSyncing] = useState(false);

  const load = () => {
    if (!currentStore) return;
    api.get<AdvertisingCampaign[]>(`/stores/${currentStore.id}/advertising/campaigns`).then(setCampaigns);
    api.get<PerformanceCredentialsStatus>(`/stores/${currentStore.id}/ozon/performance/credentials`).then(setPerfStatus);
  };

  useEffect(load, [currentStore]);

  if (!currentStore) return null;

  const sync = async () => {
    setSyncing(true);
    setNotice("Синхронизация кампаний с Ozon Performance API...");
    try {
      const run = await api.post<{ status: string; error_message: string | null; items_created: number; items_skipped_duplicate: number }>(
        `/stores/${currentStore.id}/sync/ozon-advertising`
      );
      if (run.status === "failed") {
        setNotice(`Синхронизация не удалась: ${run.error_message}`);
      } else {
        setNotice(`Готово: новых кампаний ${run.items_created}, обновлено ${run.items_skipped_duplicate}.`);
      }
      load();
    } catch (err) {
      setNotice(err instanceof ApiError ? err.message : "Ошибка синхронизации");
    } finally {
      setSyncing(false);
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-lg font-semibold text-slate-800">Реклама — {currentStore.name}</h1>
        <button
          onClick={sync}
          disabled={syncing || !perfStatus?.configured}
          className="rounded-md bg-slate-100 px-3 py-1.5 text-xs font-medium hover:bg-slate-200 disabled:opacity-50"
        >
          {syncing ? "Синхронизация..." : "Синхронизировать кампании"}
        </button>
      </div>

      {!perfStatus?.configured && (
        <div className="rounded-md border border-amber-300 bg-amber-50 p-4 text-sm text-amber-800">
          Ключи Ozon Performance API не заданы. Перейдите в{" "}
          <Link to="/ozon-settings" className="underline">
            «Подключение к Ozon»
          </Link>{" "}
          и укажите Client-Id/Client-Secret Performance API, затем проверьте подключение.
        </div>
      )}

      {notice && <div className="rounded-md bg-slate-50 p-2 text-xs text-slate-600">{notice}</div>}

      <div className="rounded-md border border-dashed border-slate-300 bg-white p-4 text-sm text-slate-500">
        Показаны только реальные данные, синхронизированные из Ozon Performance API: название, тип, статус, дневной
        бюджет и даты кампании. Статистика по дням (показы, клики, расход, заказы) пока не подключена — этот раздел
        появится отдельно, когда будет реализована выгрузка отчётов Ozon Performance API.
      </div>

      {campaigns === null ? (
        <div className="text-slate-500">Загрузка...</div>
      ) : campaigns.length === 0 ? (
        <div className="rounded-md border border-slate-200 bg-white p-6 text-center text-slate-500">
          Нет данных. Синхронизируйте кампании, чтобы увидеть список.
        </div>
      ) : (
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="border-b border-slate-200 text-xs text-slate-500">
              <th className="py-1">Название</th>
              <th>Тип</th>
              <th>Статус</th>
              <th>Дневной бюджет</th>
              <th>Период</th>
            </tr>
          </thead>
          <tbody>
            {campaigns.map((c) => (
              <tr key={c.id} className="border-b border-slate-100">
                <td className="py-1">{c.name ?? "Без названия"}</td>
                <td>{c.campaign_type ?? "—"}</td>
                <td>{c.state ?? "—"}</td>
                <td>{c.daily_budget_rub !== null ? `${c.daily_budget_rub.toLocaleString("ru-RU")} ₽` : "Нет данных"}</td>
                <td>
                  {c.date_from ?? "—"} — {c.date_to ?? "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
