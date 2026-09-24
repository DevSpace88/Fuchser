// ============================================================================
// lib/api.ts — fetch wrapper with JWT attachment + automatic refresh
// ============================================================================
//
// This file is the most important piece of the frontend for LEARNING: here you
// can see how an SPA deals with JWT-based auth.
//
// ─────────────────────────────────────────────────────────────────────────────
// WHERE DO WE STORE THE TOKEN? (Important decision!)
// ─────────────────────────────────────────────────────────────────────────────
// Three common options, each with pros/cons:
//
//   1) localStorage  (THAT'S WHAT WE USE HERE)
//      + simple, available everywhere in JS, survives a reload.
//      - BAD against XSS: malicious JS can read it.
//      -> OK for learning + internal tools; rather not for sensitive apps.
//
//   2) httpOnly cookie (set by the backend)
//      + immune to XSS (JS can NOT read httpOnly cookies).
//      - more complex setup (CSRF protection needed, CORS credentials).
//      -> best practice for production.
//
//   3) in-memory (only in a variable)
//      + immune to XSS.
//      - gone on reload -> refresh needed at startup.
//
// For production the recommendation is: access token IN-MEMORY, refresh token
// in an httpOnly cookie. Here, for learning purposes: localStorage (easy to
// understand). See also the README section "Erweiterungen" (extensions).

const ACCESS_TOKEN_KEY = "app.access_token";
const REFRESH_TOKEN_KEY = "app.refresh_token";

// ----------------------------------------------------------------------------
// Token storage: thin wrapper around localStorage so that we only need to
// change the "how" in ONE place (e.g. switch to a cookie later).
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
// Types that match the backend schema (see backend/app/schemas/auth.py).
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
// REFRESH LOGIC
// ----------------------------------------------------------------------------
// IMPORTANT: we must prevent EVERY one of several simultaneous 401 requests
// from triggering its own refresh (race condition). Solution: a single
// "in-flight" promise that all of them wait for.
let refreshPromise: Promise<string | null> | null = null;

// Exported because lib/sse.ts also uses it (streaming needs the same
// 401→refresh→retry flow as apiFetch, but cannot wait for JSON).
export async function doRefresh(): Promise<string | null> {
  // If a refresh is already running, reuse the same promise.
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
        // Refresh failed (expired/revoked) -> user must log in again.
        tokenStore.clear();
        return null;
      }
      const data: TokenPair = await resp.json();
      tokenStore.set(data.access_token, data.refresh_token);
      return data.access_token;
    } finally {
      // Reset the promise so a later refresh can start anew.
      refreshPromise = null;
    }
  })();

  return refreshPromise;
}

// ----------------------------------------------------------------------------
// Proactive refresh: read the expiry date from the JWT (base64-decoding the
// payload — no verification needed, the server checks it anyway) and renew
// BEFORE the request if the token expires in <30s. That way no more visible
// 401s ever occur due to expired access tokens.
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
    return null; // broken token -> server decides
  }
}

export async function getFreshAccessToken(): Promise<string | null> {
  const inMs = tokenExpiresInMs();
  if (inMs !== null && inMs > 30_000) return tokenStore.getAccess();
  return doRefresh(); // renew in time (or not at all anymore)
}

// ----------------------------------------------------------------------------
// apiFetch — the central fetch function for ALL API calls.
// ----------------------------------------------------------------------------
// It does THREE things automatically:
//   1) attach the JWT as the Authorization header (if present).
//   2) send/receive JSON (Content-Type + parse).
//   3) on 401, attempt ONE refresh and retry the request.
//
// Called from components:
//   const me = await apiFetch<User>("/api/v1/auth/me");
//   const users = await apiFetch<User[]>("/api/v1/users");
export async function apiFetch<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  // Helper: run the request WITH an optional access token.
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

  // Proactive refresh instead of sending blindly (avoids the 401 round trip).
  let resp = await run(await getFreshAccessToken());

  // ───── 401 -> try one refresh (fallback, e.g. revocation) ─────
  if (resp.status === 401) {
    const newAccess = await doRefresh();
    if (newAccess) {
      // Refresh successful -> repeat the request with the new token.
      resp = await run(newAccess);
    }
  }

  // ───── Error handling ─────
  if (!resp.ok) {
    // Try to extract a detail message from the response
    // (FastAPI returns { "detail": "..." } or { "detail": [{ "msg": "..." }] }).
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
      /* no JSON -> keep the detail at the HTTP code */
    }
    throw new ApiError(resp.status, detail);
  }

  // 204 No Content -> nothing to parse.
  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
}

// Own error class so components can react to the status code.
export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
}
