// routes/not_found.tsx — 404-Seite für unbekannte URLs.

import { Link, useNavigate } from "react-router-dom";
import { ArrowLeft, Home, Compass, Search } from "lucide-react";
import { useAuth } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { FoxIcon } from "@/components/FoxIcon";

export function NotFoundPage() {
  const { user } = useAuth();
  const navigate = useNavigate();

  return (
    <div className="container flex min-h-[calc(100vh-3.5rem)] flex-col items-center justify-center py-16 text-center">
      {/* 404 Badge mit Fuchs-Icon */}
      <div className="relative mb-6 flex items-center justify-center">
        <div className="absolute -inset-4 rounded-full bg-primary/10 blur-xl" />
        <div className="relative flex h-24 w-24 items-center justify-center rounded-3xl border border-border/80 bg-card shadow-lg">
          <FoxIcon size={48} className="text-primary" />
        </div>
      </div>

      <div className="inline-flex items-center gap-2 rounded-full border border-border/80 bg-muted/60 px-3.5 py-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
        <Compass className="h-3.5 w-3.5 text-primary" /> 404 Fehler
      </div>

      <h1 className="mt-4 text-3xl font-extrabold tracking-tight sm:text-4xl">
        Seite nicht gefunden
      </h1>

      <p className="mx-auto mt-3 max-w-md text-sm text-muted-foreground sm:text-base">
        Die von dir gesuchte Seite existiert nicht, wurde verschoben oder die Fährte hat sich verlaufen.
      </p>

      {/* Buttons je nach Login-Status */}
      <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
        <Button
          variant="outline"
          onClick={() => navigate(-1)}
          className="gap-2 shadow-sm"
        >
          <ArrowLeft className="h-4 w-4" /> Zurück
        </Button>

        {user ? (
          <>
            <Button asChild className="gap-2 shadow-md">
              <Link to="/dashboard">
                <Home className="h-4 w-4" /> Zum Dashboard
              </Link>
            </Button>
            <Button asChild variant="secondary" className="gap-2">
              <Link to="/research">
                <Search className="h-4 w-4" /> Zur Recherche
              </Link>
            </Button>
          </>
        ) : (
          <>
            <Button asChild className="gap-2 shadow-md">
              <Link to="/">
                <Home className="h-4 w-4" /> Zur Startseite
              </Link>
            </Button>
            <Button asChild variant="secondary">
              <Link to="/login">Anmelden</Link>
            </Button>
          </>
        )}
      </div>
    </div>
  );
}
