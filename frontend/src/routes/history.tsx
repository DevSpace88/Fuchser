// routes/history.tsx — Die Chat-Historie: UNSERER UNTERHALTUNGEN.
// ============================================================================
// Google-AI-Studio-Modell: Die Historie listet CHATS (Conversations), nicht
// einzelne Fragen. Nachfragen sind Nachrichten INNERHALB eines Chats.
// Jede Karte: Titel (umbenennbar ✏️), Nachrichtenzahl, Token, letzter Stand.

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { FoxIcon } from "@/components/FoxIcon";
import { ConfirmModal } from "@/components/ConfirmModal";
import {
  Zap,
  MessageSquare,
  AlertCircle,
  Pencil,
  Check,
  X,
  Trash2,
} from "lucide-react";
import {
  listConversations,
  renameConversation,
  deleteConversation,
  type Conversation,
} from "@/lib/research";

function HistoryEntry({
  conversation,
  onRenamed,
  onDeleted,
}: {
  conversation: Conversation;
  onRenamed: (updated: Conversation) => void;
  onDeleted: (id: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(conversation.title);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const save = async () => {
    const t = draft.trim();
    if (t && t !== conversation.title) {
      try {
        onRenamed(await renameConversation(conversation.id, t));
      } catch {
        setDraft(conversation.title);
      }
    }
    setEditing(false);
  };

  return (
    <div className="group rounded-2xl border bg-card p-4 shadow-sm transition-all hover:-translate-y-0.5 hover:shadow-md">
      <div className="flex items-center gap-1.5">
        {editing ? (
          <div className="flex flex-1 items-center gap-1.5">
            <input
              autoFocus
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void save();
                if (e.key === "Escape") setEditing(false);
              }}
              maxLength={200}
              className="flex-1 rounded-lg border bg-background px-2.5 py-1.5 text-sm font-medium outline-none focus:border-primary"
            />
            <button
              onClick={() => void save()}
              className="rounded-md p-1.5 text-primary hover:bg-primary/10"
              title="Speichern (Enter)"
            >
              <Check className="h-4 w-4" />
            </button>
            <button
              onClick={() => setEditing(false)}
              className="rounded-md p-1.5 text-muted-foreground hover:bg-muted"
              title="Abbrechen (Esc)"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        ) : (
          <>
            <Link
              to={`/research?conversation=${conversation.id}`}
              className="min-w-0 flex-1 truncate font-medium text-foreground group-hover:text-primary"
              title={conversation.title}
            >
              {conversation.title}
            </Link>
            <button
              onClick={() => {
                setDraft(conversation.title);
                setEditing(true);
              }}
              className="shrink-0 rounded-md p-1.5 text-muted-foreground opacity-80 sm:opacity-0 transition-opacity hover:bg-muted hover:text-primary sm:group-hover:opacity-100"
              title="Umbenennen"
            >
              <Pencil className="h-3.5 w-3.5" />
            </button>
            <button
              onClick={() => setConfirmDelete(true)}
              className="shrink-0 rounded-md p-1.5 text-muted-foreground opacity-80 sm:opacity-0 transition-opacity hover:bg-destructive/10 hover:text-destructive sm:group-hover:opacity-100"
              title="Unterhaltung löschen"
            >
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          </>
        )}
      </div>
      <p className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
        <span className="flex items-center gap-1">
          <MessageSquare className="h-3 w-3 text-primary" />
          {conversation.message_count}{" "}
          {conversation.message_count === 1 ? "Nachricht" : "Nachrichten"}
        </span>
        {conversation.total_tokens > 0 && (
          <span className="flex items-center gap-1">
            <Zap className="h-3 w-3 text-secondary" />
            {(conversation.total_tokens / 1000).toFixed(1)}k Token
          </span>
        )}
        <span>{new Date(conversation.updated_at).toLocaleString("de-DE")}</span>
      </p>

      <ConfirmModal
        open={confirmDelete}
        title="Unterhaltung löschen?"
        message={
          `„${conversation.title}" wird endgültig gelöscht.\n` +
          `Das betrifft ${conversation.message_count} Nachricht(en) inklusive Reports, Quellen und hochgeladener Dokumente.\n\n` +
          `Dieser Schritt kann nicht rückgängig gemacht werden.`
        }
        confirmLabel="Endgültig löschen"
        destructive
        onCancel={() => setConfirmDelete(false)}
        onConfirm={() => {
          setConfirmDelete(false);
          deleteConversation(conversation.id)
            .then(() => onDeleted(conversation.id))
            .catch(console.error);
        }}
      />
    </div>
  );
}

export function HistoryPage() {
  const [conversations, setConversations] = useState<Conversation[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listConversations()
      .then(setConversations)
      .catch((e) => setError(e.message));
  }, []);

  return (
    <div className="container max-w-4xl px-3 sm:px-6 py-6 sm:py-10">
      <div className="flex items-center gap-3">
        <FoxIcon size={28} className="text-primary shrink-0" />
        <div>
          <h1 className="text-xl sm:text-2xl font-bold">Chat-Historie</h1>
          <p className="text-xs sm:text-sm text-muted-foreground">
            Deine Unterhaltungen. Nachfragen gehören zum Chat, in dem sie
            gestellt wurden — ✏️ umbenennen nach Herzenslust.
          </p>
        </div>
      </div>

      {error && (
        <p className="mt-4 sm:mt-6 flex items-center gap-2 rounded-lg border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
          <AlertCircle className="h-4 w-4" /> {error}
        </p>
      )}
      {conversations !== null && conversations.length === 0 && (
        <div className="mt-8 sm:mt-10 rounded-2xl border bg-card p-6 sm:p-10 text-center text-muted-foreground">
          <FoxIcon size={56} className="mx-auto mb-3 text-primary/30" />
          <p>Noch keine Unterhaltungen.</p>
          <Link
            to="/research"
            className="mt-3 inline-block text-sm font-medium text-primary hover:underline"
          >
            Erste Frage stellen →
          </Link>
        </div>
      )}

      <div className="mt-6 sm:mt-8 space-y-3">
        {conversations?.map((c) => (
          <HistoryEntry
            key={c.id}
            conversation={c}
            onRenamed={(updated) =>
              setConversations((prev) =>
                (prev ?? []).map((x) => (x.id === updated.id ? { ...x, ...updated } : x)),
              )
            }
            onDeleted={(id) =>
              setConversations((prev) => (prev ?? []).filter((x) => x.id !== id))
            }
          />
        ))}
      </div>
    </div>
  );
}
