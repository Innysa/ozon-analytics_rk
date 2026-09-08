// Thin fetch wrapper. Session auth is a HttpOnly cookie — every request goes
// with credentials: "include" and we never read/write any token in JS.

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

// Field names as they appear in our Pydantic schemas -> human-readable Russian labels,
// used to turn FastAPI/Pydantic validation errors into a readable message instead of
// letting an array of error objects get stringified to "[object Object]".
const FIELD_LABELS: Record<string, string> = {
  email: "Email",
  password: "Пароль",
  full_name: "Имя",
  is_admin: "Администратор платформы",
  role: "Роль",
  user_id: "Пользователь",
  store_id: "Магазин",
};

function fieldLabel(loc: unknown): string {
  const parts = Array.isArray(loc) ? loc.filter((p) => typeof p === "string") : [];
  const field = parts[parts.length - 1];
  return (typeof field === "string" && FIELD_LABELS[field]) || (typeof field === "string" ? field : "Поле");
}

function formatValidationError(err: any): string {
  const label = fieldLabel(err?.loc);
  const ctx = err?.ctx ?? {};
  switch (err?.type) {
    case "string_too_short":
      return `${label}: слишком короткое значение (минимум ${ctx.min_length} симв.)`;
    case "string_too_long":
      return `${label}: слишком длинное значение (максимум ${ctx.max_length} симв.)`;
    case "missing":
      return `${label}: обязательное поле`;
    case "value_error":
    case "string_type":
    case "bool_type":
    case "int_type":
      return typeof err?.msg === "string" ? `${label}: ${err.msg}` : `${label}: некорректное значение`;
    default:
      return typeof err?.msg === "string" ? `${label}: ${err.msg}` : `${label}: некорректное значение`;
  }
}

// FastAPI validation errors (422) come back as `{ detail: [{type, loc, msg, ctx}, ...] }`,
// not a string — passing that array straight into Error() stringifies it to "[object Object]".
function formatErrorDetail(detail: unknown, fallback: string): string {
  if (typeof detail === "string" && detail) return detail;
  if (Array.isArray(detail) && detail.length > 0) {
    return detail.map(formatValidationError).join("; ");
  }
  return fallback;
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const res = await fetch(`/api${path}`, {
    ...options,
    credentials: "include",
    headers: {
      ...(options.body && !(options.body instanceof FormData) ? { "Content-Type": "application/json" } : {}),
      ...(options.headers || {}),
    },
  });

  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      detail = formatErrorDetail(data.detail, res.statusText);
    } catch {
      // ignore
    }
    throw new ApiError(res.status, detail);
  }

  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: body !== undefined ? JSON.stringify(body) : undefined }),
  patch: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "PATCH", body: body !== undefined ? JSON.stringify(body) : undefined }),
  put: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "PUT", body: body !== undefined ? JSON.stringify(body) : undefined }),
  delete: <T>(path: string) => request<T>(path, { method: "DELETE" }),
  upload: <T>(path: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<T>(path, { method: "POST", body: form });
  },
};
