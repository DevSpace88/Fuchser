// ============================================================================
// lib/sse.ts — server-sent events via fetch streaming
// ============================================================================
//
// WHY not the built-in EventSource?
//   * EventSource can only do GET — but our run endpoint is POST.
//   * EventSource cannot set Authorization headers.
//   So: fetch + ReadableStream, and we parse the SSE text format ourselves.
//
// The protocol (see PLAN.md §4): frames of the form
//   event: token\n
//   data: {"text": "..."}\n
//   \n
// Separated by BLANK LINES (\n\n). We buffer chunks and cut out frames.

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
      signal, // stop button: aborting fetch also ends the server stream
    });

  let resp = await run(await getFreshAccessToken());

  // Same 401→refresh→retry pattern as apiFetch (once).
  if (resp.status === 401) {
    const newAccess = await doRefresh();
    if (newAccess) resp = await run(newAccess);
  }
  if (!resp.ok || !resp.body) {
    throw new Error(`Stream fehlgeschlagen (HTTP ${resp.status})`);
  }

  // Read the text stream and split frames at the blank line.
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
      onEvent({ event, data: dataLines.join("\n") }); // fallback: raw text
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
  if (buffer.trim()) handleFrame(buffer); // last frame without a blank line
}
