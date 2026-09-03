import { Navigate, Outlet, Route, Routes } from "react-router-dom";
import { AuthProvider, useAuth } from "./auth/AuthContext";
import { StoreProvider, useStore } from "./store/StoreContext";
import { TopBar } from "./components/TopBar";
import { Sidebar } from "./components/Sidebar";
import { LoginPage } from "./pages/LoginPage";
import { ReviewsPage } from "./pages/ReviewsPage";
import { AnalyticsPage } from "./pages/AnalyticsPage";
import { ProductsPage } from "./pages/ProductsPage";
import { ProductDetailPage } from "./pages/ProductDetailPage";
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
      <Layout />
    </StoreProvider>
  );
}

function Layout() {
  const { loading, stores, currentStore } = useStore();
  return (
    <div className="flex min-h-screen flex-col">
      <TopBar />
      <div className="flex flex-1">
        <Sidebar />
        <main className="flex-1 p-4">
          {loading ? (
            <div className="text-slate-500">Загрузка магазинов...</div>
          ) : stores.length === 0 ? (
            <div className="rounded-md border border-amber-300 bg-amber-50 p-4 text-amber-800">
              У вас нет доступа ни к одному магазину. Обратитесь к администратору.
            </div>
          ) : !currentStore ? (
            <div className="text-slate-500">Выберите магазин</div>
          ) : (
            <Outlet />
          )}
        </main>
      </div>
    </div>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route element={<RequireAuth />}>
          <Route path="/" element={<Navigate to="/reviews" replace />} />
          <Route path="/reviews" element={<ReviewsPage />} />
          <Route path="/analytics" element={<AnalyticsPage />} />
          <Route path="/products" element={<ProductsPage />} />
          <Route path="/products/:productId" element={<ProductDetailPage />} />
          <Route path="/ai-settings" element={<AISettingsPage />} />
          <Route path="/ozon-settings" element={<OzonSettingsPage />} />
          <Route path="/admin/stores" element={<AdminStoresPage />} />
          <Route path="/admin/users" element={<AdminUsersPage />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AuthProvider>
  );
}
