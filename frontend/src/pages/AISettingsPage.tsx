import { FormEvent, useEffect, useState } from "react";
import { api } from "../api/client";
import { useStore } from "../store/StoreContext";
import type { StoreAISettings } from "../types";

const FIELDS: { key: keyof StoreAISettings; label: string; type: "text" | "textarea" | "checkbox" | "select" }[] = [
  { key: "brand_name", label: "Название магазина/бренда", type: "text" },
  { key: "tone_of_voice", label: "Стиль общения", type: "text" },
  { key: "customer_address_form", label: "Обращение к покупателю", type: "text" },
  { key: "signature", label: "Подпись в конце ответа", type: "text" },
  { key: "forbidden_words", label: "Запрещённые слова (по одному на строку)", type: "textarea" },
  { key: "allowed_promises", label: "Разрешённые обещания", type: "textarea" },
  { key: "negative_review_rules", label: "Правила ответа на негатив", type: "textarea" },
  { key: "warranty_info", label: "Информация о гарантии", type: "textarea" },
  { key: "return_policy_info", label: "Информация о возвратах", type: "textarea" },
  { key: "support_contacts", label: "Контакты поддержки", type: "textarea" },
  { key: "product_facts", label: "Факты о товарах", type: "textarea" },
];

export function AISettingsPage() {
  const { currentStore } = useStore();
  const [settings, setSettings] = useState<StoreAISettings | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (!currentStore) return;
    api.get<StoreAISettings>(`/stores/${currentStore.id}/ai-settings`).then(setSettings);
  }, [currentStore]);

  if (!currentStore || !settings) return <div className="text-slate-500">Загрузка...</div>;

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    const updated = await api.put<StoreAISettings>(`/stores/${currentStore.id}/ai-settings`, settings);
    setSettings(updated);
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  };

  return (
    <div className="max-w-2xl space-y-4">
      <h1 className="text-lg font-semibold text-slate-800">Настройки ответов — {currentStore.name}</h1>
      <p className="text-sm text-slate-500">
        Эти настройки определяют, как ИИ формулирует ответы покупателям для этого магазина. Изменить их могут owner и admin.
      </p>
      <form onSubmit={onSubmit} className="space-y-3 rounded-md border border-slate-200 bg-white p-4">
        {FIELDS.map((f) => (
          <div key={f.key}>
            <label className="mb-1 block text-sm text-slate-600">{f.label}</label>
            {f.type === "textarea" ? (
              <textarea
                value={settings[f.key] as string ?? ""}
                onChange={(e) => setSettings({ ...settings, [f.key]: e.target.value })}
                className="w-full rounded-md border border-slate-300 px-2 py-1 text-sm"
                rows={2}
              />
            ) : (
              <input
                value={settings[f.key] as string ?? ""}
                onChange={(e) => setSettings({ ...settings, [f.key]: e.target.value })}
                className="w-full rounded-md border border-slate-300 px-2 py-1 text-sm"
              />
            )}
          </div>
        ))}

        <div className="flex items-center gap-4">
          <label className="text-sm text-slate-600">Длина ответа</label>
          <select
            value={settings.reply_length}
            onChange={(e) => setSettings({ ...settings, reply_length: e.target.value })}
            className="rounded-md border border-slate-300 px-2 py-1 text-sm"
          >
            <option value="short">Короткая</option>
            <option value="medium">Средняя</option>
            <option value="long">Длинная</option>
          </select>

          <label className="flex items-center gap-2 text-sm text-slate-600">
            <input
              type="checkbox"
              checked={settings.use_emoji}
              onChange={(e) => setSettings({ ...settings, use_emoji: e.target.checked })}
            />
            Использовать эмодзи
          </label>
        </div>

        <button type="submit" className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700">
          Сохранить
        </button>
        {saved && <span className="ml-2 text-sm text-emerald-600">Сохранено</span>}
      </form>
    </div>
  );
}
