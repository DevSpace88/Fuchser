// Test harness: runs the REAL linkifyCitations logic (as a plain-JS copy)
// against a REAL report from the DB and counts linked/unlinked.
import { readFileSync } from "fs";

const d = JSON.parse(readFileSync("/tmp/project.json", "utf8"));
const report = d.report || "";
const sources = d.sources || [];

// === 1:1 copy of the regex logic from frontend/src/lib/citations.ts (with fix) ===
function linkifyCitations(markdownText, sources) {
  if (!markdownText || !sources || sources.length === 0) return markdownText;
  const parts = markdownText.split(/(```[\s\S]*?```|`[^`\n]*`)/g);
  return parts
    .map((part, index) => {
      if (index % 2 === 1) return part;
      return part.replace(
        /\[([0-9\s,\-–]+)\](?!\()/g,
        (fullMatch, inner) => {
          const rawItems = inner.split(/[,;\s]+/).filter(Boolean);
          if (rawItems.length === 0) return fullMatch;
          const allNumeric = rawItems.every((item) => /^\d+(?:[-–]\d+)?$/.test(item));
          if (!allNumeric) return fullMatch;
          const links = [];
          for (const item of rawItems) {
            const rangeMatch = item.match(/^(\d+)[-–](\d+)$/);
            if (rangeMatch) {
              const start = parseInt(rangeMatch[1], 10);
              const end = parseInt(rangeMatch[2], 10);
              if (start <= end && end - start <= 15) {
                for (let n = start; n <= end; n++) {
                  const src = sources[n - 1];
                  if (src?.url) links.push(`[${n}](${src.url})`);
                  else links.push(`[${n}]`);
                }
                continue;
              }
            }
            const num = parseInt(item, 10);
            if (!isNaN(num) && num > 0) {
              const src = sources[num - 1];
              if (src?.url) links.push(`[${num}](${src.url})`);
              else links.push(`[${num}]`);
            } else links.push(item);
          }
          if (links.length === 0) return fullMatch;
          return links.join(" ");
        },
      );
    })
    .join("");
}

const result = linkifyCitations(report, sources);

// Occurrences of [n] patterns in the original vs. linked ones in the result
const citations = report.match(/\[[0-9][0-9\s,\-–]*\]/g) || [];
const linked = (result.match(/\[\d+\]\(/g) || []).length;
const dead = (result.match(/\[\d+\](?!\()/g) || []).length;

console.log("=== ECHTER LAUF:", d.question.slice(0, 50), "===");
console.log("Quellen:", sources.length);
console.log("Zitat-Vorkommen im Report-Text:", citations.length);
console.log("VERLINKT:", linked, "| UNVERLINKT:", dead);

// Show the UNLINKED ones with context (what kind of patterns are they?)
const ctx = [];
let idx = 0;
for (const m of result.matchAll(/\[([\d\s,\-–]{1,15})\](?!\()/g)) {
  if (idx++ > 8) break;
  ctx.push(JSON.stringify(m[0]));
}
console.log("Unverlinkte Muster (Beispiele):", ctx);

// Distribution: how many citation numbers > source count (hallucinated)?
const nums = [...new Set((report.match(/\[\d+\]|\[\d+\]\[\d+\]/g) || []).join(",").match(/\d+/g))].map(Number);
const tooHigh = nums.filter((n) => n > sources.length);
console.log("Zitierte Nummern über Quellenlänge (" + sources.length + "):", tooHigh);
