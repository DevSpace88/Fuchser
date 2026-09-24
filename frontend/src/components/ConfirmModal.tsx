// ============================================================================
// ConfirmModal.tsx — stylish confirmation modal in the theme (instead of window.confirm).
// ============================================================================
// Deliberately lean, without an additional dependency (we skip Radix & co.):
// fixed overlay + centered card, ESC/overlay click closes it,
// destructive variant (delete) in red.

import { useEffect } from "react";
import { createPortal } from "react-dom";
import { AlertTriangle, X } from "lucide-react";

export interface ConfirmModalProps {
  open: boolean;
  title: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  destructive?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export function ConfirmModal({
  open,
  title,
  message,
  confirmLabel = "Bestätigen",
  cancelLabel = "Abbrechen",
  destructive = false,
  onConfirm,
  onCancel,
}: ConfirmModalProps) {
  // ESC closes (only when open)
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onCancel]);

  if (!open) return null;

  // PORTAL onto the body: parent elements with a CSS transform (e.g. hover:
  // -translate-y on chat cards) otherwise make position:fixed relative to the
  // CARD — the modal "sat" on the card and flickered (user feedback).
  // A portal escapes any transform/overflow context.
  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      onClick={onCancel}
      role="dialog"
      aria-modal="true"
    >
      <div
        className="w-full max-w-md rounded-2xl border bg-card p-6 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-3">
            <span
              className={
                "flex h-10 w-10 shrink-0 items-center justify-center rounded-xl " +
                (destructive ? "bg-destructive/15 text-destructive" : "bg-primary/10 text-primary")
              }
            >
              <AlertTriangle className="h-5 w-5" />
            </span>
            <h2 className="font-semibold text-foreground">{title}</h2>
          </div>
          <button
            onClick={onCancel}
            className="rounded-md p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            aria-label="Schließen"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <p className="mt-4 whitespace-pre-line text-sm leading-relaxed text-muted-foreground">
          {message}
        </p>

        <div className="mt-6 flex justify-end gap-2">
          <button
            onClick={onCancel}
            className="rounded-lg border px-4 py-2 text-sm font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          >
            {cancelLabel}
          </button>
          <button
            onClick={onConfirm}
            className={
              "rounded-lg px-4 py-2 text-sm font-medium text-white shadow-md transition-colors " +
              (destructive
                ? "bg-destructive hover:bg-destructive/90"
                : "bg-primary hover:bg-primary/90")
            }
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
