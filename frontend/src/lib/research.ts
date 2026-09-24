// ============================================================================
// lib/research.ts — Typen + API-Helfer für Recherche-Projekte (PLAN.md Stufe 1)
// ============================================================================
//
// Die Typen spiegeln backend/app/schemas/research.py (ResearchProjectOut).
// Alle Aufrufe laufen durch apiFetch (JWT + Auto-Refresh inklusive).

import { apiFetch } from "./api";

export interface Source {
  title: string;
  url: string;
  snippet: string;
}

export interface Usage {
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  llm_calls: number;
}

export interface ResearchProject {
  id: string;
  question: string;
  title: string | null; // Anzeigename (umbenennbar, steuert PDF-Titel)
  conversation_id: string | null; // zugehörige Unterhaltung
  depth: "quick" | "deep";
  outline: {
    title: string;
    abstract: string;
    chapters: { title: string; focus: string }[];
  } | null; // Deep-Gliederung (überlebt Reloads)
  status: "queued" | "running" | "done" | "error";
  report: string | null;
  error: string | null;
  sources: Source[] | null;
  chapters: { title: string; content: string }[] | null; // fertige Deep-Kapitel
  usage: Usage | null;
  parent_id: string | null; // gesetzt bei Follow-ups
  trace: object[] | null; // Ausführungs-Historie (Sidepanel-Timeline)
  thread_id: string;
  created_at: string;
  updated_at: string;
}

export type ResearchDepth = "quick" | "deep";

export function createResearch(
  question: string,
  followupOf?: string | null,
  contextSummary?: string | null,
  conversationId?: string | null,
  depth: ResearchDepth = "quick",
  citationStyle: "apa" | "ieee" | "plain" = "ieee",
): Promise<ResearchProject> {
  return apiFetch<ResearchProject>("/api/v1/research", {
    method: "POST",
    body: JSON.stringify({
      question,
      followup_of: followupOf || null,
      context_summary: contextSummary || null,
      conversation_id: conversationId || null,
      depth,
      citation_style: citationStyle,
    }),
  });
}

// ----------------------------------------------------------------------------
// Unterhaltungen (Google-AI-Studio-Modell): Historie listet CHATS.
// ----------------------------------------------------------------------------
export interface Conversation {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  message_count: number;
  total_tokens: number;
  last_question: string | null;
}

export interface ConversationDetail extends Conversation {
  messages: ResearchProject[];
}

export function listConversations(): Promise<Conversation[]> {
  return apiFetch<Conversation[]>("/api/v1/conversations");
}

export function getConversation(id: string): Promise<ConversationDetail> {
  return apiFetch<ConversationDetail>(`/api/v1/conversations/${id}`);
}

export function renameConversation(id: string, title: string): Promise<Conversation> {
  return apiFetch<Conversation>(`/api/v1/conversations/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ title }),
  });
}

export interface AdminResearchEntry {
  id: string;
  question: string;
  status: string;
  user_email: string;
  usage: Usage | null;
  parent_id: string | null;
  created_at: string;
}

export function listAllResearch(): Promise<AdminResearchEntry[]> {
  return apiFetch<AdminResearchEntry[]>("/api/v1/research/admin/all");
}

export function listResearch(): Promise<ResearchProject[]> {
  return apiFetch<ResearchProject[]>("/api/v1/research");
}

export function getResearch(id: string): Promise<ResearchProject> {
  return apiFetch<ResearchProject>(`/api/v1/research/${id}`);
}

export function renameResearch(id: string, title: string): Promise<ResearchProject> {
  return apiFetch<ResearchProject>(`/api/v1/research/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ title }),
  });
}

export function deleteResearch(id: string): Promise<void> {
  return apiFetch<void>(`/api/v1/research/${id}`, { method: "DELETE" });
}

// ----------------------------------------------------------------------------
// PDF-Download: Echter Datei-Download (Blob), KEIN window.print().
// Ein <a href> reicht nicht, weil der Authorization-Header mit MUSS —
// also fetch → Blob → ObjectURL → Klick auf unsichtbaren <a>.
// ----------------------------------------------------------------------------
import { doRefresh, getFreshAccessToken } from "./api";

export async function downloadResearchPdf(id: string): Promise<void> {
  const run = (accessToken: string | null) =>
    fetch(`/api/v1/research/${id}/pdf`, {
      headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {},
    });

  let resp = await run(await getFreshAccessToken());
  if (resp.status === 401) {
    const newAccess = await doRefresh();
    if (newAccess) resp = await run(newAccess);
  }
  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`;
    try {
      const body = await resp.json();
      if (body?.detail) detail = body.detail;
    } catch {
      /* kein JSON */
    }
    throw new Error(detail);
  }

  // Dateiname aus dem Content-Disposition-Header nehmen (Server liefert ihn
  // RFC-konform, auch mit Umlauten via filename*=UTF-8''…).
  const disposition = resp.headers.get("content-disposition") ?? "";
  const utf8Match = disposition.match(/filename\*=UTF-8''([^;]+)/);
  const asciiMatch = disposition.match(/filename="?([^";]+)"?/);
  const filename = utf8Match
    ? decodeURIComponent(utf8Match[1])
    : asciiMatch?.[1] ?? "deep-research-report.pdf";

  const blob = await resp.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}


// ----------------------------------------------------------------------------
// Dokumenten-Upload (PDF/DOCX/TXT/MD/CSV): multipart mit Auth-Header.
// apiFetch kann kein multipart (setzt JSON-Header) -> eigener Weg mit
// demselben 401->Refresh->Retry-Muster.
// ----------------------------------------------------------------------------
export interface DocumentInfo {
  id: string;
  project_id: string; // Eigentümer-Projekt (Ketten-Sicht: Löschen braucht es)
  filename: string;
  mime_type: string;
  size_bytes: number;
  char_count: number;
}

export async function uploadDocuments(
  projectId: string,
  files: File[],
): Promise<DocumentInfo[]> {
  const run = (accessToken: string | null) => {
    const form = new FormData();
    files.forEach((f) => form.append("files", f));
    return fetch(`/api/v1/research/${projectId}/documents`, {
      method: "POST",
      headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {},
      body: form, // Content-Type setzt der Browser selbst (boundary!)
    });
  };

  let resp = await run(await getFreshAccessToken());
  if (resp.status === 401) {
    const newAccess = await doRefresh();
    if (newAccess) resp = await run(newAccess);
  }
  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`;
    try {
      const body = await resp.json();
      if (body?.detail) detail = body.detail;
    } catch {
      /* kein JSON */
    }
    throw new Error(detail);
  }
  return resp.json();
}

export function deleteConversation(id: string): Promise<void> {
  return apiFetch<void>(`/api/v1/conversations/${id}`, { method: "DELETE" });
}

export function resumeResearch(
  projectId: string,
  outline: object,
): Promise<Response> {
  return apiFetch<Response>(`/api/v1/research/${projectId}/resume`, {
    method: "POST",
    body: JSON.stringify({ outline }),
  });
}
