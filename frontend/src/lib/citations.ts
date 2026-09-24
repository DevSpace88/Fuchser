// lib/citations.ts — turns [n] citations in the text into clickable links.
// ============================================================================
// The synthesizer cites inline as [1], [2], [1, 2], [1][2], [1-3] etc.
// This function safely replaces citation patterns in the Markdown with
// Markdown links [n](url "title"), without damaging code blocks (`...`,
// ```...```) or existing links [text](url).

import type { Source } from "./research";

export function linkifyCitations(
  markdownText: string,
  sources: Source[] | undefined | null
): string {
  if (!markdownText || !sources || sources.length === 0) {
    return markdownText;
  }

  // 1) Split the text into code blocks / inline code and normal text,
  // so that code contents (e.g. array accesses like arr[1]) stay unchanged.
  const parts = markdownText.split(/(```[\s\S]*?```|`[^`\n]*`)/g);

  return parts
    .map((part, index) => {
      // Odd indices are code blocks / inline code -> return 1:1
      if (index % 2 === 1) {
        return part;
      }

      // 2) Match citations like [1], [1, 2], [1-3] and CHAINS [1][2][3].
      // (?!\() only blocks already-linked Markdown links [foo](bar) —
      // a following "[" is the NEXT citation and must NOT be blocked
      // (the old lookahead (?![\(\[]) made only the LAST number linkable in
      // chains like [25][28] — which is why most numbers were dead).
      return part.replace(
        /\[([0-9\s,\-–]+)\](?!\()/g,
        (fullMatch, inner: string) => {
          // Split the content by commas / spaces
          const rawItems = inner.split(/[,;\s]+/).filter(Boolean);
          if (rawItems.length === 0) return fullMatch;

          // Check that all items really are pure numbers or ranges (e.g. 1-3)
          const allNumeric = rawItems.every((item) => /^\d+(?:[-–]\d+)?$/.test(item));
          if (!allNumeric) return fullMatch;

          const links: string[] = [];

          for (const item of rawItems) {
            // Range like "1-3"
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

            // Single number like "1"
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
