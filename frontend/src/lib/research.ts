// ============================================================================
// lib/research.ts — types + API helpers for research projects (PLAN.md level 1)
// ============================================================================
//
// The types mirror backend/app/schemas/research.py (ResearchProjectOut).
// All calls go through apiFetch (JWT + auto-refresh included).

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
  title: string | null; // display name (renameable, drives the PDF title)
  conversation_id: string | null; // associated conversation
  depth: "quick" | "deep";
  outline: {
    title: string;
    abstract: string;
    chapters: { title: string; focus: string }[];
  } | null; // deep outline (survives reloads)
  status: "queued" | "running" | "done" | "error";
  report: string | null;
  error: string | null;
  sources: Source[] | null;
  chapters: { title: string; content: string }[] | null; // finished deep chapters
  usage: Usage | null;
  parent_id: string | null; // set for follow-ups
  trace: object[] | null; // execution history (side panel timeline)
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
// Conversations (Google AI Studio model): the history lists CHATS.
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
// PDF download: a real file download (blob), NOT window.print().
// An <a href> is not enough because the Authorization header MUST come along —
// so: fetch → blob → object URL → click on an invisible <a>.
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
      /* no JSON */
    }
    throw new Error(detail);
  }

  // Take the file name from the Content-Disposition header (the server
  // delivers it RFC-compliant, even with umlauts via filename*=UTF-8''…).
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
// Document upload (PDF/DOCX/TXT/MD/CSV): multipart with an auth header.
// apiFetch cannot do multipart (it sets JSON headers) -> our own path with
// the same 401->refresh->retry pattern.
// ----------------------------------------------------------------------------
export interface DocumentInfo {
  id: string;
  project_id: string; // owner project (chain view: deleting needs it)
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
      body: form, // the browser sets Content-Type itself (boundary!)
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
      /* no JSON */
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
