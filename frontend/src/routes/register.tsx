// routes/register.tsx — Registrierungs-Seite.

import { useState, type FormEvent } from "react";
import { Navigate, useNavigate, Link } from "react-router-dom";
import { useAuth } from "@/lib/auth";
import { ApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export function RegisterPage() {
  const { register, user, loading } = useAuth();
  const navigate = useNavigate();

  // Bereits eingeloggt? Registrieren macht dann keinen Sinn -> Dashboard.
  if (!loading && user) {
    return <Navigate to="/dashboard" replace />;
  }

  const [email, setEmail] = useState("");
  const [fullName, setFullName] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    // Client-seitiger Abgleich VOR dem Request: Tippfehler im Passwort
    // merkt man so sofort — der Server müsste sie sonst per E-Mail-Rücksetz-
    // Flow "heilen" (den gibt es hier noch nicht).
    if (password !== confirmPassword) {
      setError("Die Passwörter stimmen nicht überein.");
      return;
    }
    setSubmitting(true);
    try {
      // register() legt den Account an UND loggt direkt ein.
      await register(email, password, fullName || undefined);
      navigate("/dashboard", { replace: true });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Registrierung fehlgeschlagen.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="container flex min-h-screen items-center justify-center px-4 py-8">
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle>Registrieren</CardTitle>
          <CardDescription>Lege ein neues Konto an.</CardDescription>
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
              <Label htmlFor="name">Vollständiger Name (optional)</Label>
              <Input
                id="name"
                type="text"
                value={fullName}
                onChange={(e) => setFullName(e.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="password">Passwort (min. 8 Zeichen)</Label>
              <Input
                id="password"
                type="password"
                required
                minLength={8}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="new-password"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="confirm-password">Passwort bestätigen</Label>
              <Input
                id="confirm-password"
                type="password"
                required
                minLength={8}
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                autoComplete="new-password"
                // Live-Feedback: Feld bekommt roten Ring, sobald beide
                // gefüllt sind und nicht übereinstimmen.
                className={
                  confirmPassword && password !== confirmPassword
                    ? "border-destructive focus-visible:ring-destructive"
                    : ""
                }
              />
              {confirmPassword && password !== confirmPassword && (
                <p className="text-xs text-destructive">
                  Die Passwörter stimmen nicht überein.
                </p>
              )}
            </div>
            {error && (
              <p className="rounded bg-destructive/10 p-2 text-sm text-destructive">
                {error}
              </p>
            )}
            <Button type="submit" className="w-full" disabled={submitting}>
              {submitting ? "Wird angelegt…" : "Konto erstellen"}
            </Button>
            <p className="text-center text-sm text-muted-foreground">
              Schon dabei?{" "}
              <Link to="/login" className="underline">
                Anmelden
              </Link>
            </p>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
