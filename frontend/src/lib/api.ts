// ============================================================================
// lib/api.ts — fetch-Wrapper mit JWT-Anhängung + automatischem Refresh
// ============================================================================
//
// Diese Datei ist das wichtigste Stück des Frontends fürs LERNEN: Hier sieht
// man, wie eine SPA mit JWT-basierter Auth umgeht.
//
// ─────────────────────────────────────────────────────────────────────────────
// WO SPEICHERN WIR DAS TOKEN? (Wichtige Entscheidung!)
// ─────────────────────────────────────────────────────────────────────────────
// Drei gängige Optionen, jede mit Vor-/Nachteilen:
//
//   1) localStorage  (DAS NUTZEN WIR HIER)
//      + einfach, überall in JS verfügbar, überlebt Reload.
//      - SCHLECHT gegen XSS: schädliches JS kann es auslesen.
//      -> OK für Lernen + interne Tools; für sensible Apps eher nicht.
//
//   2) httpOnly-Cookie (vom Backend gesetzt)
//      + immun gegen XSS (JS kann httpOnly-Cookies NICHT lesen).
//      - komplexeres Setup (CSRF-Schutz nötig, CORS-credentials).
//      -> Best-Practice für Produktion.
//
//   3) In-Memory (nur in einer Variable)
//      + immun gegen XSS.
//      - bei Reload weg -> Refresh beim Start nötig.
//
// In Produktion empfiehlt sich: Access-Token IN-MEMORY, Refresh-Token im
// httpOnly-Cookie. Hier für Lernzwecke: localStorage (einfach zu verstehen).
// Siehe auch README "Erweiterungen".

const ACCESS_TOKEN_KEY = "app.access_token";
const REFRESH_TOKEN_KEY = "app.refresh_token";

// ----------------------------------------------------------------------------
// Token-Storage: dünner Wrapper um localStorage, damit wir nur an EINER Stelle
// das "wie" ändern müssen (z. B. später auf Cookie umstellen).
// ----------------------------------------------------------------------------
export const tokenStore = {
  getAccess(): string | null {
    return localStorage.getItem(ACCESS_TOKEN_KEY);
  },
  getRefresh(): string | null {
    return localStorage.getItem(REFRESH_TOKEN_KEY);
  },
  set(access: string, refresh: string) {
    localStorage.setItem(ACCESS_TOKEN_KEY, access);
    localStorage.setItem(REFRESH_TOKEN_KEY, refresh);
  },
  clear() {
    localStorage.removeItem(ACCESS_TOKEN_KEY);
    localStorage.removeItem(REFRESH_TOKEN_KEY);
  },
};

// ----------------------------------------------------------------------------
// Typen, die zum Backend-Schema passen (siehe backend/app/schemas/auth.py).
// ----------------------------------------------------------------------------
export interface User {
  id: string;
  email: string;
  full_name: string | null;
  role: "user" | "admin";
  is_active: boolean;
  created_at: string;
}

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: string;
  user: User;
}

// ----------------------------------------------------------------------------
// REFRESH-LOGIK
// ----------------------------------------------------------------------------
// WICHTIG: wir müssen verhindern, dass BEI MEHREREN gleichzeitigen 401-Requests
// JEDER einen eigenen Refresh anstößt (Race-Condition). Lösung: eine einzelne
// "in-flight"-Promise, die alle warten.
let refreshPromise: Promise<string | null> | null = null;

// Exportiert, weil auch lib/sse.ts ihn nutzt (Streaming braucht denselben
// 401→Refresh→Retry-Flow wie apiFetch, kann aber nicht auf JSON warten).
export async function doRefresh(): Promise<string | null> {
  // Wenn schon ein Refresh läuft, denselben Promise wiederverwenden.
  if (refreshPromise) return refreshPromise;

  const refresh = tokenStore.getRefresh();
  if (!refresh) return null;

  refreshPromise = (async () => {
    try {
      const resp = await fetch("/api/v1/auth/refresh", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: refresh }),
      });
      if (!resp.ok) {
        // Refresh fehlgeschlagen (abgelaufen/revoked) -> User muss neu login.
        tokenStore.clear();
        return null;
      }
      const data: TokenPair = await resp.json();
      tokenStore.set(data.access_token, data.refresh_token);
      return data.access_token;
    } finally {
      // Promise zurücksetzen, damit ein späterer Refresh wieder neu starten kann.
      refreshPromise = null;
    }
  })();

  return refreshPromise;
}

