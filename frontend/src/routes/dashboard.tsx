// routes/dashboard.tsx — your overview: statistics + recent conversations.
// ============================================================================
// Consistent with the history page: the dashboard shows CONVERSATIONS (chats),
// not individual questions — the same unit as everywhere else.

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { MessageSquare, Zap, FileText, Sparkles, Plus, ArrowRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { FoxIcon } from "@/components/FoxIcon";
import { listConversations, type Conversation } from "@/lib/research";

function StatCard({
  icon: Icon,
  value,
  label,
  color,
}: {
  icon: typeof Zap;
  value: string | number;
  label: string;
  color: string;
}) {
  return (
    <div className="rounded-2xl border border-border/80 bg-card p-5 shadow-sm">
      <div className={`inline-flex h-10 w-10 items-center justify-center rounded-xl border ${color}`}>
        <Icon className="h-5 w-5" />
      </div>
      <div className="mt-3 text-2xl font-bold tabular-nums text-foreground">{value}</div>
      <div className="text-xs text-muted-foreground">{label}</div>
    </div>
  );
}

export function DashboardPage() {
  const [conversations, setConversations] = useState<Conversation[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listConversations()
      .then(setConversations)
      .catch((e) => setError(e.message));
  }, []);

  const totalMessages = (conversations ?? []).reduce((n, c) => n + c.message_count, 0);
  const totalTokens = (conversations ?? []).reduce((n, c) => n + c.total_tokens, 0);
  const recent = (conversations ?? []).slice(0, 5);

  return (
    <div className="container max-w-5xl px-3 sm:px-6 py-6 sm:py-10">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2.5 sm:gap-3">
          <FoxIcon size={28} className="text-primary shrink-0" />
          <div>
            <h1 className="text-xl sm:text-2xl font-bold">Dashboard</h1>
            <p className="text-xs sm:text-sm text-muted-foreground">
              Was willst du heute rausfinden?
            </p>
          </div>
        </div>
        <Button asChild size="sm" className="gap-1.5 shadow-lg sm:size-default">
          <Link to="/research?new=1">
            <Plus className="h-4 w-4" />
            <span>Neue Recherche</span>
          </Link>
        </Button>
      </div>

      {/* Statistics */}
      <div className="mt-8 grid grid-cols-1 gap-4 sm:grid-cols-3">
        <StatCard
          icon={MessageSquare}
          color="text-primary bg-primary/10 border-primary/20"
          value={conversations === null ? "…" : conversations.length}
          label="Unterhaltungen"
        />
        <StatCard
          icon={FileText}
          color="text-secondary bg-secondary/10 border-secondary/20"
          value={conversations === null ? "…" : totalMessages}
          label="Fragen gestellt"
        />
        <StatCard
          icon={Zap}
          color="text-accent bg-accent/10 border-accent/20"
          value={
            conversations === null
              ? "…"
              : totalTokens > 0
                ? `${(totalTokens / 1000).toFixed(0)}k`
                : "0"
          }
          label="Token verbraucht"
        />
      </div>

      {/* Recent conversations */}
      <div className="mt-10">
        <h2 className="text-lg font-semibold tracking-tight">Deine letzten Unterhaltungen</h2>
        <div className="mt-4 grid gap-3">
          {error && (
            <p className="rounded-xl border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
              {error}
            </p>
          )}

          {conversations !== null && conversations.length === 0 && (
            <div className="rounded-2xl border border-border/80 bg-card p-10 text-center text-muted-foreground">
              <Sparkles className="mx-auto h-8 w-8 text-primary/40 mb-2" />
              <p className="font-medium text-foreground">Noch keine Unterhaltungen.</p>
              <Button asChild variant="outline" className="mt-4 gap-2">
                <Link to="/research?new=1">
                  <Plus className="h-4 w-4" /> Erste Frage stellen
                </Link>
              </Button>
            </div>
          )}

          {recent.map((c) => (
            <Link
              key={c.id}
              to={`/research?conversation=${c.id}`}
              className="group flex items-center justify-between gap-4 rounded-2xl border border-border/80 bg-card p-4 shadow-sm transition-all duration-200 hover:-translate-y-0.5 hover:border-primary/40 hover:shadow-md"
            >
              <div className="min-w-0 flex-1">
                <p
                  className="line-clamp-2 text-sm font-medium leading-snug text-foreground break-words group-hover:text-primary transition-colors"
                  title={c.title}
                >
                  {c.title}
                </p>
                <div className="mt-1.5 flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
                  <span className="flex items-center gap-1">
                    <MessageSquare className="h-3 w-3 text-primary" />
                    {c.message_count} {c.message_count === 1 ? "Nachricht" : "Nachrichten"}
                  </span>
                  <span>{new Date(c.updated_at).toLocaleString("de-DE")}</span>
                  {c.total_tokens > 0 && (
                    <span className="flex items-center gap-1">
                      <Zap className="h-3 w-3 text-secondary" />
                      {(c.total_tokens / 1000).toFixed(1)}k Token
                    </span>
                  )}
                </div>
              </div>
              <ArrowRight className="h-4 w-4 shrink-0 text-muted-foreground transition-colors group-hover:text-primary" />
            </Link>
          ))}

          {conversations !== null && conversations.length > recent.length && (
            <Link
              to="/history"
              className="text-center text-sm text-muted-foreground underline-offset-2 hover:text-primary hover:underline"
            >
              Alle {conversations.length} Unterhaltungen in der Historie ansehen →
            </Link>
          )}
        </div>
      </div>
    </div>
  );
}
