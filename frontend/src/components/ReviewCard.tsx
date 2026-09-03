import { useState } from "react";
import { api, ApiError } from "../api/client";
import type { Review } from "../types";

const STATUS_LABELS: Record<string, string> = {
  new: "Новый",
  pending_analysis: "Ожидает анализа",
  analyzed: "Проанализирован",
  draft_created: "Черновик создан",
  pending_approval: "Ожидает подтверждения",
  approved: "Одобрен",
  published: "Опубликован",
  publish_failed: "Ошибка публикации",
  no_reply_needed: "Ответ не требуется",
};

const SENTIMENT_LABELS: Record<string, string> = {
  positive: "Позитивный",
  neutral: "Нейтральный",
  negative: "Негативный",
};

function Stars({ rating }: { rating: number }) {
  return <span className="text-amber-500">{"★".repeat(rating)}{"☆".repeat(5 - rating)}</span>;
}

export function ReviewCard({ review, storeId, onChanged }: { review: Review; storeId: string; onChanged: () => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [draftText, setDraftText] = useState(review.latest_draft?.text ?? "");
  const [editing, setEditing] = useState(false);

  const base = `/stores/${storeId}/reviews/${review.id}`;

  const run = async (label: string, fn: () => Promise<unknown>) => {
    setBusy(label);
    setError(null);
    try {
      await fn();
      onChanged();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Ошибка запроса");
    } finally {
      setBusy(null);
    }
  };

  const draft = review.latest_draft;
  const canEdit = draft && draft.status === "draft";
  const canApprove = draft && draft.status === "draft";
  const canPublish = draft && draft.status === "approved";

  const copyToClipboard = async () => {
    if (!draft) return;
    await navigator.clipboard.writeText(draft.text);
    await run("copy", () => api.post(`${base}/comments/${draft.id}/copy`));
  };

  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-center gap-3">
          {review.product_image_url ? (
            <img src={review.product_image_url} alt="" className="h-12 w-12 rounded object-cover" />
          ) : (
            <div className="flex h-12 w-12 items-center justify-center rounded bg-slate-100 text-xs text-slate-400">нет фото</div>
          )}
          <div>
            <div className="text-sm font-medium text-slate-800">{review.product_name ?? "Товар не определён"}</div>
            <div className="text-xs text-slate-500">
              SKU: {review.product_sku ?? "—"} {review.product_offer_id ? `· Артикул: ${review.product_offer_id}` : ""}
            </div>
          </div>
        </div>
        <div className="text-right">
          <Stars rating={review.rating} />
          <div className="text-xs text-slate-500">{review.published_at ? new Date(review.published_at).toLocaleDateString("ru-RU") : "дата неизвестна"}</div>
        </div>
      </div>

      <div className="mt-3 space-y-1 text-sm">
        {review.text && <p className="text-slate-800">{review.text}</p>}
        {review.pros && <p className="text-emerald-700"><b>Достоинства:</b> {review.pros}</p>}
        {review.cons && <p className="text-red-700"><b>Недостатки:</b> {review.cons}</p>}
        {review.existing_seller_reply && (
          <p className="rounded bg-slate-50 p-2 text-slate-600"><b>Существующий ответ продавца:</b> {review.existing_seller_reply}</p>
        )}
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
        <span className="rounded-full bg-slate-100 px-2 py-1">{STATUS_LABELS[review.status] ?? review.status}</span>
        {review.analysis && (
          <>
            <span className="rounded-full bg-indigo-50 px-2 py-1 text-indigo-700">
              {SENTIMENT_LABELS[review.analysis.sentiment] ?? review.analysis.sentiment}
            </span>
            <span className="rounded-full bg-slate-50 px-2 py-1 text-slate-600">Категория: {review.analysis.category}</span>
            {review.analysis.urgency === "high" && (
              <span className="rounded-full bg-red-50 px-2 py-1 text-red-700">Требует внимания</span>
            )}
          </>
        )}
      </div>

      {review.analysis && (review.analysis.hypotheses.length > 0) && (
        <div className="mt-2 text-xs italic text-slate-500">
          Гипотезы ИИ (требуют проверки): {review.analysis.hypotheses.join("; ")}
        </div>
      )}

      {draft && (
        <div className="mt-3 rounded-md border border-slate-200 bg-slate-50 p-3">
          <div className="mb-1 flex items-center justify-between">
            <span className="text-xs font-medium text-slate-600">
              Черновик ответа {draft.generated_by_ai ? "(создан ИИ)" : ""} {draft.edited_by_user ? "· отредактирован" : ""}
            </span>
            <span className="text-xs text-slate-500">{draft.status}</span>
          </div>
          {editing ? (
            <textarea
              value={draftText}
              onChange={(e) => setDraftText(e.target.value)}
              className="w-full rounded-md border border-slate-300 p-2 text-sm"
              rows={4}
            />
          ) : (
            <p className="text-sm text-slate-800 whitespace-pre-wrap">{draft.text}</p>
          )}
          {draft.publish_error && <p className="mt-1 text-xs text-red-600">{draft.publish_error}</p>}
        </div>
      )}

      {error && <p className="mt-2 text-xs text-red-600">{error}</p>}

      <div className="mt-3 flex flex-wrap gap-2">
        <button
          disabled={!!busy}
          onClick={() => run("analyze", () => api.post(`${base}/analyze`))}
          className="rounded-md bg-slate-100 px-3 py-1.5 text-xs font-medium hover:bg-slate-200 disabled:opacity-50"
        >
          {busy === "analyze" ? "Анализ..." : "Проанализировать"}
        </button>

        <button
          disabled={!!busy}
          onClick={() => run("generate", () => api.post(`${base}/generate-draft`))}
          className="rounded-md bg-indigo-50 px-3 py-1.5 text-xs font-medium text-indigo-700 hover:bg-indigo-100 disabled:opacity-50"
        >
          {busy === "generate" ? "Генерация..." : draft ? "Сгенерировать заново" : "Сгенерировать ответ"}
        </button>

        {draft && draft.status === "draft" && (
          <>
            <button
              disabled={!!busy}
              onClick={() => run("shorter", () => api.post(`${base}/comments/${draft.id}/rewrite`, { instruction: "shorter" }))}
              className="rounded-md bg-slate-100 px-3 py-1.5 text-xs hover:bg-slate-200 disabled:opacity-50"
            >
              Короче
            </button>
            <button
              disabled={!!busy}
              onClick={() => run("warmer", () => api.post(`${base}/comments/${draft.id}/rewrite`, { instruction: "warmer" }))}
              className="rounded-md bg-slate-100 px-3 py-1.5 text-xs hover:bg-slate-200 disabled:opacity-50"
            >
              Теплее
            </button>
            <button
              disabled={!!busy}
              onClick={() => run("formal", () => api.post(`${base}/comments/${draft.id}/rewrite`, { instruction: "formal" }))}
              className="rounded-md bg-slate-100 px-3 py-1.5 text-xs hover:bg-slate-200 disabled:opacity-50"
            >
              Официальнее
            </button>
          </>
        )}

        {canEdit && !editing && (
          <button onClick={() => setEditing(true)} className="rounded-md bg-slate-100 px-3 py-1.5 text-xs hover:bg-slate-200">
            Редактировать
          </button>
        )}
        {canEdit && editing && (
          <button
            disabled={!!busy}
            onClick={() =>
              run("save", async () => {
                await api.patch(`${base}/comments/${draft!.id}`, { text: draftText });
                setEditing(false);
              })
            }
            className="rounded-md bg-emerald-50 px-3 py-1.5 text-xs text-emerald-700 hover:bg-emerald-100 disabled:opacity-50"
          >
            Сохранить
          </button>
        )}

        {canApprove && (
          <button
            disabled={!!busy}
            onClick={() => run("approve", () => api.post(`${base}/comments/${draft!.id}/approve`))}
            className="rounded-md bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
          >
            Одобрить
          </button>
        )}

        {canPublish && (
          <button
            disabled={!!busy}
            onClick={() => run("publish", () => api.post(`${base}/comments/${draft!.id}/publish`))}
            className="rounded-md bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
          >
            Опубликовать в Ozon
          </button>
        )}

        {draft && (
          <button onClick={copyToClipboard} className="rounded-md bg-slate-100 px-3 py-1.5 text-xs hover:bg-slate-200">
            Скопировать
          </button>
        )}

        {review.status !== "no_reply_needed" && review.status !== "published" && (
          <button
            disabled={!!busy}
            onClick={() => run("no-reply", () => api.post(`${base}/no-reply-needed`))}
            className="rounded-md bg-slate-50 px-3 py-1.5 text-xs text-slate-500 hover:bg-slate-100 disabled:opacity-50"
          >
            Не отвечать
          </button>
        )}
      </div>
    </div>
  );
}
