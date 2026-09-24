// ============================================================================
// ResearchPanel.tsx — Das rechte Sidepanel für EINE Recherche (Stufe 5+).
// ============================================================================
// Ersetzt die alte Detail-Seite: Alles lebt im Chat; dieses Panel zeigt
// Kontext zur ausgewählten Recherche:
//   * Kopf: Status, Datum, Thread, Token-Bilanz
//   * PDF-Download
//   * "Ergänzung suchen": Follow-up-Frage im selben Thread (Memory!)
//   * Alle Quellen (vollständig, klickbar)
//   * AUSFÜHRUNGS-HISTORIE: Timeline der Agenten-Events (aus project.trace)

import { useEffect, useRef, useState } from "react";
import {
  X,
  FileDown,
  BookOpen,
  Zap,
  Brain,
  Globe,
  FileText,
  ShieldCheck,
  Check,
  CircleDot,
  Loader2,
  ChevronDown,
  Paperclip,
  Trash2,
  RefreshCw,
} from "lucide-react";
import { getFreshAccessToken } from "@/lib/api";
import {
  getResearch,
  downloadResearchPdf,
  uploadDocuments,
  type DocumentInfo,
  type ResearchProject,
} from "@/lib/research";

// --- Trace-Timeline: Icon + Label je Event-Typ -------------------------------
const NODE_META: Record<string, { icon: typeof Brain; label: string; color: string }> = {
  supervisor: { icon: Brain, label: "Supervisor", color: "text-primary" },
  researcher: { icon: Globe, label: "Researcher", color: "text-secondary" },
  synthesizer: { icon: FileText, label: "Synthesizer", color: "text-accent" },
  critic: { icon: ShieldCheck, label: "Kritiker", color: "text-chart-4" },
};

interface TraceEntry {
  t: string;
  event: string;
  node?: string;
  status?: string;
  verdict?: string;
  planned?: number;
  sub_question?: string;
}

function TraceLine({ entry }: { entry: TraceEntry }) {
  const time = new Date(entry.t).toLocaleTimeString("de-DE", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });

  let icon = <CircleDot className="h-3 w-3" />;
  let text = entry.event;
  let color = "text-muted-foreground";

  if (entry.event === "node" && entry.node && NODE_META[entry.node]) {
    const meta = NODE_META[entry.node];
    const Icon = meta.icon;
    icon = <Icon className="h-3 w-3" />;
    color = meta.color;
    if (entry.status === "start") text = `${meta.label} gestartet`;
    else if (entry.status === "end") text = `${meta.label} fertig`;
    else if (entry.status === "loop") text = `${meta.label}: Lücken → neue Runde`;
  } else if (entry.event === "subagents_planned") {
    text = `${entry.planned} Sub-Fragen geplant`;
  } else if (entry.event === "subagent") {
    icon = <Check className="h-3 w-3" />;
    text = `„${(entry.sub_question ?? "").slice(0, 50)}…" recherchiert`;
  } else if (entry.event === "status") {
    text = `Phase: ${entry.status}`;
  } else if (entry.event === "done") {
    icon = <Check className="h-3 w-3" />;
    text = "Recherche abgeschlossen";
  }

  return (
    <li className="flex items-start gap-2 py-1 text-xs">
      <span className={`mt-0.5 shrink-0 ${color}`}>{icon}</span>
      <span className="min-w-0 flex-1 text-foreground/90">{text}</span>
      <span className="shrink-0 font-mono text-[10px] text-muted-foreground">{time}</span>
    </li>
  );
}

