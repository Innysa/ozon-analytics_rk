import { Link, Navigate, Outlet, Route, Routes } from "react-router-dom";
import { AuthProvider, useAuth } from "./auth/AuthContext";
import { StoreProvider, useStore } from "./store/StoreContext";
import { TopBar } from "./components/TopBar";
import { Sidebar } from "./components/Sidebar";
import { LoginPage } from "./pages/LoginPage";
import { ReviewsPage } from "./pages/ReviewsPage";
import { AnalyticsPage } from "./pages/AnalyticsPage";
import { ProductsPage } from "./pages/ProductsPage";
import { ProductDetailPage } from "./pages/ProductDetailPage";
import { AdvertisingPage } from "./pages/AdvertisingPage";
import { AISettingsPage } from "./pages/AISettingsPage";
import { OzonSettingsPage } from "./pages/OzonSettingsPage";
import { AdminStoresPage } from "./pages/AdminStoresPage";
import { AdminUsersPage } from "./pages/AdminUsersPage";

function RequireAuth() {
  const { user, loading } = useAuth();
  if (loading) return <div className="p-6 text-slate-500">Загрузка...</div>;
  if (!user) return <Navigate to="/login" replace />;
  return (
    <StoreProvider>
      <Shell />
    </StoreProvider>
  );
}

// Chrome (top bar + sidebar) shared by every authenticated page, admin pages
// included. Does NOT gate on store access — /admin/stores in particular must
// stay reachable with zero stores, since it's the only way to create the
// first one.
function Shell() {
  return (
    <div className="flex min-h-screen flex-col">
      <TopBar />
      <div className="flex flex-1">
        <Sidebar />
        <main className="flex-1 p-4">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

// Gate for pages that operate on a specific store (reviews, analytics,
// products, etc.) — these need at least one store to exist and one selected.
// Admin pages are NOT nested under this route, precisely so a fresh admin
// with zero stores can still reach "Магазины" to create the first one.
function StoreScopedRoute() {
  const { user } = useAuth();
  const { loading, stores, currentStore } = useStore();
  if (loading) return <div className="text-slate-500">Загрузка магазинов...</div>;
  if (stores.length === 0) {
    return (
      <div className="rounded-md border border-amber-300 bg-amber-50 p-4 text-amber-800">
        {user?.is_admin ? (
          <>
            У вас пока нет ни одного магазина. Создайте первый в разделе{" "}
            <Link to="/admin/stores" className="underline">
              «Администрирование → Магазины»
            </Link>
            .
          </>
        ) : (
          "У вас нет доступа ни к одному магазину. Обратитесь к администратору."
        )}
      </div>
    );
  }
  if (!currentStore) return <div className="text-slate-500">Выберите магазин</div>;
  return <Outlet />;
}

export default function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route element={<RequireAuth />}>
          <Route element={<StoreScopedRoute />}>
            <Route path="/" element={<Navigate to="/reviews" replace />} />
            <Route path="/reviews" element={<ReviewsPage />} />
            <Route path="/analytics" element={<AnalyticsPage />} />
            <Route path="/products" element={<ProductsPage />} />
            <Route path="/products/:productId" element={<ProductDetailPage />} />
            <Route path="/advertising" element={<AdvertisingPage />} />
            <Route path="/ai-settings" element={<AISettingsPage />} />
            <Route path="/ozon-settings" element={<OzonSettingsPage />} />
          </Route>
          <Route path="/admin/stores" element={<AdminStoresPage />} />
          <Route path="/admin/users" element={<AdminUsersPage />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AuthProvider>
  );
}
