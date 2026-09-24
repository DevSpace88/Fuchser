// routes/admin.tsx — Admin-Seite.
// Demonstriert AUTORISIERUNG auf Frontend-Seite: nur User mit role==="admin".
//
// ACHTUNG: Das ist nur UX (Benutzerfreundlichkeit), KEINE Sicherheit!
// Die echte Autorisierung macht das BACKEND (require_admin-Dependency).
// Ein findiger User könnte die Frontend-Prüfung umgehen — aber der API-Call
// auf /api/v1/users würde dann 403 liefern. Frontend-Checks sind Komfort,
// Backend-Checks sind Sicherheit.

import { useEffect, useState } from "react";
import { useAuth } from "@/lib/auth";
import { apiFetch, ApiError, type User } from "@/lib/api";
import { listAllResearch, type AdminResearchEntry } from "@/lib/research";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export function AdminPage() {
  const { user } = useAuth();
  const [users, setUsers] = useState<User[]>([]);
  const [research, setResearch] = useState<AdminResearchEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // Listen aller User + aller Recherchen. Backend prüft: Admin?
    // Wenn nicht -> 403, und wir zeigen die Fehlermeldung an.
    (async () => {
      try {
        const [userData, researchData] = await Promise.all([
          apiFetch<User[]>("/api/v1/users"),
          listAllResearch(),
        ]);
        setUsers(userData);
        setResearch(researchData);
      } catch (err) {
        setError(err instanceof ApiError ? err.message : "Laden fehlgeschlagen.");
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  if (!user) return null;

  return (
    <div className="container max-w-5xl px-3 sm:px-6 py-6 sm:py-10">
      <h1 className="text-xl sm:text-2xl font-bold">Admin-Bereich</h1>
      <p className="mt-1 text-xs sm:text-sm text-muted-foreground">
        Nur für Admins. Übersicht über User und Recherchen.
      </p>

      <Card className="mt-6 max-w-3xl">
        <CardHeader className="p-4 sm:p-6">
          <CardTitle className="text-base sm:text-lg">Users ({users.length})</CardTitle>
        </CardHeader>
        <CardContent className="p-4 sm:p-6 pt-0 sm:pt-0">
          {loading && <p className="text-sm text-muted-foreground">Lade…</p>}
          {error && <p className="text-sm text-destructive">{error}</p>}
          {!loading && !error && (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[480px] text-xs sm:text-sm">
                <thead className="border-b text-left text-muted-foreground">
                  <tr>
                    <th className="py-2">E-Mail</th>
                    <th>Name</th>
                    <th>Rolle</th>
                    <th>Aktiv</th>
                  </tr>
                </thead>
                <tbody>
                  {users.map((u) => (
                    <tr key={u.id} className="border-b last:border-0">
                      <td className="py-2">{u.email}</td>
                      <td>{u.full_name ?? "—"}</td>
                      <td>{u.role}</td>
                      <td>{u.is_active ? "ja" : "nein"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      <Card className="mt-6">
        <CardHeader className="p-4 sm:p-6">
          <CardTitle className="text-base sm:text-lg">🦊 Recherchen aller User ({research.length})</CardTitle>
        </CardHeader>
        <CardContent className="p-4 sm:p-6 pt-0 sm:pt-0">
          {loading && <p className="text-sm text-muted-foreground">Lade…</p>}
          {!loading && research.length === 0 && (
            <p className="text-sm text-muted-foreground">Noch keine Recherchen.</p>
          )}
          {research.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[560px] text-xs sm:text-sm">
                <thead className="border-b text-left text-muted-foreground">
                  <tr>
                    <th className="py-2">Frage</th>
                    <th>User</th>
                    <th>Status</th>
                    <th>Token</th>
                    <th>Datum</th>
                  </tr>
                </thead>
                <tbody>
                  {research.map((r) => (
                    <tr key={r.id} className="border-b last:border-0">
                      <td className="max-w-[280px] sm:max-w-[320px] truncate py-2" title={r.question}>
                        {r.parent_id && <span className="mr-1 text-accent">↳</span>}
                        {r.question}
                      </td>
                      <td className="text-muted-foreground">{r.user_email}</td>
                      <td>{r.status}</td>
                      <td className="font-mono text-xs">
                        {r.usage?.total_tokens
                          ? `${(r.usage.total_tokens / 1000).toFixed(1)}k`
                          : "—"}
                      </td>
                      <td className="text-xs text-muted-foreground">
                        {new Date(r.created_at).toLocaleDateString("de-DE")}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
