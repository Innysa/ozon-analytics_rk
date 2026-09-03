import { useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { useStore } from "../store/StoreContext";

function formatSyncDate(iso: string | null): string {
  if (!iso) return "ещё не выполнялась";
  return new Date(iso).toLocaleString("ru-RU");
}

export function TopBar() {
  const { user, logout } = useAuth();
  const { stores, currentStore, setCurrentStoreId } = useStore();
  const navigate = useNavigate();

  const handleLogout = async () => {
    await logout();
    navigate("/login");
  };

  return (
    <header className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 bg-white px-4 py-3">
      <div className="flex items-center gap-3">
        <span className="text-lg font-semibold text-slate-800">Ozon AI Аналитик</span>
        {currentStore && (
          <select
            className="rounded-md border border-slate-300 px-2 py-1 text-sm"
            value={currentStore.id}
            onChange={(e) => setCurrentStoreId(e.target.value)}
          >
            {stores.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
        )}
      </div>

      <div className="flex items-center gap-4 text-sm text-slate-600">
        {currentStore && (
          <span title="Дата последней синхронизации">
            Синхронизация: {formatSyncDate(currentStore.last_sync_at)}
          </span>
        )}
        {user && (
          <span className="font-medium text-slate-800">
            {user.full_name} {user.is_admin && <span className="text-xs text-indigo-600">(admin)</span>}
          </span>
        )}
        <button onClick={handleLogout} className="rounded-md bg-slate-100 px-3 py-1 hover:bg-slate-200">
          Выйти
        </button>
      </div>
    </header>
  );
}
