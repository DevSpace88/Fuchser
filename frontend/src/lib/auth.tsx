// ============================================================================
// lib/auth.tsx — React Context für den Login-State
// ============================================================================
//
// Dieser Context ist die EINE Stelle, an der die App weiß, "wer ist eingeloggt".
// Alle Komponenten nutzen den useAuth()-Hook, um:
//   - den aktuellen User zu lesen,
//   - sich einzuloggen / auszuloggen / zu registrieren,
//   - zu prüfen, ob noch geladen wird.
//
// Aufbau:
//   <AuthProvider>        // umhüllt die ganze App (siehe main.tsx)
//     <App />
//   </AuthProvider>
//
// In einer Komponente:
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
  user: User | null; // null = nicht eingeloggt
  loading: boolean; // true = initial noch nicht gecheckt
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, fullName?: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  // --- BEIM START: prüfen, ob wir noch gültige Tokens haben ---
  // Wenn ein Access-Token im localStorage liegt, versuchen wir /auth/me.
  // Bei Erfolg sind wir eingeloggt; bei Misserfolg (Token abgelaufen etc.)
  // kümmert sich apiFetch intern um den Refresh.
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
    // 1) Account anlegen.
    await apiFetch<User>("/api/v1/auth/register", {
      method: "POST",
      body: JSON.stringify({ email, password, full_name: fullName ?? null }),
    });
    // 2) Sofort einloggen (komfortabel, der User will sich nicht doppelt
    //    durchklicken). In echten Apps würde man hier oft eine E-Mail-
    //    Verifizierung zwischenschalten.
    await login(email, password);
  }

  // --- LOGOUT ---
  async function logout() {
    // Backend bescheid geben (Refresh-Token widerrufen).
    const refresh = tokenStore.getRefresh();
    try {
      await apiFetch("/api/v1/auth/logout", {
        method: "POST",
        body: JSON.stringify({ refresh_token: refresh }),
      });
    } catch {
      // nicht schlimm — lokal loggen wir uns sowieso aus.
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

// Hook für Komponenten.
// Wirft bewusst einen Fehler, wenn useAuth AUSSERHALB des Providers aufgerufen
// wird — das ist ein Programmierfehler und sollte早期 auffallen.
export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth muss innerhalb eines <AuthProvider> genutzt werden.");
  return ctx;
}
