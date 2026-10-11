/** The structured report: references in order of first citation, source locations and the
 * Markdown export (built from `report`, never re-parsed from final_output). */
import { citedIds } from "./citations";
import { domain } from "./format";
import type { Report, ReportSource } from "./schemas";

/** Sources in order of first citation (summary, then sections); uncited ones last. */
export function orderedSources(report: Report): ReportSource[] {
  const text = [report.summary, ...report.sections.map((s) => s.content)].join("\n");
  const order = citedIds(text);
  const rank = (id: string) => {
    const i = order.indexOf(id);
    return i === -1 ? Number.MAX_SAFE_INTEGER : i;
  };
  return [...report.sources].sort((a, b) => rank(a.id) - rank(b.id));
}

/** "en.wikipedia.org", "handbook.pdf, p. 4" or "handbook.pdf". */
export function sourceLocation(s: ReportSource): string {
  if (s.document_name) return s.page !== null ? `${s.document_name}, p. ${s.page}` : s.document_name;
  return s.url ? domain(s.url) : "";
}

export function reportToMarkdown(report: Report): string {
  const parts = [`# ${report.title}`, "", report.summary, ""];
  for (const s of report.sections) parts.push(`## ${s.heading}`, "", s.content, "");
  if (report.references.length) {
    parts.push("## References", "");
    for (const r of report.references) parts.push(`- ${r}`);
    parts.push("");
  }
  return parts.join("\n");
}

export const referenceAnchor = (runId: string, id: string) => `${runId}-ref-${id}`;
