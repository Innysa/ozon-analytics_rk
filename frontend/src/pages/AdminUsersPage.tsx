import { FormEvent, useEffect, useState } from "react";
import { api, ApiError } from "../api/client";
import { useStore } from "../store/StoreContext";
import type { Membership, UserRow } from "../types";

export function AdminUsersPage() {
  const { stores } = useStore();
  const [users, setUsers] = useState<UserRow[]>([]);
  const [email, setEmail] = useState("");
  const [fullName, setFullName] = useState("");
  const [password, setPassword] = useState("");
  const [isAdmin, setIsAdmin] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [assignUserId, setAssignUserId] = useState("");
  const [assignStoreId, setAssignStoreId] = useState("");
  const [assignRole, setAssignRole] = useState<"owner" | "manager" | "viewer">("manager");
  const [memberships, setMemberships] = useState<Record<string, Membership[]>>({});

  const loadUsers = () => {
    api.get<UserRow[]>("/users").then((data) => {
      setUsers(data);
      data.forEach((u) => {
        api.get<Membership[]>(`/users/${u.id}/memberships`).then((m) => setMemberships((prev) => ({ ...prev, [u.id]: m })));
      });
    });
  };

  useEffect(loadUsers, []);

  const createUser = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      await api.post("/users", { email, full_name: fullName, password, is_admin: isAdmin });
      setEmail("");
      setFullName("");
      setPassword("");
      setIsAdmin(false);
      loadUsers();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Ошибка создания пользователя");
    }
  };

  const assignMembership = async (e: FormEvent) => {
    e.preventDefault();
    if (!assignUserId || !assignStoreId) return;
    setError(null);
    try {
      await api.post("/users/memberships", { user_id: assignUserId, store_id: assignStoreId, role: assignRole });
      loadUsers();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Ошибка назначения доступа");
    }
  };

  const storeName = (id: string) => stores.find((s) => s.id === id)?.name ?? id;

  return (
    <div className="max-w-3xl space-y-6">
      <h1 className="text-lg font-semibold text-slate-800">Пользователи</h1>

      <form onSubmit={createUser} className="space-y-2 rounded-md border border-slate-200 bg-white p-4">
        <h3 className="text-sm font-semibold text-slate-700">Новый пользователь</h3>
        <div className="flex flex-wrap gap-2">
          <input required type="email" placeholder="Email" value={email} onChange={(e) => setEmail(e.target.value)} className="rounded-md border border-slate-300 px-2 py-1 text-sm" />
          <input required placeholder="Имя" value={fullName} onChange={(e) => setFullName(e.target.value)} className="rounded-md border border-slate-300 px-2 py-1 text-sm" />
          <input required type="password" placeholder="Пароль" value={password} onChange={(e) => setPassword(e.target.value)} className="rounded-md border border-slate-300 px-2 py-1 text-sm" />
          <label className="flex items-center gap-1 text-sm text-slate-600">
            <input type="checkbox" checked={isAdmin} onChange={(e) => setIsAdmin(e.target.checked)} />
            Администратор платформы
          </label>
        </div>
        {error && <div className="text-xs text-red-600">{error}</div>}
        <button type="submit" className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700">
          Создать
        </button>
      </form>

      <form onSubmit={assignMembership} className="flex flex-wrap items-end gap-2 rounded-md border border-slate-200 bg-white p-4">
        <h3 className="w-full text-sm font-semibold text-slate-700">Назначить доступ к магазину</h3>
        <select value={assignUserId} onChange={(e) => setAssignUserId(e.target.value)} className="rounded-md border border-slate-300 px-2 py-1 text-sm">
          <option value="">Пользователь</option>
          {users.map((u) => (
            <option key={u.id} value={u.id}>
              {u.full_name} ({u.email})
            </option>
          ))}
        </select>
        <select value={assignStoreId} onChange={(e) => setAssignStoreId(e.target.value)} className="rounded-md border border-slate-300 px-2 py-1 text-sm">
          <option value="">Магазин</option>
          {stores.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
        <select value={assignRole} onChange={(e) => setAssignRole(e.target.value as any)} className="rounded-md border border-slate-300 px-2 py-1 text-sm">
          <option value="owner">owner</option>
          <option value="manager">manager</option>
          <option value="viewer">viewer</option>
        </select>
        <button type="submit" className="rounded-md bg-slate-100 px-3 py-1.5 text-sm font-medium hover:bg-slate-200">
          Назначить
        </button>
      </form>

      <table className="w-full text-left text-sm">
        <thead>
          <tr className="border-b border-slate-200 text-xs text-slate-500">
            <th className="py-1">Пользователь</th>
            <th>Роль платформы</th>
            <th>Доступы к магазинам</th>
          </tr>
        </thead>
        <tbody>
          {users.map((u) => (
            <tr key={u.id} className="border-b border-slate-100 align-top">
              <td className="py-1">{u.full_name} ({u.email})</td>
              <td>{u.is_admin ? "admin" : "—"}</td>
              <td>
                {(memberships[u.id] ?? []).map((m) => (
                  <div key={m.id}>
                    {storeName(m.store_id)} — {m.role}
                  </div>
                ))}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