// --- Das Panel ----------------------------------------------------------------
export function ResearchPanel({
  projectId,
  chainIds,
  onClose,
  onSelectProject,
}: {
  projectId: string;
  /** Alle Projekte der Unterhaltung (Kette) — für die gemeinsame Historie & Umschaltung. */
  chainIds?: string[];
  onClose: () => void;
  /** Umschalten des aktiven Projekts (synchronisiert mit Chat-Scroll). */
  onSelectProject?: (projectId: string) => void;
}) {
  const [selectedId, setSelectedId] = useState(projectId);
  const [project, setProject] = useState<ResearchProject | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sourcesOpen, setSourcesOpen] = useState(true);
  const [historyOpen, setHistoryOpen] = useState(false);
  // Dokumente der Unterhaltung (Upload/Löschen direkt im Panel)
  const [documents, setDocuments] = useState<DocumentInfo[]>([]);
  const [docBusy, setDocBusy] = useState(false);
  const docInputRef = useRef<HTMLInputElement>(null);

  // Synchronisation wenn von außen (z. B. durch Chat-Scroll) ein neues projectId kommt
  useEffect(() => {
    setSelectedId(projectId);
  }, [projectId]);

  const tokenHeader = async () => ({
    Authorization: `Bearer ${(await getFreshAccessToken()) ?? ""}`,
  });

  const loadDocuments = (pid: string) => {
    tokenHeader()
      .then((headers) =>
        fetch(`/api/v1/research/${pid}/documents?chain=true`, { headers }),
      )
      .then((r) => (r.ok ? r.json() : []))
      .then(setDocuments)
      .catch(() => {});
  };

  const uploadToProject = async (files: File[]) => {
    if (!files.length) return;
    setDocBusy(true);
    try {
      await uploadDocuments(selectedId, files);
      loadDocuments(selectedId);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Upload fehlgeschlagen");
    } finally {
      setDocBusy(false);
    }
  };

  const deleteDoc = async (docId: string, ownerProjectId: string) => {
    setDocBusy(true);
    try {
      await fetch(`/api/v1/research/${ownerProjectId}/documents/${docId}`, {
        method: "DELETE",
        headers: await tokenHeader(),
      });
      loadDocuments(selectedId);
    } finally {
      setDocBusy(false);
    }
  };

  // Kette: alle Projekte der Unterhaltung (älteste zuerst)
  const [chainProjects, setChainProjects] = useState<ResearchProject[]>([]);
  const effectiveChain = chainIds && chainIds.length > 0 ? chainIds : [selectedId];

  const loadChain = () => {
    Promise.all(effectiveChain.map((id) => getResearch(id).catch(() => null)))
      .then((all) => {
        const valid = all.filter((p): p is ResearchProject => p !== null);
        valid.sort((a, b) => +new Date(a.created_at) - +new Date(b.created_at));
        setChainProjects(valid);
        const match = valid.find((p) => p.id === selectedId) ?? valid.at(-1) ?? null;
        setProject(match);
      })
      .catch((e) => setError(e.message));
  };

  useEffect(() => {
    getResearch(selectedId)
      .then(setProject)
      .catch((e) => setError(e.message));
    loadDocuments(selectedId);
    loadChain();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId, chainIds?.join(",")]);

  const handleSelectTurn = (id: string) => {
    setSelectedId(id);
    onSelectProject?.(id);
  };

  const currentTurnIndex = chainProjects.findIndex((p) => p.id === selectedId);

  return (
    <aside className="pointer-events-auto flex h-full w-full flex-col overflow-hidden rounded-2xl border border-border/80 bg-card shadow-2xl">
      {/* Kopf */}
      <div className="flex items-center justify-between border-b px-4 py-3 bg-muted/20">
        <h2 className="flex items-center gap-2 text-sm font-semibold">
          <FileText className="h-4 w-4 text-primary" /> Recherche-Details
        </h2>
        <button onClick={onClose} className="rounded p-1 hover:bg-muted" aria-label="Schließen">
          <X className="h-4 w-4" />
        </button>
      </div>

      {/* Umschalter für Nachfragen / Verlaufs-Kette (wenn mehr als 1 Prompt in der Unterhaltung) */}
      {chainProjects.length > 1 && (
        <div className="border-b bg-muted/10 px-3 py-2">
          <div className="mb-1.5 flex items-center justify-between text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
            <span>Fragen in dieser Unterhaltung</span>
            <span className="font-mono text-primary font-bold">
              {currentTurnIndex >= 0 ? currentTurnIndex + 1 : 1} / {chainProjects.length}
            </span>
          </div>
          <div className="flex gap-1.5 overflow-x-auto pb-1 scrollbar-none">
            {chainProjects.map((p, idx) => {
              const active = p.id === selectedId;
              return (
                <button
                  key={p.id}
                  onClick={() => handleSelectTurn(p.id)}
                  className={`flex shrink-0 items-center gap-1.5 rounded-lg border px-2.5 py-1 text-xs transition-all ${
                    active
                      ? "border-primary bg-primary text-primary-foreground font-semibold shadow-sm"
                      : "border-border/70 bg-background text-muted-foreground hover:border-primary/40 hover:text-foreground"
                  }`}
                  title={p.question}
                >
                  <span className={`font-mono text-[10px] ${active ? "opacity-90" : "opacity-60"}`}>
                    #{idx + 1}
                  </span>
                  <span className="max-w-[120px] truncate">{p.title || p.question}</span>
                </button>
              );
            })}
          </div>
        </div>
      )}

      <div className="flex-1 overflow-y-auto px-4 py-3">
        {error && <p className="text-sm text-destructive">{error}</p>}
        {!project && !error && (
          <p className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" /> Lade …
          </p>
        )}
        {project && (
          <>
            {/* Frage-Titel & Turn Badge */}
            <div className="mb-3 rounded-xl border border-primary/20 bg-primary/5 p-2.5">
              <div className="flex items-center justify-between gap-1 text-[11px] text-muted-foreground mb-1">
                <span className="font-semibold uppercase tracking-wider text-primary">
                  {currentTurnIndex >= 0
                    ? currentTurnIndex === 0
                      ? "Hauptfrage"
                      : `Nachfrage #${currentTurnIndex + 1}`
                    : "Ausgewählte Frage"}
                </span>
                <span className="font-mono text-[10px]">
                  {new Date(project.created_at).toLocaleTimeString("de-DE", {
                    hour: "2-digit",
                    minute: "2-digit",
                  })}
                </span>
              </div>
              <p className="text-xs sm:text-sm font-medium text-foreground leading-snug line-clamp-3" title={project.question}>
                {project.question}
              </p>
            </div>

            {/* Meta */}
            <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
              <span className="rounded-full bg-primary/10 px-2 py-0.5 font-medium text-primary">
                {project.status}
              </span>
              {project.usage && project.usage.total_tokens > 0 && (
                <span className="flex items-center gap-1 rounded-full bg-secondary/10 px-2 py-0.5 font-mono text-secondary">
                  <Zap className="h-3 w-3" />
                  {(project.usage.total_tokens / 1000).toFixed(1)}k Token
                </span>
              )}
              <span>{new Date(project.created_at).toLocaleDateString("de-DE")}</span>
            </div>

            {/* PDF Download Button für die aktuell ausgewählte Frage */}
            <button
              onClick={() => downloadResearchPdf(project.id).catch(console.error)}
              disabled={!project.report}
              className="mt-3 flex w-full items-center justify-center gap-2 rounded-lg border border-primary/40 bg-primary/10 px-3 py-2 text-xs sm:text-sm font-semibold text-primary transition-colors hover:bg-primary/20 disabled:opacity-50"
            >
              <FileDown className="h-4 w-4" />
              {chainProjects.length > 1
                ? `PDF zu Frage #${currentTurnIndex + 1} herunterladen`
                : "Report als PDF herunterladen"}
            </button>

            {/* Dokumente der Unterhaltung (Upload + Verwaltung) */}
            <div className="mt-3 rounded-lg border border-border/70 p-2.5">
              <div className="flex items-center justify-between">
                <span className="flex items-center gap-1.5 text-xs font-semibold text-muted-foreground">
                  <Paperclip className="h-3.5 w-3.5 text-secondary" />
                  Dokumente ({documents.length})
                </span>
                <input
                  ref={docInputRef}
                  type="file"
                  multiple
                  accept=".pdf,.docx,.txt,.md,.csv"
                  className="hidden"
                  onChange={(e) => {
                    void uploadToProject(Array.from(e.target.files ?? []));
                    e.target.value = "";
                  }}
                />
                <button
                  onClick={() => docInputRef.current?.click()}
                  disabled={docBusy}
                  className="flex items-center gap-1 rounded-md px-2 py-1 text-xs text-secondary transition-colors hover:bg-secondary/10 disabled:opacity-50"
                >
                  {docBusy ? <RefreshCw className="h-3 w-3 animate-spin" /> : <Paperclip className="h-3 w-3" />}
                  Hinzufügen
                </button>
              </div>
              {documents.length === 0 && (
                <p className="mt-1.5 text-[11px] text-muted-foreground">
                  Noch keine Dateien. Der Fuchs durchsucht Uploads bei jeder
                  Recherche dieser Unterhaltung (auch bei Follow-ups).
                </p>
              )}
              {documents.length > 0 && (
                <ul className="mt-1.5 space-y-1">
                  {documents.map((d) => (
                    <li key={d.id} className="flex items-center justify-between gap-2 text-xs">
                      <span className="min-w-0 flex-1 truncate" title={d.filename}>
                        📄 {d.filename}
                        <span className="ml-1 text-muted-foreground">
                          ({(d.size_bytes / 1024).toFixed(0)} KB · {d.char_count.toLocaleString("de-DE")} Zeichen)
                        </span>
                      </span>
                      <button
                        onClick={() => void deleteDoc(d.id, d.project_id)}
                        disabled={docBusy}
                        className="shrink-0 rounded p-1 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                        title="Entfernen (wirkt ab dem nächsten Lauf)"
                      >
                        <Trash2 className="h-3 w-3" />
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>


            {/* Quellen dieser Frage (aus-/einklappbar) */}
            {project.sources && project.sources.length > 0 && (
              <div className="mt-4 rounded-xl border border-border/60 bg-muted/20 p-2.5">
                <button
                  type="button"
                  onClick={() => setSourcesOpen((prev) => !prev)}
                  className="flex w-full items-center justify-between text-xs font-semibold uppercase tracking-wider text-muted-foreground transition-colors hover:text-foreground"
                >
                  <span className="flex items-center gap-1.5">
                    <BookOpen className="h-3.5 w-3.5 text-accent" /> Quellen ({project.sources.length})
                  </span>
                  <ChevronDown
                    className={`h-4 w-4 transition-transform duration-200 ${
                      sourcesOpen ? "rotate-0" : "-rotate-90"
                    }`}
                  />
                </button>
                {sourcesOpen && (
                  <ol className="mt-2.5 space-y-1.5 pl-0.5">
                    {project.sources.map((s, i) => (
                      <li key={i} className="text-xs leading-snug">
                        <span className="mr-1 font-mono text-muted-foreground">[{i + 1}]</span>
                        <a
                          href={s.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-accent hover:underline break-all"
                        >
                          {s.title || s.url}
                        </a>
                      </li>
                    ))}
                  </ol>
                )}
              </div>
            )}

            {/* Ausführungs-Historie: Timeline der Agenten-Events */}
            {chainProjects.some((p) => (p.trace ?? []).length > 0) && (
              <div className="mt-3 rounded-xl border border-border/60 bg-muted/20 p-2.5">
                <button
                  type="button"
                  onClick={() => setHistoryOpen((prev) => !prev)}
                  className="flex w-full items-center justify-between text-xs font-semibold uppercase tracking-wider text-muted-foreground transition-colors hover:text-foreground"
                >
                  <span className="flex items-center gap-1.5">
                    <Loader2 className="h-3.5 w-3.5 text-secondary" /> Historie
                    ({chainProjects.reduce((n, p) => n + (p.trace?.length ?? 0), 0)}
                    {chainProjects.length > 1 ? ` · ${chainProjects.length} Läufe` : ""})
                  </span>
                  <ChevronDown
                    className={`h-4 w-4 transition-transform duration-200 ${
                      historyOpen ? "rotate-0" : "-rotate-90"
                    }`}
                  />
                </button>
                {historyOpen && (
                  <div>
                    <button
                      type="button"
                      onClick={loadChain}
                      className="mb-1.5 flex items-center gap-1 text-[11px] text-muted-foreground hover:text-primary"
                    >
                      <RefreshCw className="h-3 w-3" /> aktualisieren
                    </button>
                    {chainProjects.map((p, idx) => (
                      <div key={p.id} className={p.id === selectedId ? "rounded-lg bg-primary/5 p-1.5 -mx-1" : ""}>
                        {chainProjects.length > 1 && (
                          <div className="mb-1 mt-2 flex items-center justify-between">
                            <span className="text-[11px] font-semibold text-secondary">
                              Frage #{idx + 1}: {p.title ?? p.question}
                            </span>
                            {p.id === selectedId && (
                              <span className="rounded bg-primary/20 px-1 py-0.2 text-[9px] font-bold text-primary">
                                Aktiv
                              </span>
                            )}
                          </div>
                        )}
                        <ul className="border-l pl-2">
                          {((p.trace ?? []) as TraceEntry[]).map((entry, i) => (
                            <TraceLine key={`${p.id}-${i}`} entry={entry} />
                          ))}
                        </ul>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </>
        )}
      </div>
    </aside>
  );
}
