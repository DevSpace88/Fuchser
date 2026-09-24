// routes/login.tsx — login page.
// Demonstrates the typical flow: form -> useAuth().login -> redirect.

import { useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate, Link } from "react-router-dom";
import { useAuth } from "@/lib/auth";
import { ApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export function LoginPage() {
  const { login, user, loading } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  // Already logged in? Then the login page makes no sense — go straight
  // to the dashboard (user feedback: no pointless login form).
  if (!loading && user) {
    return <Navigate to="/dashboard" replace />;
  }

  // If we were led here from a "protected" page, we want to go back exactly
  // THERE after a successful login. Otherwise -> /dashboard.
  const from = (location.state as { from?: { pathname: string } } | null)?.from?.pathname ?? "/dashboard";

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(email, password);
      navigate(from, { replace: true });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Login fehlgeschlagen.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="container flex min-h-screen items-center justify-center px-4 py-8">
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle>Login</CardTitle>
          <CardDescription>Melde dich mit deiner E-Mail an.</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={onSubmit} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="email">E-Mail</Label>
              <Input
                id="email"
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                autoComplete="email"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="password">Passwort</Label>
              <Input
                id="password"
                type="password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
              />
            </div>
            {error && (
              <p className="rounded bg-destructive/10 p-2 text-sm text-destructive">
                {error}
              </p>
            )}
            <Button type="submit" className="w-full" disabled={submitting}>
              {submitting ? "Anmelden…" : "Anmelden"}
            </Button>
            <p className="text-center text-sm text-muted-foreground">
              Noch kein Konto?{" "}
              <Link to="/register" className="underline">
                Registrieren
              </Link>
            </p>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
