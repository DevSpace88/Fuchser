// ============================================================================
// lib/sse.ts — Server-Sent-Events über fetch-Streaming
// ============================================================================
//
// WARUM nicht das eingebaute EventSource?
//   * EventSource kann NUR GET — unser Run-Endpunkt ist aber POST.
//   * EventSource kann keine Authorization-Header setzen.
//   Also: fetch + ReadableStream und das SSE-Textformat selbst parsen.
//
// Das Protokoll (siehe PLAN.md §4): Frames der Form
//   event: token\n
//   data: {"text": "..."}\n
//   \n
// Getrennt durch LEERZEILEN (\n\n). Wir puffern Chunks und schneiden Frames heraus.

import { doRefresh, getFreshAccessToken } from "./api";

export interface SSEEvent {
  event: string;
  data: any;
}

export async function streamSSE(
  path: string,
  onEvent: (ev: SSEEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const run = (accessToken: string | null) =>
    fetch(path, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
        ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
      },
      signal, // Stop-Button: fetch abbrechen beendet auch den Server-Stream
    });

  let resp = await run(await getFreshAccessToken());

  // Gleiches 401→Refresh→Retry-Muster wie apiFetch (einmalig).
  if (resp.status === 401) {
    const newAccess = await doRefresh();
    if (newAccess) resp = await run(newAccess);
  }
  if (!resp.ok || !resp.body) {
    throw new Error(`Stream fehlgeschlagen (HTTP ${resp.status})`);
  }

  // Text-Strom lesen und Frames an der Leerzeile splitten.
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  const handleFrame = (frame: string) => {
    let event = "message";
    const dataLines: string[] = [];
    for (const line of frame.split("\n")) {
      if (line.startsWith("event: ")) event = line.slice(7).trim();
      else if (line.startsWith("data: ")) dataLines.push(line.slice(6));
    }
    if (dataLines.length === 0) return;
    try {
      onEvent({ event, data: JSON.parse(dataLines.join("\n")) });
    } catch {
      onEvent({ event, data: dataLines.join("\n") }); // Fallback: Roh-Text
    }
  };

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    for (;;) {
      const idx = buffer.indexOf("\n\n");
      if (idx === -1) break;
      const frame = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      if (frame.trim()) handleFrame(frame);
    }
  }
  if (buffer.trim()) handleFrame(buffer); // letzter Frame ohne Leerzeile
}
