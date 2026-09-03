import { FormEvent, useEffect, useState } from "react";
import { api } from "../api/client";
import { useStore } from "../store/StoreContext";
import type { Store } from "../types";

export function AdminStoresPage() {
  const { stores, refreshStores } = useStore();
  const [name, setName] = useState("");
  const [legalName, setLegalName] = useState("");

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    await api.post<Store>("/stores", { name, legal_name: legalName || null });
    setName("");
    setLegalName("");
    refreshStores();
  };

  return (
    <div className="max-w-2xl space-y-4">
      <h1 className="text-lg font-semibold text-slate-800">Магазины</h1>

      <form onSubmit={onSubmit} className="flex flex-wrap items-end gap-2 rounded-md border border-slate-200 bg-white p-4">
        <div>
          <label className="mb-1 block text-sm text-slate-600">Название магазина</label>
          <input required value={name} onChange={(e) => setName(e.target.value)} className="rounded-md border border-slate-300 px-2 py-1 text-sm" />
        </div>
        <div>
          <label className="mb-1 block text-sm text-slate-600">Юр. название (необязательно)</label>
          <input value={legalName} onChange={(e) => setLegalName(e.target.value)} className="rounded-md border border-slate-300 px-2 py-1 text-sm" />
        </div>
        <button type="submit" className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700">
          Добавить магазин
        </button>
      </form>

      <table className="w-full text-left text-sm">
        <thead>
          <tr className="border-b border-slate-200 text-xs text-slate-500">
            <th className="py-1">Название</th>
            <th>Юр. название</th>
            <th>Последняя синхронизация</th>
          </tr>
        </thead>
        <tbody>
          {stores.map((s) => (
            <tr key={s.id} className="border-b border-slate-100">
              <td className="py-1">{s.name}</td>
              <td>{s.legal_name ?? "—"}</td>
              <td>{s.last_sync_at ? new Date(s.last_sync_at).toLocaleString("ru-RU") : "ещё не выполнялась"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
