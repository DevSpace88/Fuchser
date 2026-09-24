// ============================================================================
// lib/auth.tsx — React context for the login state
// ============================================================================
//
// This context is the ONE place where the app knows "who is logged in".
// All components use the useAuth() hook to:
//   - read the current user,
//   - log in / log out / register,
//   - check whether loading is still in progress.
//
// Structure:
//   <AuthProvider>        // wraps the whole app (see main.tsx)
//     <App />
//   </AuthProvider>
//
// In a component:
//   const { user, login, logout } = useAuth();

import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { apiFetch, tokenStore, type TokenPair, type User } from "@/lib/api";

interface AuthContextValue {
  user: User | null; // null = not logged in
  loading: boolean; // true = initial check not done yet
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, fullName?: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  // --- AT STARTUP: check whether we still have valid tokens ---
  // If an access token is in localStorage, we try /auth/me.
  // On success we are logged in; on failure (token expired etc.)
  // apiFetch takes care of the refresh internally.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (!tokenStore.getAccess() && !tokenStore.getRefresh()) {
        setLoading(false);
        return;
      }
      try {
        const me = await apiFetch<User>("/api/v1/auth/me");
        if (!cancelled) setUser(me);
      } catch {
        if (!cancelled) setUser(null);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // --- LOGIN ---
  async function login(email: string, password: string) {
    const data = await apiFetch<TokenPair>("/api/v1/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
    tokenStore.set(data.access_token, data.refresh_token);
    setUser(data.user);
  }

  // --- REGISTER ---
  async function register(email: string, password: string, fullName?: string) {
    // 1) Create the account.
    await apiFetch<User>("/api/v1/auth/register", {
      method: "POST",
      body: JSON.stringify({ email, password, full_name: fullName ?? null }),
    });
    // 2) Log in immediately (convenient — the user does not want to click
    //    through twice). In real apps an email verification step would
    //    often be inserted here.
    await login(email, password);
  }

  // --- LOGOUT ---
  async function logout() {
    // Tell the backend (revoke the refresh token).
    const refresh = tokenStore.getRefresh();
    try {
      await apiFetch("/api/v1/auth/logout", {
        method: "POST",
        body: JSON.stringify({ refresh_token: refresh }),
      });
    } catch {
      // not a problem — we log out locally regardless.
    }
    tokenStore.clear();
    setUser(null);
  }

  return (
    <AuthContext.Provider value={{ user, loading, login, register, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

// Hook for components.
// Deliberately throws an error when useAuth is called OUTSIDE the provider —
// that is a programming error and should be caught early.
export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth muss innerhalb eines <AuthProvider> genutzt werden.");
  return ctx;
}
