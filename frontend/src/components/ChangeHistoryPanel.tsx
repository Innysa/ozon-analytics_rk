import { FormEvent, useEffect, useState } from "react";
import { api } from "../api/client";
import type { ChangeHistoryEntry } from "../types";

const CHANGE_TYPES: { value: string; label: string }[] = [
  { value: "main_photo", label: "Замена главного фото" },
  { value: "seo", label: "Изменение SEO" },
  { value: "title", label: "Изменение названия" },
  { value: "description", label: "Изменение описания" },
  { value: "price", label: "Изменение цены" },
  { value: "characteristics", label: "Изменение характеристик" },
  { value: "advertising", label: "Изменение рекламы" },
  { value: "other", label: "Другое" },
];

export function ChangeHistoryPanel({ storeId, productId }: { storeId: string; productId: string }) {
  const [entries, setEntries] = useState<ChangeHistoryEntry[]>([]);
  const [showForm, setShowForm] = useState(false);
  const [changeType, setChangeType] = useState("other");
  const [changedAt, setChangedAt] = useState(() => new Date().toISOString().slice(0, 16));
  const [description, setDescription] = useState("");
  const [comment, setComment] = useState("");

  const load = () => {
    api.get<ChangeHistoryEntry[]>(`/stores/${storeId}/change-history?product_id=${productId}`).then(setEntries);
  };

  useEffect(load, [storeId, productId]);

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    await api.post(`/stores/${storeId}/change-history`, {
      product_id: productId,
      change_type: changeType,
      changed_at: new Date(changedAt).toISOString(),
      description,
      comment: comment || null,
    });
    setDescription("");
    setComment("");
    setShowForm(false);
    load();
  };

  return (
    <div className="space-y-3">
      <button onClick={() => setShowForm((v) => !v)} className="rounded-md bg-indigo-50 px-3 py-1.5 text-xs font-medium text-indigo-700 hover:bg-indigo-100">
        {showForm ? "Отмена" : "Зафиксировать изменение"}
      </button>

      {showForm && (
        <form onSubmit={onSubmit} className="space-y-2 rounded-md border border-slate-200 bg-white p-3 text-sm">
          <div className="flex gap-2">
            <select value={changeType} onChange={(e) => setChangeType(e.target.value)} className="rounded-md border border-slate-300 px-2 py-1">
              {CHANGE_TYPES.map((c) => (
                <option key={c.value} value={c.value}>
                  {c.label}
                </option>
              ))}
            </select>
            <input
              type="datetime-local"
              value={changedAt}
              onChange={(e) => setChangedAt(e.target.value)}
              className="rounded-md border border-slate-300 px-2 py-1"
            />
          </div>
          <input
            required
            placeholder="Описание изменения"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            className="w-full rounded-md border border-slate-300 px-2 py-1"
          />
          <input
            placeholder="Комментарий (необязательно)"
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            className="w-full rounded-md border border-slate-300 px-2 py-1"
          />
          <button type="submit" className="rounded-md bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-indigo-700">
            Сохранить
          </button>
        </form>
      )}

      {entries.length === 0 ? (
        <div className="text-slate-500">Нет данных</div>
      ) : (
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="border-b border-slate-200 text-xs text-slate-500">
              <th className="py-1">Дата</th>
              <th>Тип</th>
              <th>Описание</th>
              <th>Пользователь</th>
              <th>Комментарий</th>
            </tr>
          </thead>
          <tbody>
            {entries.map((e) => (
              <tr key={e.id} className="border-b border-slate-100">
                <td className="py-1">{new Date(e.changed_at).toLocaleString("ru-RU")}</td>
                <td>{CHANGE_TYPES.find((c) => c.value === e.change_type)?.label ?? e.change_type}</td>
                <td>{e.description}</td>
                <td>{e.user_name ?? "—"}</td>
                <td>{e.comment ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
