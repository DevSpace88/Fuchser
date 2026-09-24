// lib/citations.ts — Wandelt [n]-Zitate im Text in klickbare Links um.
// ============================================================================
// Der Synthesizer zitiert im Fließtext als [1], [2], [1, 2], [1][2], [1-3] etc.
// Diese Funktion ersetzt Zitat-Muster im Markdown sicher durch Markdown-Links
// [n](url "title"), ohne Codeblöcke (`...`, ```...```) oder bestehende Links
// [text](url) zu beschädigen.

import type { Source } from "./research";

export function linkifyCitations(
  markdownText: string,
  sources: Source[] | undefined | null
): string {
  if (!markdownText || !sources || sources.length === 0) {
    return markdownText;
  }

  // 1) Text in Code-Blöcke / Inline-Code und normalen Text aufteilen,
  // damit Code-Inhalte (z. B. Array-Zugriffe wie arr[1]) unverändert bleiben.
  const parts = markdownText.split(/(```[\s\S]*?```|`[^`\n]*`)/g);

  return parts
    .map((part, index) => {
      // Ungerade Indizes sind Code-Blöcke / Inline-Code -> 1:1 zurückgeben
      if (index % 2 === 1) {
        return part;
      }

      // 2) Zitate wie [1], [1, 2], [1-3] und KETTEN [1][2][3] matchen.
      // (?!\() blockt nur bereits verlinkte Markdown-Links [foo](bar) —
      // ein folgendes "[" ist die NÄCHSTE Zitation und darf NICHT blocken
      // (der alte Lookahead (?![\(\[]) ließ in Ketten wie [25][28] nur die
      // LETZTE Zahl verlinken — deshalb waren die meisten Zahlen tot).
      return part.replace(
        /\[([0-9\s,\-–]+)\](?!\()/g,
        (fullMatch, inner: string) => {
          // Zerlege den Inhalt nach Kommas / Leerzeichen
          const rawItems = inner.split(/[,;\s]+/).filter(Boolean);
          if (rawItems.length === 0) return fullMatch;

          // Prüfen, ob wirklich alle Items reine Zahlen oder Bereiche (z. B. 1-3) sind
          const allNumeric = rawItems.every((item) => /^\d+(?:[-–]\d+)?$/.test(item));
          if (!allNumeric) return fullMatch;

          const links: string[] = [];

          for (const item of rawItems) {
            // Bereich wie "1-3"
            const rangeMatch = item.match(/^(\d+)[-–](\d+)$/);
            if (rangeMatch) {
              const start = parseInt(rangeMatch[1], 10);
              const end = parseInt(rangeMatch[2], 10);
              if (start <= end && end - start <= 15) {
                for (let n = start; n <= end; n++) {
                  const src = sources[n - 1];
                  if (src?.url) {
                    const safeTitle = (src.title || src.url).replace(/["\\]/g, "");
                    links.push(`[${n}](${src.url} "${safeTitle}")`);
                  } else {
                    links.push(`[${n}]`);
                  }
                }
                continue;
              }
            }

            // Einzelne Zahl wie "1"
            const num = parseInt(item, 10);
            if (!isNaN(num) && num > 0) {
              const src = sources[num - 1];
              if (src?.url) {
                const safeTitle = (src.title || src.url).replace(/["\\]/g, "");
                links.push(`[${num}](${src.url} "${safeTitle}")`);
              } else {
                links.push(`[${num}]`);
              }
            } else {
              links.push(item);
            }
          }

          if (links.length === 0) return fullMatch;
          return links.join(" ");
        }
      );
    })
    .join("");
}
