/** Turns [E<n>] citations into Markdown links to citation chips, outside code only.
 * Unknown ids stay plain text; "[E1, E2]" is split into "[E1][E2]" first. */

const GROUPED = /\[(E\d{1,4}(?:\s*,\s*E\d{1,4})+)\]/g;
const SINGLE = /\[(E\d{1,4})\](?!\()/g; // not "[E1](url)", which is already a link
const FENCE = /^\s*(```|~~~)/;
export const CITE_PREFIX = "#cite-";

export function linkCitations(markdown: string, known: ReadonlySet<string>): string {
  let inFence = false;
  return markdown
    .split("\n")
    .map((line) => {
      if (FENCE.test(line)) {
        inFence = !inFence;
        return line;
      }
      if (inFence) return line;
      return line
        .split(/(`[^`]*`)/)
        .map((part, i) => {
          if (i % 2 === 1) return part; // inline code
          return part
            .replace(GROUPED, (_, list: string) =>
              list
                .split(",")
                .map((n) => `[${n.trim()}]`)
                .join(""),
            )
            .replace(SINGLE, (match, id: string) => (known.has(id) ? `[${id}](${CITE_PREFIX}${id})` : match));
        })
        .join("");
    })
    .join("\n");
}

/** The evidence ids cited in a text, in order of first citation (also inside groups). */
export function citedIds(markdown: string): string[] {
  const seen: string[] = [];
  for (const m of markdown.matchAll(/\[(E\d{1,4}(?:\s*,\s*E\d{1,4})*)\]/g)) {
    for (const id of m[1].split(",").map((s) => s.trim())) if (!seen.includes(id)) seen.push(id);
  }
  return seen;
}

/** Only http(s) links and in-page anchors survive; anything else becomes "" (no link). */
export function safeUrl(url: string): string {
  if (url.startsWith("#")) return url;
  try {
    const parsed = new URL(url);
    return parsed.protocol === "http:" || parsed.protocol === "https:" ? url : "";
  } catch {
    return "";
  }
}
