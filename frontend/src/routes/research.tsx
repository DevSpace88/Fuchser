// routes/research.tsx — Die Recherche als CHAT (User-Wunsch: kein einzelnes
// Suchfeld mehr, sondern ein Gespräch wie auf der Landingpage simuliert).
// ============================================================================
//
// Jede Nachricht = ein Recherche-Projekt:
//   User-Bubble (rechts): die Frage
//   Fuchser-Bubble (links): Status-Zeile → Sub-Agent-Chips → live getippter
//   Report (Markdown) → Quellen-Chips → Aktionen (PDF, Detail-Ansicht).
//
// Die Historie kommt aus der Datenbank (listResearch, älteste zuerst) —
// neue Fragen hängen unten an. Gestreamt wird über dasselbe SSE-Protokoll
// wie bisher (lib/sse.ts), nur landet alles jetzt in Chat-Bubbles.

import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import Markdown from "react-markdown";
import { Button } from "@/components/ui/button";
import { FoxIcon } from "@/components/FoxIcon";
import { ResearchPanel } from "@/components/ResearchPanel";
import { PanelRightOpen } from "lucide-react";
import {
  Brain,
  Globe,
  FileText,
  ShieldCheck,
  BookOpen,
  Loader2,
  FileDown,
  RotateCw,
  Sparkles,
  Check,
  Send,
  AlertCircle,
  MessageCirclePlus,
  Zap,
  Link2,
  X,
  Square,
  MessageSquarePlus,
  Paperclip,
} from "lucide-react";
import {
  createResearch,
  getConversation,
  getResearch,
  listResearch,
  downloadResearchPdf,
  uploadDocuments,
  type ResearchProject,
  type Source,
} from "@/lib/research";
import { streamSSE } from "@/lib/sse";
import { linkifyCitations } from "@/lib/citations";

// ----------------------------------------------------------------------------
// Ein Chat-Beitrag: die Frage (von dir) + alles, was der Agent dazu erzeugt.
// ----------------------------------------------------------------------------
interface ChatEntry {
  projectId: string;
  question: string;
  status: string;
  report: string; // wächst beim Streaming
  sources: Source[];
  subagents: { sub_question: string; sources: number | null }[];
  activeAgent: string | null;
  error: string | null;
  running: boolean;
  parentId: string | null; // Stufe 5: Follow-up-Verweis
  usage: { total_tokens: number; llm_calls: number } | null;
  historical: boolean; // aus der DB geladen (vs. in DIESER Session gefragt)
  // Phase 2 "Deep Reports": Gliederung + Kapitel-Fortschritt (Todo-Liste)
  outline: { title: string; abstract: string; chapters: { title: string; focus: string }[] } | null;
  latestActivity: { tool: string; query: string; agent: string; t?: number } | null;
  activityLog: { tool: string; query: string; agent: string; t: number }[];
  chapterStatus: Record<number, "running" | "done">;
}

// Alte Projekte (aus der DB) → Chat-Einträge.
function entryFromProject(p: ResearchProject): ChatEntry {
  // Deep-Report nach Reload wiederherstellen: Gliederung + Kapitel-Fortschritt
  // + bereits geschriebene Kapitel als Report-Vorschau. So bleibt der
  // Teilerfolg eines ABGEBROCHENEN Laufs sichtbar (und "Fortsetzen" macht
  // ab dem ersten fehlenden Kapitel weiter, statt von vorne).
  const outline = p.outline ?? null;
  const chapters = p.chapters ?? [];
  const chapterStatus: Record<number, "running" | "done"> = {};
  if (outline) {
    outline.chapters.forEach((ch, i) => {
      if (chapters.some((c) => c.title === ch.title)) chapterStatus[i + 1] = "done";
    });
  }
  const restoredReport =
    p.report ??
    (chapters.length > 0
      ? chapters.map((c, i) => `## ${i + 1}. ${c.title}\n\n${c.content}`).join("\n\n")
      : "");
  return {
    projectId: p.id,
    question: p.question,
    status: p.status,
    report: restoredReport,
    sources: p.sources ?? [],
    subagents: [],
    activeAgent: null,
    error: p.error,
    running: false,
    parentId: p.parent_id,
    usage: p.usage
      ? { total_tokens: p.usage.total_tokens, llm_calls: p.usage.llm_calls }
      : null,
    historical: true,
    outline,
    latestActivity: null,
    activityLog: [],
    chapterStatus,
  };
}

const AGENT_LABEL: Record<string, string> = {
  supervisor: "Supervisor plant die Recherche",
  researcher: "Researcher suchen im Web",
  synthesizer: "Synthesizer schreibt den Report",
  critic: "Kritiker prüft die Qualität",
};

