import { NavLink } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";

const linkClass = ({ isActive }: { isActive: boolean }) =>
  `block rounded-md px-3 py-2 text-sm ${isActive ? "bg-indigo-100 text-indigo-700 font-medium" : "text-slate-600 hover:bg-slate-100"}`;

export function Sidebar() {
  const { user } = useAuth();
  return (
    <nav className="w-56 shrink-0 border-r border-slate-200 bg-white p-3">
      <div className="space-y-1">
        <NavLink to="/reviews" className={linkClass}>
          Отзывы
        </NavLink>
        <NavLink to="/analytics" className={linkClass}>
          Аналитика отзывов
        </NavLink>
        <NavLink to="/products" className={linkClass}>
          Товары
        </NavLink>
        <NavLink to="/advertising" className={linkClass}>
          Реклама
        </NavLink>
        <NavLink to="/ai-settings" className={linkClass}>
          Настройки ответов
        </NavLink>
        <NavLink to="/ozon-settings" className={linkClass}>
          Подключение к Ozon
        </NavLink>
        {user?.is_admin && (
          <>
            <div className="mt-4 px-3 text-xs font-semibold uppercase text-slate-400">Администрирование</div>
            <NavLink to="/admin/stores" className={linkClass}>
              Магазины
            </NavLink>
            <NavLink to="/admin/users" className={linkClass}>
              Пользователи
            </NavLink>
          </>
        )}
      </div>
    </nav>
  );
}
