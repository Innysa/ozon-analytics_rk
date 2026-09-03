import { createContext, useContext, useEffect, useState, ReactNode, useCallback } from "react";
import { api } from "../api/client";
import type { Store } from "../types";
import { useAuth } from "../auth/AuthContext";

interface StoreContextValue {
  stores: Store[];
  currentStore: Store | null;
  setCurrentStoreId: (id: string) => void;
  loading: boolean;
  refreshStores: () => Promise<void>;
}

const StoreContext = createContext<StoreContextValue | undefined>(undefined);

const LAST_STORE_KEY = "oaa_last_store_id";

export function StoreProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  const [stores, setStores] = useState<Store[]>([]);
  const [currentStoreId, setCurrentStoreIdState] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const refreshStores = useCallback(async () => {
    if (!user) {
      setStores([]);
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const data = await api.get<Store[]>("/stores");
      setStores(data);
      setCurrentStoreIdState((prev) => {
        if (prev && data.some((s) => s.id === prev)) return prev;
        const remembered = localStorage.getItem(LAST_STORE_KEY);
        if (remembered && data.some((s) => s.id === remembered)) return remembered;
        return data[0]?.id ?? null;
      });
    } finally {
      setLoading(false);
    }
  }, [user]);

  useEffect(() => {
    refreshStores();
  }, [refreshStores]);

  const setCurrentStoreId = (id: string) => {
    setCurrentStoreIdState(id);
    localStorage.setItem(LAST_STORE_KEY, id);
  };

  const currentStore = stores.find((s) => s.id === currentStoreId) ?? null;

  return (
    <StoreContext.Provider value={{ stores, currentStore, setCurrentStoreId, loading, refreshStores }}>
      {children}
    </StoreContext.Provider>
  );
}

export function useStore(): StoreContextValue {
  const ctx = useContext(StoreContext);
  if (!ctx) throw new Error("useStore must be used within StoreProvider");
  return ctx;
}