// ----------------------------------------------------------------------------
// Proaktiver Refresh: Ablaufdatum aus dem JWT lesen (Base64-Dekodierung des
// Payloads — keine Verifikation nötig, der Server prüft eh) und VOR dem
// Request erneuern, wenn der Token in <30s abläuft. So entstehen gar keine
// sichtbaren 401er mehr durch abgelaufene Access-Tokens.
// ----------------------------------------------------------------------------
function tokenExpiresInMs(): number | null {
  const token = tokenStore.getAccess();
  if (!token) return null;
  try {
    const payload = JSON.parse(
      atob(token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")),
    );
    return (payload.exp ?? 0) * 1000 - Date.now();
  } catch {
    return null; // kaputtes Token -> Server entscheidet
  }
}

export async function getFreshAccessToken(): Promise<string | null> {
  const inMs = tokenExpiresInMs();
  if (inMs !== null && inMs > 30_000) return tokenStore.getAccess();
  return doRefresh(); // rechtzeitig (oder gar nicht mehr) erneuern
}

// ----------------------------------------------------------------------------
// apiFetch — die zentrale Fetch-Funktion für ALLE API-Aufrufe.
// ----------------------------------------------------------------------------
// Sie macht DREI Dinge automatisch:
//   1) JWT als Authorization-Header anhängen (falls vorhanden).
//   2) JSON senden/empfangen (Content-Type + parse).
//   3) Bei 401 EINMALIG einen Refresh versuchen und den Request wiederholen.
//
// Aufruf aus Komponenten:
//   const me = await apiFetch<User>("/api/v1/auth/me");
//   const users = await apiFetch<User[]>("/api/v1/users");
export async function apiFetch<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  // Hilfsfunktion: Request MIT optionalem Access-Token ausführen.
  const run = (accessToken: string | null): Promise<Response> => {
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      ...(options.headers as Record<string, string>),
    };
    if (accessToken) {
      headers["Authorization"] = `Bearer ${accessToken}`;
    }
    return fetch(path, { ...options, headers });
  };

  // Proaktiver Refresh statt blind losschicken (vermeidet die 401-Runde).
  let resp = await run(await getFreshAccessToken());

  // ───── 401 -> einmal Refresh versuchen (Fallback, z. B. Revocation) ─────
  if (resp.status === 401) {
    const newAccess = await doRefresh();
    if (newAccess) {
      // Refresh erfolgreich -> Request mit neuem Token nochmal.
      resp = await run(newAccess);
    }
  }

  // ───── Fehlerbehandlung ─────
  if (!resp.ok) {
    // Versuchen, eine detail-Message aus der Antwort zu extrahieren
    // (FastAPI liefert { "detail": "..." } oder { "detail": [{ "msg": "..." }] }).
    let detail = `HTTP ${resp.status}`;
    try {
      const body = await resp.json();
      if (body?.detail) {
        if (typeof body.detail === "string") {
          detail = body.detail;
        } else if (Array.isArray(body.detail)) {
          detail = body.detail
            .map((item: { msg?: string; loc?: (string | number)[] }) => {
              const field = item.loc ? item.loc.slice(1).join(".") : "";
              return field ? `${field}: ${item.msg || "ungültig"}` : (item.msg || JSON.stringify(item));
            })
            .join(", ");
        } else {
          detail = JSON.stringify(body.detail);
        }
      } else if (body?.message) {
        detail = body.message;
      }
    } catch {
      /* kein JSON -> detail beim HTTP-Code belassen */
    }
    throw new ApiError(resp.status, detail);
  }

  // 204 No Content -> nichts parsen.
  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
}

// Eigene Error-Klasse, damit Komponenten auf den Statuscode reagieren können.
export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
}
