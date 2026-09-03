import { FormEvent, useEffect, useState } from "react";
import { api, ApiError } from "../api/client";
import { useStore } from "../store/StoreContext";
import type { OzonCredentialsStatus, PerformanceCredentialsStatus, SyncRun } from "../types";

const SOURCE_LABELS: Record<string, string> = {
  ozon_api: "Ozon Seller API (отзывы)",
  ozon_advertising_api: "Ozon Performance API (реклама)",
  csv_import: "CSV",
  xlsx_import: "XLSX",
};

export function OzonSettingsPage() {
  const { currentStore } = useStore();
  const [status, setStatus] = useState<OzonCredentialsStatus | null>(null);
  const [clientId, setClientId] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [runs, setRuns] = useState<SyncRun[]>([]);

  const [perfStatus, setPerfStatus] = useState<PerformanceCredentialsStatus | null>(null);
  const [perfClientId, setPerfClientId] = useState("");
  const [perfClientSecret, setPerfClientSecret] = useState("");
  const [perfMessage, setPerfMessage] = useState<string | null>(null);

  const load = () => {
    if (!currentStore) return;
    api.get<OzonCredentialsStatus>(`/stores/${currentStore.id}/ozon/credentials`).then(setStatus);
    api.get<PerformanceCredentialsStatus>(`/stores/${currentStore.id}/ozon/performance/credentials`).then(setPerfStatus);
    api.get<SyncRun[]>(`/stores/${currentStore.id}/sync/runs`).then(setRuns);
  };

  useEffect(load, [currentStore]);

  if (!currentStore) return null;

  const saveCredentials = async (e: FormEvent) => {
    e.preventDefault();
    try {
      await api.put(`/stores/${currentStore.id}/ozon/credentials`, { client_id: clientId, api_key: apiKey });
      setClientId("");
      setApiKey("");
      setMessage("Ключи сохранены");
      load();
    } catch (err) {
      setMessage(err instanceof ApiError ? err.message : "Ошибка сохранения");
    }
  };

  const checkConnection = async () => {
    setMessage("Проверка подключения...");
    try {
      const result = await api.post<OzonCredentialsStatus>(`/stores/${currentStore.id}/ozon/check-connection`);
      setStatus(result);
      setMessage(result.last_connection_message ?? null);
    } catch (err) {
      setMessage(err instanceof ApiError ? err.message : "Ошибка проверки подключения");
    }
  };

  const savePerfCredentials = async (e: FormEvent) => {
    e.preventDefault();
    try {
      await api.put(`/stores/${currentStore.id}/ozon/performance/credentials`, {
        client_id: perfClientId,
        client_secret: perfClientSecret,
      });
      setPerfClientId("");
      setPerfClientSecret("");
      setPerfMessage("Ключи сохранены");
      load();
    } catch (err) {
      setPerfMessage(err instanceof ApiError ? err.message : "Ошибка сохранения");
    }
  };

  const checkPerfConnection = async () => {
    setPerfMessage("Проверка подключения...");
    try {
      const result = await api.post<PerformanceCredentialsStatus>(`/stores/${currentStore.id}/ozon/performance/check-connection`);
      setPerfStatus(result);
      setPerfMessage(result.last_connection_message ?? null);
    } catch (err) {
      setPerfMessage(err instanceof ApiError ? err.message : "Ошибка проверки подключения");
    }
  };

  return (
    <div className="max-w-2xl space-y-6">
      <h1 className="text-lg font-semibold text-slate-800">Подключение к Ozon — {currentStore.name}</h1>

      <section className="space-y-3">
        <h2 className="text-sm font-semibold text-slate-800">Seller API (отзывы, товары)</h2>
        <div className="rounded-md border border-slate-200 bg-white p-4 text-sm">
          <div className="mb-2 text-slate-600">
            Текущие ключи: {status?.configured ? (
              <>Client-Id: <code>{status.client_id_masked}</code>, Api-Key: <code>{status.api_key_masked}</code></>
            ) : (
              "не заданы"
            )}
          </div>
          {status?.last_connection_check_at && (
            <div className="text-xs text-slate-500">
              Последняя проверка: {new Date(status.last_connection_check_at).toLocaleString("ru-RU")} —{" "}
              {status.last_connection_ok ? "успешно" : "неуспешно"}. {status.last_connection_message}
            </div>
          )}
          {status?.reviews_api_available === false && (
            <div className="mt-2 rounded bg-amber-50 p-2 text-xs text-amber-700">
              Метод получения отзывов недоступен для этого магазина (вероятно, требуется тариф Ozon Premium Plus).
              Используйте загрузку отзывов из CSV/XLSX на странице «Отзывы»; публикация ответов через API отключена —
              используйте кнопку «Скопировать».
            </div>
          )}
          <button onClick={checkConnection} className="mt-3 rounded-md bg-slate-100 px-3 py-1.5 text-xs font-medium hover:bg-slate-200">
            Проверить подключение к Ozon
          </button>
        </div>

        <form onSubmit={saveCredentials} className="space-y-3 rounded-md border border-slate-200 bg-white p-4">
          <h3 className="text-sm font-semibold text-slate-700">Указать новые ключи Seller API</h3>
          <div>
            <label className="mb-1 block text-sm text-slate-600">Client-Id</label>
            <input value={clientId} onChange={(e) => setClientId(e.target.value)} required className="w-full rounded-md border border-slate-300 px-2 py-1 text-sm" />
          </div>
          <div>
            <label className="mb-1 block text-sm text-slate-600">Api-Key</label>
            <input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              required
              className="w-full rounded-md border border-slate-300 px-2 py-1 text-sm"
            />
          </div>
          <p className="text-xs text-slate-400">
            Ключи хранятся в базе данных в зашифрованном виде и никогда не отображаются полностью.
          </p>
          <button type="submit" className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700">
            Сохранить ключи
          </button>
        </form>

        {message && <div className="rounded-md bg-slate-50 p-2 text-xs text-slate-600">{message}</div>}
      </section>

      <section className="space-y-3">
        <h2 className="text-sm font-semibold text-slate-800">Performance API (реклама)</h2>
        <div className="rounded-md border border-slate-200 bg-white p-4 text-sm">
          <div className="mb-2 text-slate-600">
            Текущие ключи: {perfStatus?.configured ? (
              <>Client-Id: <code>{perfStatus.client_id_masked}</code>, Client-Secret: <code>{perfStatus.client_secret_masked}</code></>
            ) : (
              "не заданы"
            )}
          </div>
          {perfStatus?.last_connection_check_at && (
            <div className="text-xs text-slate-500">
              Последняя проверка: {new Date(perfStatus.last_connection_check_at).toLocaleString("ru-RU")} —{" "}
              {perfStatus.last_connection_ok ? "успешно" : "неуспешно"}. {perfStatus.last_connection_message}
            </div>
          )}
          <button onClick={checkPerfConnection} className="mt-3 rounded-md bg-slate-100 px-3 py-1.5 text-xs font-medium hover:bg-slate-200">
            Проверить подключение к Ozon Performance API
          </button>
        </div>

        <form onSubmit={savePerfCredentials} className="space-y-3 rounded-md border border-slate-200 bg-white p-4">
          <h3 className="text-sm font-semibold text-slate-700">Указать новые ключи Performance API</h3>
          <div>
            <label className="mb-1 block text-sm text-slate-600">Client-Id</label>
            <input value={perfClientId} onChange={(e) => setPerfClientId(e.target.value)} required className="w-full rounded-md border border-slate-300 px-2 py-1 text-sm" />
          </div>
          <div>
            <label className="mb-1 block text-sm text-slate-600">Client-Secret</label>
            <input
              type="password"
              value={perfClientSecret}
              onChange={(e) => setPerfClientSecret(e.target.value)}
              required
              className="w-full rounded-md border border-slate-300 px-2 py-1 text-sm"
            />
          </div>
          <p className="text-xs text-slate-400">
            Отдельная пара ключей от Seller API — создаётся в личном кабинете Ozon в разделе «Продвижение → API-ключи».
          </p>
          <button type="submit" className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700">
            Сохранить ключи
          </button>
        </form>

        {perfMessage && <div className="rounded-md bg-slate-50 p-2 text-xs text-slate-600">{perfMessage}</div>}
      </section>

      <div>
        <h3 className="mb-2 text-sm font-semibold text-slate-700">Журнал синхронизаций</h3>
        {runs.length === 0 ? (
          <div className="text-slate-500">Нет данных</div>
        ) : (
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-slate-200 text-xs text-slate-500">
                <th className="py-1">Начало</th>
                <th>Источник</th>
                <th>Статус</th>
                <th>Получено</th>
                <th>Создано</th>
                <th>Дублей / обновлено</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => (
                <tr key={r.id} className="border-b border-slate-100">
                  <td className="py-1">{new Date(r.started_at).toLocaleString("ru-RU")}</td>
                  <td>{SOURCE_LABELS[r.source_type] ?? r.source_type}</td>
                  <td>{r.status}</td>
                  <td>{r.items_fetched}</td>
                  <td>{r.items_created}</td>
                  <td>{r.items_skipped_duplicate}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