export function ResearchPage() {
  const [entries, setEntries] = useState<ChatEntry[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [loaded, setLoaded] = useState(false);
  // Stufe 5: Follow-up-Modus — die nächste Frage läuft im selben Thread
  // wie die ausgewählte Recherche (der Agent "erinnert" sich).
  const [followupOf, setFollowupOf] = useState<string | null>(null);
  // Sidepanel: Welche Recherche ist im rechten Panel geöffnet?
  const [panelId, setPanelId] = useState<string | null>(null);
  // Auto-Kontext (default AN): Jede neue Frage läuft automatisch im Thread
  // der letzten abgeschlossenen Recherche — der Chat ist EIN Gespräch.
  const [autoContext, setAutoContext] = useState(false);
  // Aktive Unterhaltung (ECHTES Chat-Modell): conversation-id + ihre
  // Nachrichten-IDs. Neue Fragen im Chat landen als NACHRICHT darin —
  // ohne parent-Ketten-Gebastel (Google-AI-Studio-Modell).
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  // Aktive Unterhaltung (Kette von Projekt-IDs): Per Historie-Klick
  // geöffnet — dann zeigt der Chat NUR diese Follow-up-Kette (Wurzel +
  // alle Nachfragen), nicht mehr die globale Historie.
  const [conversationChain, setConversationChain] = useState<string[] | null>(null);
  // "Neue Unterhaltung": true = nur DIESE Session zeigen (leerer Chat),
  // false = kompletter Verlauf. DEFAULT: true — /research öffnet FRISCH
  // (User-Wunsch); der Verlauf ist einen Klick entfernt.
  const [hideHistory, setHideHistory] = useState(true);
  // Abort-Controller je laufendem Projekt (Stop-Button).
  const abortMap = useRef<Record<string, AbortController>>({});
  // Aktive Timeouts für Reconnects & Cleanup beim Unmount
  const activeRetryTimeouts = useRef<Record<string, number>>({});
  const isMountedRef = useRef(true);

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
      Object.values(activeRetryTimeouts.current).forEach((t) => window.clearTimeout(t));
      activeRetryTimeouts.current = {};
    };
  }, []);
  // Phase 2: Tiefe der NÄCHSTEN Frage — "quick" oder "deep" (Langbericht).
  const [depth, setDepth] = useState<"quick" | "deep">("quick");
  const [citationStyle, setCitationStyle] = useState<"apa" | "ieee" | "plain">("ieee");
  // Dokumenten-Upload: Dateien, die an die NÄCHSTE Frage angehängt werden
  // (PDF/DOCX/TXT/MD/CSV — Text wird serverseitig extrahiert).
  const [pendingFiles, setPendingFiles] = useState<File[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);
  // Auto-Grow-Textarea: Höhe misst sich am Inhalt (auch bei langen Zeilen
  // OHNE Zeilenumbruch), wächst mit bis ~220px — danach interner Scroll.
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 220)}px`;
  }, [input]);
  // Fokus aus der URL (?focus=<id>, z. B. vom Dashboard): Panel öffnen.
  const [searchParams] = useSearchParams();
  // Auto-Scroll ans Ende bei neuen Inhalten.
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const fresh = searchParams.get("new");
    if (fresh) {
      // "Neue Recherche": LEERER Chat + Kontext AUS — die nächste Frage
      // startet ohne Altvorwissen und OHNE angezeigten Altverlauf.
      setAutoContext(false);
      setHideHistory(true);
      setPanelId(null);
      setTimeout(
        () => document.querySelector<HTMLTextAreaElement>("form textarea")?.focus(),
        150,
      );
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const focus = searchParams.get("focus");
    const convParam = searchParams.get("conversation");
    const openConversation = async (convId: string, focusId?: string) => {
      const detail = await getConversation(convId);
      const msgs = detail.messages.map(entryFromProject);
      setEntries((prev) => {
        // Bestehende Session-Einträge behalten (sie könnten laufen)
        const sessionEntries = prev.filter((e) => !e.historical && !msgs.some((m) => m.projectId === e.projectId));
        // LIVE-STATE MERGEN: Der Reload-Reconnect hat für laufende Tasks
        // bereits running=true + Live-Telemetrie gesetzt. Das Ersetzen durch
        // die DB-Frischversion (running=false) würde das auslöschen — die UI
        // zeigte dann keinen Fortschritt, obwohl der Worker arbeitet.
        const merged = msgs.map((m) => {
          const live = prev.find((e) => e.projectId === m.projectId);
          if (!live) return m;
          return {
            ...m,
            running: live.running || m.status === "running",
            activeAgent: live.activeAgent,
            latestActivity: live.latestActivity,
            activityLog: live.activityLog.length ? live.activityLog : m.activityLog,
            chapterStatus: { ...m.chapterStatus, ...live.chapterStatus },
          };
        });
        return [...merged, ...sessionEntries];
      });
      setActiveConversationId(convId);
      setConversationChain(msgs.map((m) => m.projectId));
      setAutoContext(true);
      if (focusId) {
        setPanelId(focusId);
        setTimeout(() => {
          document.getElementById(`entry-${focusId}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
        }, 100);
      }
    };

    listResearch()
      .then((projects) => {
        const loaded = projects.reverse().map(entryFromProject);
        setEntries(loaded);
        // Reload-Recovery: laufende Tasks wieder verbinden (Worker läuft weiter)
        loaded.forEach((e) => {
          if (e.status === "running" || e.status === "queued") {
            void runAgent(e.projectId, false);
          }
        });
        if (convParam) {
          void openConversation(convParam, focus ?? undefined);
          return;
        }
        if (focus) {
          // Legacy/Fokus: Projekt -> dessen Unterhaltung öffnen
          void getResearch(focus).then((p) => {
            if (p.conversation_id) void openConversation(p.conversation_id, focus);
          });
          return;
        }
        // Reload während eines LAUFENDEN Tasks: direkt zur Aufgabe zurück-
        // springen (Unterhaltung öffnen + fokussieren), statt einen leeren
        // Chat zu zeigen. Der User landet so wieder dort, wo er war.
        const active = loaded.find(
          (e) => e.status === "running" || e.status === "queued",
        );
        if (active) {
          void getResearch(active.projectId).then((p) => {
            if (p.conversation_id) {
              void openConversation(p.conversation_id, active.projectId);
            }
          });
        }
      })
      .catch(() => {})
      .finally(() => setLoaded(true));
    // searchParams bewusst nicht in den Deps: nur beim Mount auslesen.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Auto-Scroll nur, wenn der User (nahe) am Ende ist. Wer hochscrollt,
  // um zu lesen, wird NICHT mehr nach unten gezwingen (User-Feedback:
  // "während es druckt kann ich nicht hochscrollen").
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const isNearBottom = () => {
    const el = scrollContainerRef.current;
    if (!el) return true;
    return el.scrollHeight - el.scrollTop - el.clientHeight < 120;
  };
  const wasNearBottom = useRef(true);
  useEffect(() => {
    if (wasNearBottom.current) {
      bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
    }
  }, [entries]);
  useEffect(() => {
    const el = scrollContainerRef.current;
    if (!el) return;
    const onScroll = () => {
      wasNearBottom.current = isNearBottom();
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, []);


  // Hilfs-Setter: EINEN Eintrag (nach projectId) aktualisieren.
  const update = (projectId: string, patch: (e: ChatEntry) => ChatEntry) =>
    setEntries((prev) => prev.map((e) => (e.projectId === projectId ? patch(e) : e)));

  // Robuster Stream- & Recovery-Handler für eine Recherche:
  // 1. Hört per SSE auf Live-Events (Token, Status, Kapitel, Quellen, etc.)
  // 2. Fängt Verbindungsabbrüche / Proxy-Timeouts ab
  // 3. Gleicht bei Unterbrechung den echten Backend-Status (getResearch) ab
  // 4. Läuft die Recherche noch im Backend-Worker -> automatischer Reconnect + Polling-Fallback
  // 5. Ist die Recherche fertig (oder fehlgeschlagen) -> direktes Übernehmen ohne Reload!
  const runAgent = async (projectId: string, isInitialStart = true) => {
    if (activeRetryTimeouts.current[projectId]) {
      window.clearTimeout(activeRetryTimeouts.current[projectId]);
      delete activeRetryTimeouts.current[projectId];
    }

    if (isInitialStart) {
      update(projectId, (e) => ({
        ...e,
        running: true,
        // Resume (nach Fehler): bisherigen Teilerfolg behalten — neue Kapitel
        // werden live angehängt. Frischer/neuer Lauf: zurücksetzen.
        report: e.error ? e.report : "",
        sources: [],
        subagents: [],
        error: null,
      }));
    } else {
      update(projectId, (e) => ({ ...e, running: true }));
    }

    const controller = new AbortController();
    abortMap.current[projectId] = controller;

    let streamCompletedNormally = false;

    const handleEvent = (ev: { event: string; data: any }) => {
      if (ev.event === "token") {
        const text: string = ev.data.text;
        update(projectId, (e) => ({ ...e, report: e.report + text }));
      } else if (ev.event === "tool") {
        // Live-Status: Welche Suche/Aktion läuft gerade?
        const act = {
          tool: ev.data.tool ?? "?",
          query: ev.data.query ?? "",
          agent: ev.data.agent ?? "",
          t: Date.now(),
        };
        update(projectId, (e) => ({
          ...e,
          latestActivity: act,
          activityLog: [...e.activityLog.slice(-7), act],
        }));
      } else if (ev.event === "outline") {
        // Deep: Gliederung als TODO-Liste anzeigen
        update(projectId, (e) => ({ ...e, outline: ev.data.outline ?? null }));
      } else if (ev.event === "chapter") {
        // Deep: Kapitel-Fortschritt (start/done) in die Todo-Liste. Das
        // fertige Kapitel wird zusätzlich live in die Report-Vorschau
        // gehängt — der Teilerfolg bleibt so auch OHNE Reload sichtbar.
        const idx: number = ev.data.index;
        update(projectId, (e) => ({
          ...e,
          chapterStatus: {
            ...e.chapterStatus,
            [idx]: ev.data.status === "done" ? "done" : "running",
          },
          report:
            ev.data.status === "done" && ev.data.content
              ? (e.report ? e.report + "\n\n" : "") +
                `## ${idx}. ${ev.data.title}\n\n${ev.data.content}`
              : e.report,
        }));
      } else if (ev.event === "report_reset") {
        // Kritiker-Loop: Der Synthesizer schreibt einen KOMPLETT NEUEN
        // Report — alte Version aus der Anzeige werfen.
        update(projectId, (e) => ({ ...e, report: "" }));
      } else if (ev.event === "node") {
        const node: string = ev.data.node;
        if (ev.data.status === "start") {
          update(projectId, (e) => ({ ...e, activeAgent: node }));
        } else if (ev.data.status === "end" && ev.data.verdict === "gaps") {
          update(projectId, (e) => ({
            ...e,
            subagents: [...e.subagents, ...ev.data.gaps.map((g: string) => ({ sub_question: g, sources: null }))],
          }));
        }
      } else if (ev.event === "subagents_planned") {
        update(projectId, (e) => ({
          ...e,
          subagents: (ev.data.sub_questions ?? []).map((q: string) => ({
            sub_question: q,
            sources: null,
          })),
        }));
      } else if (ev.event === "subagent") {
        update(projectId, (e) => ({
          ...e,
          subagents: e.subagents.map((a) =>
            a.sub_question === ev.data.sub_question ? { ...a, sources: ev.data.sources } : a,
          ),
        }));
      } else if (ev.event === "sources") {
        update(projectId, (e) => ({ ...e, sources: ev.data.sources ?? [] }));
      } else if (ev.event === "usage") {
        update(projectId, (e) => ({
          ...e,
          usage: {
            total_tokens: ev.data.usage?.total_tokens ?? 0,
            llm_calls: ev.data.usage?.llm_calls ?? 0,
          },
        }));
      } else if (ev.event === "done") {
        streamCompletedNormally = true;
        update(projectId, (e) => ({ ...e, running: false, status: "done" }));
        // DB-Abgleich für finale Quellen, Report & Trace
        void getResearch(projectId)
          .then((fresh) => {
            if (!isMountedRef.current) return;
            update(projectId, (e) => ({
              ...e,
              report: fresh.report || e.report,
              sources: fresh.sources ?? e.sources,
              usage: fresh.usage ?? e.usage,
              status: "done",
              running: false,
            }));
          })
          .catch(() => {});
      } else if (ev.event === "error") {
        streamCompletedNormally = true;
        update(projectId, (e) => ({
          ...e,
          running: false,
          status: "error",
          error: ev.data.detail ?? "Lauf fehlgeschlagen",
        }));
      }
    };

    try {
      await streamSSE(`/api/v1/research/${projectId}/run`, handleEvent, controller.signal);
      streamCompletedNormally = true;
    } catch {
      // Stream abgebrochen / unterbrochen / fehlgeschlagen
    } finally {
      delete abortMap.current[projectId];
    }

    if (controller.signal.aborted || !isMountedRef.current) {
      update(projectId, (e) => ({ ...e, running: false, status: "queued" }));
      return;
    }

    // Wenn der Stream beendet wurde, ohne dass 'done'/'error' empfangen wurde
    // oder bei Verbindungsabbruch: DB-Status prüfen (Self-Healing)!
    try {
      const current = await getResearch(projectId);
      if (!isMountedRef.current) return;

      if (current.status === "done") {
        const freshEntry = entryFromProject(current);
        update(projectId, (e) => ({
          ...e,
          ...freshEntry,
          running: false,
          error: null,
        }));
        return;
      }

      if (current.status === "error") {
        update(projectId, (e) => ({
          ...e,
          running: false,
          status: "error",
          error: current.error || "Lauf fehlgeschlagen",
        }));
        return;
      }

      // Backend arbeitet noch (running / queued):
      // Automatischer Reconnect & Polling-Fallback, damit die UI nie steckenbleibt.
      if (current.status === "running" || current.status === "queued") {
        update(projectId, (e) => ({
          ...e,
          running: true,
          chapterStatus: current.chapters
            ? {
                ...e.chapterStatus,
                ...Object.fromEntries((current.chapters || []).map((_, i) => [i + 1, "done"])),
              }
            : e.chapterStatus,
        }));

        activeRetryTimeouts.current[projectId] = window.setTimeout(() => {
          delete activeRetryTimeouts.current[projectId];
          if (isMountedRef.current && !abortMap.current[projectId]) {
            void runAgent(projectId, false);
          }
        }, 1500);
      }
    } catch {
      // Wenn auch getResearch kurz fehlschlägt (z. B. Netzwerk-Glitches):
      if (!streamCompletedNormally && isMountedRef.current) {
        activeRetryTimeouts.current[projectId] = window.setTimeout(() => {
          delete activeRetryTimeouts.current[projectId];
          if (isMountedRef.current && !abortMap.current[projectId]) {
            void runAgent(projectId, false);
          }
        }, 3000);
      }
    }
  };

  // Stop: Lauf abbrechen (Backend fängt den Abbruch und setzt queued).
  const stopAgent = (projectId: string) => {
    if (activeRetryTimeouts.current[projectId]) {
      window.clearTimeout(activeRetryTimeouts.current[projectId]);
      delete activeRetryTimeouts.current[projectId];
    }
    abortMap.current[projectId]?.abort();
    update(projectId, (e) => ({ ...e, running: false, status: "queued" }));
  };

  // Chat-Verlauf als KONTEXT bauen: die letzten 3 QA-Paare, kompakt.
  // Auch FEHLGESCHLAGENE Läufe landen darin ("fehlgeschlagen: …") — genau
  // dann braucht die Nachfrage den Kontext am dringendsten.
  const buildContextSummary = (): string => {
    // NUR die sichtbare Unterhaltung (Session oder Kette) — niemals die
    // globale Historie! (Das war die Quelle versehentlicher Ketten-Links.)
    const pool = conversationChain
      ? conversationChain
          .map((id) => entries.find((e) => e.projectId === id))
          .filter((e): e is ChatEntry => Boolean(e))
      : entries.filter((e) => !e.historical);
    const relevant = pool.filter((e) => !e.running).slice(-3);
    return relevant
      .map((e) => {
        const answer = e.report
          ? e.report.slice(0, 400).replace(/\s+/g, " ")
          : e.error
            ? `(fehlgeschlagen: ${e.error.slice(0, 120)})`
            : "(kein Report)";
        return `User: ${e.question}\nFuchser: ${answer}`;
      })
      .join("\n\n");
  };


  const send = async (e: React.FormEvent) => {
    e.preventDefault();
    const question = input.trim();
    if (!question || sending) return;
    if (question.length < 3) {
      const errorEntry: ChatEntry = {
        projectId: "error-" + Date.now(),
        question,
        status: "error",
        error: "Die Forschungsfrage muss mindestens 3 Zeichen lang sein.",
        report: "",
        sources: [],
        subagents: [],
        activeAgent: null,
        running: false,
        parentId: null,
        usage: null,
        historical: false,
        outline: null,
        latestActivity: null,
    activityLog: [],
        chapterStatus: {},
              };
      setEntries((prev) => [...prev, errorEntry]);
      return;
    }
    setInput("");
    setSending(true);
    try {
      // Kontext-Kette: expliziter Follow-up-Modus gewinnt, sonst (wenn
      // Auto-Kontext an) automatisch die letzte Recherche mit Report.
      let parent = followupOf;
      setFollowupOf(null); // Modus gilt für genau EINE Frage
      // CHAT-MODELL (wie Google AI Studio): Eine Frage im geöffneten Chat
      // wird NACHRICHT dieser Unterhaltung (conversation_id) — KEIN parent-
      // Link, keine Ketten-Rekonstruktion mehr. parent nur noch explizit
      // über "Nachfragen"/Panel-Ergänzung.
      const inConversation = activeConversationId !== null;
      const contextSummary =
        (parent || inConversation) && (autoContext || inConversation)
          ? buildContextSummary()
          : undefined;
      const project = await createResearch(
        question,
        parent ?? undefined,
        contextSummary,
        activeConversationId ?? undefined,
        depth,
        citationStyle,
      );
      // WICHTIG: historical: false — frisch gestellte Fragen sind NICHT
      // historisch, sonst filtert hideHistory sie im frischen Chat weg und
      // das Fenster wirkt leer (genau das war der Bug!).
      const freshEntry = {
        ...entryFromProject({ ...project, report: null, sources: null }),
        historical: false,
      };
      setEntries((prev) => [...prev, freshEntry]);
      // In der Unterhaltungs-Ansicht wird die neue Frage Teil der Kette
      // (sonst wäre sie dort unsichtbar).
      if (conversationChain) setConversationChain((prev) => [...(prev ?? []), project.id]);
      // Angehängte Dokumente hochladen (VOR dem Agentenstart — der
      // Researcher bekommt das document_search-Tool nur, wenn Docs da sind).
      const files = pendingFiles;
      setPendingFiles([]);
      if (files.length > 0) {
        try {
          await uploadDocuments(project.id, files);
        } catch (err) {
          update(project.id, (e) => ({
            ...e,
            error: `Dokumenten-Upload fehlgeschlagen: ${err instanceof Error ? err.message : err}`,
          }));
        }
      }
      // Kein await: Der Stream läuft im Hintergrund, das Input-Feld ist
      // sofort wieder frei — man kann parallel eine zweite Frage stellen.
      void runAgent(project.id);
    } catch (err) {
      const errMsg = err instanceof Error ? err.message : String(err);
      const errorEntry: ChatEntry = {
        projectId: "error-" + Date.now(),
        question,
        status: "error",
        error: `Anfrage fehlgeschlagen: ${errMsg}`,
        report: "",
        sources: [],
        subagents: [],
        activeAgent: null,
        running: false,
        parentId: null,
        usage: null,
        historical: false,
        outline: null,
        latestActivity: null,
    activityLog: [],
        chapterStatus: {},
              };
      setEntries((prev) => [...prev, errorEntry]);
    } finally {
      setSending(false);
    }
  };

  // Was angezeigt wird: alles, oder nur die aktuelle Unterhaltung.
  const visibleEntries = conversationChain
    ? // Unterhaltungs-Ansicht: nur die Kette (Wurzel -> Nachfragen)
      conversationChain
        .map((id) => entries.find((e) => e.projectId === id))
        .filter((e): e is ChatEntry => Boolean(e))
    : hideHistory
      ? entries.filter((e) => !e.historical)
      : entries;

  // Scroll-Spy: Wenn das Sidepanel geöffnet ist und der Nutzer durch den Chat scrollt,
  // wechselt das Panel automatisch synchron zum aktuell sichtbaren Prompt.
  useEffect(() => {
    if (!panelId) return;
    const el = scrollContainerRef.current;
    if (!el) return;

    let timeoutId: ReturnType<typeof setTimeout>;
    const onScrollSpy = () => {
      clearTimeout(timeoutId);
      timeoutId = setTimeout(() => {
        const containerRect = el.getBoundingClientRect();
        const triggerY = containerRect.top + containerRect.height * 0.35;

        let bestId: string | null = null;
        let minDistance = Infinity;

        for (const entry of visibleEntries) {
          const entryEl = document.getElementById(`entry-${entry.projectId}`);
          if (!entryEl) continue;
          const rect = entryEl.getBoundingClientRect();
          if (rect.bottom > containerRect.top && rect.top < containerRect.bottom) {
            const distance = Math.abs(rect.top - triggerY);
            if (distance < minDistance) {
              minDistance = distance;
              bestId = entry.projectId;
            }
          }
        }
        if (bestId && bestId !== panelId) {
          setPanelId(bestId);
        }
      }, 120);
    };

    el.addEventListener("scroll", onScrollSpy, { passive: true });
    return () => {
      clearTimeout(timeoutId);
      el.removeEventListener("scroll", onScrollSpy);
    };
  }, [panelId, visibleEntries]);

  return (
    <div
      className={
        "mx-auto flex h-[calc(100dvh-3.5rem)] w-full max-w-[1600px] flex-col p-2 sm:p-4 transition-[padding] duration-200 " +
        // Panel offen? Dann auf Desktop rechts Platz schaffen
        (panelId ? "md:pr-[400px]" : "")
      }
    >
      {/* ---------- Kopf ---------- */}
      <div className="flex items-center justify-between border-b border-border/60 px-1 pb-2.5 sm:px-2 sm:pb-3">
        <div className="flex items-center gap-2">
          <FoxIcon size={22} className="text-primary shrink-0" />
          <div className="min-w-0">
            <h1 className="font-semibold text-sm sm:text-base leading-tight text-foreground truncate">
              Fuchser Recherche
            </h1>
            <p className="text-[11px] text-muted-foreground hidden sm:block">
              Autonome Tiefenrecherche mit verifizierten Quellen
            </p>
          </div>
        </div>
        <div className="flex items-center gap-1.5 sm:gap-2 shrink-0">
          {/* Panel-Toggle */}
          <button
            onClick={() => {
              if (panelId) {
                setPanelId(null);
              } else {
                const last = visibleEntries.at(-1);
                if (last) setPanelId(last.projectId);
              }
            }}
            className={
              "flex items-center gap-1 rounded-lg border px-2 py-1 text-xs transition-colors " +
              (panelId
                ? "border-primary/50 bg-primary/10 font-medium text-primary"
                : "border-border text-muted-foreground hover:border-primary/50 hover:text-primary")
            }
            title={panelId ? "Sidepanel schließen" : "Sidepanel öffnen (Quellen, Historie, Dokumente)"}
          >
            <PanelRightOpen className="h-3.5 w-3.5" />
            <span className="hidden xs:inline">{panelId ? "Panel ✓" : "Panel"}</span>
          </button>

          {(conversationChain || !hideHistory) && (
            <button
              onClick={() => {
                setHideHistory(true);
                setAutoContext(false);
                setConversationChain(null);
                setActiveConversationId(null);
                setPanelId(null);
                setFollowupOf(null);
                document.querySelector<HTMLTextAreaElement>("form textarea")?.focus();
              }}
              className="flex items-center gap-1 rounded-lg border px-2 py-1 text-xs text-muted-foreground transition-colors hover:border-primary/50 hover:text-primary"
              title="Leeren Chat starten — ohne Kontext und Altverlauf"
            >
              <MessageSquarePlus className="h-3.5 w-3.5" />
              <span className="hidden sm:inline">Neue Unterhaltung</span>
            </button>
          )}
        </div>
      </div>

      {/* ---------- Nachrichtenverlauf ---------- */}
      <div ref={scrollContainerRef} className="flex-1 space-y-4 sm:space-y-6 overflow-y-auto px-1 sm:px-2 py-4">
        {loaded && visibleEntries.length === 0 && (
          <div className="mt-12 text-center text-muted-foreground px-4">
            <FoxIcon size={52} className="mx-auto mb-3 text-primary/40" />
            <p className="font-medium text-foreground text-sm sm:text-base">Noch keine Recherchen gestartet</p>
            <p className="mt-1 text-xs sm:text-sm">
              Stell eine Frage — z. B. „Lohnt sich ein Balkonkraftwerk 2026?“
            </p>
          </div>
        )}

        {visibleEntries.map((entry) => (
          <div key={entry.projectId} id={`entry-${entry.projectId}`} className="space-y-2.5 sm:space-y-3">
            {/* Frage-Bubble (User, rechts) */}
            <div className="flex justify-end">
              <div className="max-w-[90%] sm:max-w-[85%] rounded-2xl rounded-br-sm bg-primary px-3.5 py-2 sm:px-4 sm:py-2.5 text-xs sm:text-sm text-primary-foreground shadow-md break-words">
                {entry.parentId && (
                  <span className="mr-1.5 inline-flex items-center gap-0.5 rounded bg-white/20 px-1.5 py-0.5 text-[10px] font-medium">
                    <Link2 className="h-2.5 w-2.5" /> Nachfrage
                  </span>
                )}
                {entry.question}
              </div>
            </div>

            {/* Fuchser-Antwort (links, volle Breite) */}
            <div className="flex gap-2 sm:gap-2.5">
              <div className="mt-0.5 shrink-0">
                <FoxIcon size={22} className="text-primary" />
              </div>
              <div className="min-w-0 flex-1">
                {/* Status-Zeile */}
                {entry.running && entry.activeAgent && (
                  <div className="mb-2 flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground font-medium">
                    <span className="flex h-5 w-5 items-center justify-center rounded-md bg-primary/10 text-primary">
                      {entry.activeAgent === "supervisor" && <Brain className="h-3.5 w-3.5 animate-pulse" />}
                      {entry.activeAgent === "researcher" && <Globe className="h-3.5 w-3.5 animate-spin text-secondary" />}
                      {entry.activeAgent === "synthesizer" && <FileText className="h-3.5 w-3.5 animate-pulse text-accent" />}
                      {entry.activeAgent === "critic" && <ShieldCheck className="h-3.5 w-3.5 animate-pulse text-chart-4" />}
                    </span>
                    <span>{AGENT_LABEL[entry.activeAgent] ?? "Agent arbeitet"} …</span>
                    <button
                      onClick={() => stopAgent(entry.projectId)}
                      className="ml-auto sm:ml-2 flex items-center gap-1 rounded-md border border-border px-1.5 py-0.5 text-[11px] transition-colors hover:border-destructive hover:text-destructive"
                      title="Recherche stoppen"
                    >
                      <Square className="h-3 w-3 fill-current" />
                      <span>Stoppen</span>
                    </button>
                  </div>
                )}

                {/* Sub-Agenten-Chips */}
                {entry.subagents.length > 0 && (
                  <div className="mb-2.5 flex flex-wrap gap-1 sm:gap-1.5">
                    {entry.subagents.map((a, i) => (
                      <span
                        key={i}
                        className={
                          "flex max-w-full items-center gap-1.5 rounded-lg border px-2 py-0.5 sm:px-2.5 sm:py-1 text-[11px] sm:text-xs transition-all " +
                          (a.sources === null
                            ? "animate-pulse border-secondary/50 bg-secondary/5 text-foreground"
                            : "border-primary/30 bg-primary/5 text-foreground font-medium")
                        }
                        title={a.sub_question}
                      >
                        {a.sources === null ? (
                          <Globe className="h-3 w-3 shrink-0 text-secondary animate-spin" />
                        ) : (
                          <Check className="h-3 w-3 shrink-0 text-primary" />
                        )}
                        <span className="truncate max-w-[200px] sm:max-w-[320px]">{a.sub_question}</span>
                        {a.sources !== null && (
                          <span className="ml-0.5 shrink-0 rounded bg-primary/15 px-1 py-0.2 text-[10px] text-primary font-mono font-bold">
                            {a.sources}
                          </span>
                        )}
                      </span>
                    ))}
                  </div>
                )}

                {/* Deep: Gliederungs-Todo-Liste mit Kapitel-Fortschritt */}
                {entry.outline && (
                  <div className="report-md mb-3 rounded-2xl border border-secondary/30 bg-secondary/5 p-3 sm:p-4">
                    <p className="mb-1 text-xs font-semibold uppercase tracking-wider text-secondary">
                      📋 Gliederung — {entry.outline.title}
                    </p>
                    {entry.outline.abstract && (
                      <p className="mb-2 text-xs italic text-muted-foreground">
                        {entry.outline.abstract}
                      </p>
                    )}
                    <ol className="space-y-1">
                      {entry.outline.chapters.map((ch, i) => {
                        const status = entry.chapterStatus[i + 1];
                        return (
                          <li key={i} className="flex items-start gap-1.5 text-xs sm:text-sm">
                            <span className="inline-flex w-6 shrink-0 items-center font-mono text-xs tabular-nums text-muted-foreground whitespace-nowrap">
                              {i + 1}.
                            </span>
                            {status === "running" ? (
                              <Globe className="mt-0.5 h-3.5 w-3.5 shrink-0 animate-spin text-secondary" />
                            ) : status === "done" ? (
                              <Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary" />
                            ) : (
                              <span className="mt-1 h-3 w-3 shrink-0 rounded-full border border-muted-foreground/40" />
                            )}
                            <span className={status === "done" ? "text-foreground" : "text-muted-foreground"}>
                              {ch.title}
                            </span>
                          </li>
                        );
                      })}
                    </ol>
                  </div>
                )}

                {/* Report (streamt live rein) */}
                {(entry.report || entry.running) && (
                  <div className="report-md rounded-2xl rounded-tl-sm border border-border/80 bg-card/90 p-3 sm:p-4 text-xs sm:text-sm leading-relaxed shadow-sm">
                    {entry.running && !entry.report && (
                      <div className="flex items-center gap-2 text-xs italic text-muted-foreground">
                        <Sparkles className="h-3.5 w-3.5 animate-pulse text-primary" />
                        <span>Erstelle Synthese-Report …</span>
                      </div>
                    )}
                    <Markdown
                      components={{
                        a: ({ href, title, children, ...props }) => {
                          const text =
                            typeof children === "string"
                              ? children
                              : Array.isArray(children)
                              ? children.join("")
                              : "";
                          const isCitation = /^\[?\d+\]?$/.test(text.trim());

                          if (isCitation) {
                            const num = text.replace(/[[\]]/g, "");
                            return (
                              <a
                                href={href}
                                target="_blank"
                                rel="noopener noreferrer"
                                title={title || href}
                                className="mx-0.5 inline-flex -translate-y-1 items-center rounded-md bg-accent/15 px-1.5 font-mono text-[10px] font-bold text-accent no-underline transition-colors hover:bg-accent hover:text-accent-foreground"
                                {...props}
                              >
                                [{num}]
                              </a>
                            );
                          }

                          return (
                            <a
                              href={href}
                              target="_blank"
                              rel="noopener noreferrer"
                              title={title}
                              className="font-medium text-primary underline underline-offset-2 hover:text-primary/80 transition-colors break-words"
                              {...props}
                            >
                              {children}
                            </a>
                          );
                        },
                      }}
                    >
                      {linkifyCitations(entry.report, entry.sources)}
                    </Markdown>
                    {entry.running && entry.report && (
                      <span className="ml-0.5 inline-block h-4 w-1.5 animate-pulse bg-primary align-middle" />
                    )}
                  </div>
                )}

                {/* Quellen-Chips */}
                {entry.sources.length > 0 && !entry.running && (
                  <div className="mt-2 flex flex-wrap items-center gap-1 sm:gap-1.5">
                    <span className="mr-0.5 text-[10px] sm:text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                      Quellen:
                    </span>
                    {entry.sources.slice(0, 8).map((s, i) => (
                      <a
                        key={i}
                        href={s.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        title={s.title}
                        className="flex items-center gap-1 rounded-full border border-accent/40 bg-accent/10 px-2 py-0.5 text-[11px] text-accent transition-colors hover:bg-accent/20"
                      >
                        <BookOpen className="h-3 w-3 shrink-0" />
                        <span className="max-w-[130px] sm:max-w-[180px] truncate">{s.title || s.url}</span>
                      </a>
                    ))}
                    {entry.sources.length > 8 && (
                      <span className="self-center text-[11px] font-medium text-muted-foreground">
                        +{entry.sources.length - 8} weitere
                      </span>
                    )}
                  </div>
                )}

                {/* Fehler */}
                {entry.error && (
                  <div className="mt-2 flex items-center gap-2 rounded-lg border border-destructive/30 bg-destructive/10 p-2 text-xs text-destructive">
                    <AlertCircle className="h-4 w-4 shrink-0" />
                    <span>{entry.error}</span>
                  </div>
                )}

                {/* Aktionen */}
                {!entry.running && (entry.report || entry.error) && (
                  <div className="mt-2 flex flex-wrap items-center gap-1.5 sm:gap-3 text-xs text-muted-foreground">
                    {entry.usage && entry.usage.total_tokens > 0 && (
                      <span
                        className="flex items-center gap-1 rounded-md bg-secondary/10 px-1.5 py-0.5 font-mono text-[10px] sm:text-[11px] font-semibold text-secondary"
                        title={`${entry.usage.total_tokens.toLocaleString("de-DE")} Token in ${entry.usage.llm_calls} LLM-Calls`}
                      >
                        <Zap className="h-3 w-3" />
                        {(entry.usage.total_tokens / 1000).toFixed(1)}k Token
                      </span>
                    )}
                    {entry.report && (
                      <>
                        <button
                          className="flex items-center gap-1 rounded-md px-1.5 py-0.5 transition-colors hover:bg-muted hover:text-primary"
                          onClick={() => {
                            setFollowupOf(entry.projectId);
                            document.querySelector<HTMLTextAreaElement>("form textarea")?.focus();
                          }}
                          title="Im Kontext dieser Recherche nachfragen"
                        >
                          <MessageCirclePlus className="h-3.5 w-3.5 text-accent" />
                          <span>Nachfragen</span>
                        </button>
                        <button
                          className="flex items-center gap-1 rounded-md px-1.5 py-0.5 transition-colors hover:bg-muted hover:text-foreground"
                          onClick={() =>
                            downloadResearchPdf(entry.projectId).catch(console.error)
                          }
                        >
                          <FileDown className="h-3.5 w-3.5 text-primary" />
                          <span>PDF</span>
                        </button>
                      </>
                    )}
                    <button
                      className="flex items-center gap-1 rounded-md px-1.5 py-0.5 transition-colors hover:bg-muted hover:text-foreground"
                      onClick={() => setPanelId(entry.projectId)}
                    >
                      <PanelRightOpen className="h-3.5 w-3.5 text-secondary" />
                      <span>Details</span>
                    </button>
                    <button
                      className="flex items-center gap-1 rounded-md px-1.5 py-0.5 transition-colors hover:bg-muted hover:text-foreground"
                      onClick={() => void runAgent(entry.projectId)}
                      title={
                        entry.error
                          ? "Abgebrochenen Lauf ab dem letzten fertigen Kapitel fortsetzen"
                          : "Noch einmal ausführen"
                      }
                    >
                      <RotateCw className="h-3.5 w-3.5" />
                      <span>{entry.error ? "Fortsetzen" : "Erneut"}</span>
                    </button>
                  </div>
                )}
              </div>
            </div>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>

      {/* ---------- Live-Statusleiste (wie DSH) ---------- */}
      {entries.some((e) => e.running) && (
        <div className="mb-2 rounded-xl border border-secondary/30 bg-secondary/5 px-2.5 py-1.5 sm:px-3 sm:py-2 text-xs">
          {(() => {
            const running = entries.filter((e) => e.running);
            const entry = running.at(-1);
            const act = entry?.latestActivity;
            const agent = entry?.activeAgent;

            // Tool-Icon
            const icon =
              act?.tool === "web_search" ? "🔎" :
              act?.tool === "document_search" ? "📄" :
              act?.tool === "plan" ? "🧠" :
              act?.tool === "write_chapter" ? "✍️" :
              act?.tool === "research_chapter" ? "📚" :
              act?.tool === "retry" ? "⏳" : "⚙️";

            // Vergangene Zeit seit letzter Aktivität
            const elapsed = act?.t ? Math.floor((Date.now() - act.t) / 1000) : 0;
            const elapsedStr =
              elapsed < 60 ? `${elapsed}s` : `${Math.floor(elapsed / 60)}m ${elapsed % 60}s`;

            return (
              <>
                {/* Aktuelle Aktivität (groß) */}
                <div className="flex items-center gap-2">
                  <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-secondary" />
                  <span className="min-w-0 flex-1 truncate font-medium text-secondary text-[11px] sm:text-xs">
                    {icon} {act?.query || act?.tool || (
                      agent === "supervisor" ? "Plant die Recherche …" :
                      agent === "researcher" ? "Researcher suchen im Web …" :
                      agent === "synthesizer" ? "Synthesizer schreibt den Report …" :
                      agent === "critic" ? "Kritiker prüft die Qualität …" :
                      agent === "outline" ? "Erstellt die Gliederung …" :
                      agent === "writer" ? "Schreibt Kapitel …" :
                      "Agent arbeitet …"
                    )}
                  </span>
                  <span className="shrink-0 font-mono text-[10px] text-muted-foreground">
                    {elapsedStr}
                  </span>
                </div>

                {/* Mini-Log: letzte 4 Aktivitäten */}
                {entry?.activityLog && entry.activityLog.length > 1 && (
                  <div className="mt-1 space-y-0.5 border-t border-secondary/20 pt-1">
                    {entry.activityLog.slice(-4, -1).reverse().map((l, i) => (
                      <div key={i} className="flex items-center gap-1.5 text-[10px] text-muted-foreground">
                        <span className="shrink-0">{l.tool === "web_search" ? "🔎" : l.tool === "retry" ? "⏳" : "•"}</span>
                        <span className="min-w-0 flex-1 truncate">{l.query}</span>
                        <span className="shrink-0 font-mono opacity-60">
                          {new Date(l.t).toLocaleTimeString("de-DE", { minute: "2-digit", second: "2-digit" })}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </>
            );
          })()}
        </div>
      )}

      {/* ---------- Angehängte Dokumente (Chips) ---------- */}
      {pendingFiles.length > 0 && (
        <div className="mb-2 flex flex-wrap gap-1 sm:gap-1.5">
          {pendingFiles.map((f) => (
            <span
              key={f.name + f.size}
              className="flex items-center gap-1.5 rounded-lg border border-secondary/40 bg-secondary/10 px-2 py-0.5 sm:px-2.5 sm:py-1 text-[11px] sm:text-xs text-secondary"
              title={`${f.name} · ${(f.size / 1024).toFixed(0)} KB — wird mit der nächsten Frage hochgeladen`}
            >
              <FileText className="h-3 w-3 shrink-0" />
              <span className="max-w-[150px] sm:max-w-[200px] truncate">{f.name}</span>
              <button
                onClick={() => setPendingFiles((prev) => prev.filter((x) => x !== f))}
                className="hover:text-foreground p-0.5"
                aria-label="Datei entfernen"
              >
                <X className="h-3 w-3" />
              </button>
            </span>
          ))}
        </div>
      )}

      {/* ---------- Follow-up-Banner (Stufe 5) ---------- */}
      {followupOf && (
        <div className="mb-2 flex items-center justify-between rounded-lg border border-accent/40 bg-accent/10 px-2.5 py-1 sm:px-3 sm:py-1.5 text-[11px] sm:text-xs text-accent">
          <span className="flex items-center gap-1.5 min-w-0 truncate">
            <Link2 className="h-3.5 w-3.5 shrink-0" />
            <span className="truncate">Nachfrage — im Kontext der vorherigen Recherche</span>
          </span>
          <button onClick={() => setFollowupOf(null)} className="hover:text-foreground shrink-0 ml-2">
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      )}

      {/* ---------- Eingabe ---------- */}
      {/* Verstecktes Datei-Auswahlfeld */}
      <input
        ref={fileInputRef}
        type="file"
        multiple
        accept=".pdf,.docx,.txt,.md,.csv"
        className="hidden"
        onChange={(e) => {
          const chosen = Array.from(e.target.files ?? []);
          if (chosen.length) setPendingFiles((prev) => [...prev, ...chosen]);
          e.target.value = "";
        }}
      />
      <form
        onSubmit={send}
        className="flex flex-col gap-1.5 rounded-2xl border border-border/80 bg-card p-2 sm:p-2.5 shadow-lg"
      >
        {/* Haupt-Eingabezeile */}
        <div className="flex items-end gap-1.5 sm:gap-2">
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void send(e as unknown as React.FormEvent);
              }
            }}
            rows={1}
            placeholder={autoContext ? "Folgefrage … (Enter = senden)" : "Neue Frage … (Enter = senden)"}
            className="max-h-[200px] min-h-[2.25rem] flex-1 resize-none overflow-y-auto bg-transparent px-2 py-1 text-sm outline-none placeholder:text-muted-foreground"
            disabled={sending}
          />
          <Button
            type="submit"
            size="sm"
            disabled={sending || input.trim().length < 3}
            className="h-8 sm:h-9 px-3 gap-1 shadow-md shrink-0 rounded-xl"
          >
            {sending ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <>
                <span className="hidden sm:inline">Senden</span>
                <Send className="h-3.5 w-3.5" />
              </>
            )}
          </Button>
        </div>

        {/* Toolbar-Leiste */}
        <div className="flex flex-wrap items-center justify-between gap-1 border-t border-border/40 pt-1.5">
          <div className="flex flex-wrap items-center gap-1 sm:gap-1.5">
            {/* Depth Mode */}
            <button
              type="button"
              onClick={() => setDepth((d) => (d === "quick" ? "deep" : "quick"))}
              className={
                "flex h-7 items-center gap-1 rounded-lg border px-2 text-[11px] font-medium transition-colors " +
                (depth === "deep"
                  ? "border-secondary/60 bg-secondary/15 text-secondary font-semibold"
                  : "border-border text-muted-foreground hover:text-foreground")
              }
              title={depth === "deep" ? "Deep-Modus aktiv: Langbericht mit Gliederung (Klick: Quick)" : "Quick-Modus (Klick: Deep)"}
            >
              {depth === "deep" ? <BookOpen className="h-3 w-3" /> : <Zap className="h-3 w-3" />}
              <span>{depth === "deep" ? "Deep" : "Quick"}</span>
            </button>

            {/* Citations Style (nur bei deep) */}
            {depth === "deep" && (
              <select
                value={citationStyle}
                onChange={(e) => setCitationStyle(e.target.value as "apa" | "ieee" | "plain")}
                className="h-7 rounded-lg border bg-background px-1.5 text-[11px] text-muted-foreground outline-none"
                title="Zitierstil für das Literaturverzeichnis"
              >
                <option value="ieee">IEEE</option>
                <option value="apa">APA 7</option>
                <option value="plain">Neutral</option>
              </select>
            )}

            {/* Document upload button */}
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              className={`flex h-7 items-center gap-1 rounded-lg border px-2 text-[11px] transition-colors ${
                pendingFiles.length > 0
                  ? "border-secondary/50 bg-secondary/10 text-secondary font-medium"
                  : "border-border text-muted-foreground hover:text-foreground"
              }`}
              title="Dokument anhängen (PDF, DOCX, TXT, MD, CSV)"
            >
              <Paperclip className="h-3 w-3" />
              <span className="hidden xs:inline">Dokument</span>
              {pendingFiles.length > 0 && (
                <span className="rounded-full bg-secondary px-1 text-[9px] text-secondary-foreground font-bold">
                  {pendingFiles.length}
                </span>
              )}
            </button>

            {/* Auto-Context toggle */}
            <button
              type="button"
              onClick={() => setAutoContext((v) => !v)}
              title={autoContext ? "Kontext AN: baut auf vorheriger Recherche auf" : "Kontext AUS: eigenständig"}
              className={
                "flex h-7 items-center gap-1 rounded-lg border px-2 text-[11px] transition-colors " +
                (autoContext
                  ? "border-accent/50 bg-accent/10 text-accent font-medium"
                  : "border-border text-muted-foreground hover:text-foreground")
              }
            >
              <Link2 className="h-3 w-3" />
              <span className="hidden xs:inline">{autoContext ? "Kontext AN" : "Kontext AUS"}</span>
            </button>
          </div>

          {/* Stopp-Button wenn etwas läuft */}
          {entries.some((e) => e.running) && (
            <button
              type="button"
              onClick={() => {
                entries.filter((e) => e.running).forEach((e) => stopAgent(e.projectId));
              }}
              className="flex h-7 items-center gap-1 rounded-lg border border-destructive/40 bg-destructive/10 px-2 text-[11px] text-destructive transition-colors hover:bg-destructive/20 ml-auto"
              title="Alle laufenden Recherchen stoppen"
            >
              <Square className="h-3 w-3 fill-current" />
              <span>Stoppen</span>
            </button>
          )}
        </div>
      </form>

      {/* ---------- Sidepanel: Modal-Overlay auf Mobile / Sidebar auf Desktop ---------- */}
      {panelId && (
        <>
          {/* Mobile Backdrop */}
          <div
            className="fixed inset-0 z-40 bg-black/60 backdrop-blur-xs md:hidden"
            onClick={() => setPanelId(null)}
            aria-label="Panel schließen"
          />

          {/* Panel Container */}
          <div className="fixed inset-y-0 right-0 z-50 flex h-full w-full max-w-[400px] md:top-14 md:h-[calc(100dvh-3.5rem)] p-2 sm:p-3 pointer-events-auto">
            <ResearchPanel
              projectId={panelId}
              chainIds={conversationChain ?? (visibleEntries.length > 0 ? visibleEntries.map((e) => e.projectId) : [panelId])}
              onClose={() => setPanelId(null)}
              onSelectProject={(id) => {
                setPanelId(id);
                document.getElementById(`entry-${id}`)?.scrollIntoView({
                  behavior: "smooth",
                  block: "start",
                });
              }}
            />
          </div>
        </>
      )}
    </div>
  );
}
