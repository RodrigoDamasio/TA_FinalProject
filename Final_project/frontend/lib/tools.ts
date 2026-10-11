/** Tool-call display rules: labels, the query as written by the model, evidence ranges and the
 * "rewrote the query" marker (the backend's `rewrites_after_weak` rule, RESEARCHER §3.10). */
import type { Entry, ToolCallEntry } from "./schemas";

export const SEARCH_TOOLS: ReadonlySet<string> = new Set(["search_web", "search_wikipedia", "search_documents"]);

const TOOL_INFO: Record<string, { icon: string; label: string }> = {
  search_web: { icon: "🌐", label: "Web search" },
  search_wikipedia: { icon: "📚", label: "Wikipedia search" },
  search_documents: { icon: "📄", label: "Document search" },
  read_result: { icon: "📖", label: "Read page" },
};

export function toolInfo(tool: string): { icon: string; label: string } {
  return TOOL_INFO[tool] ?? { icon: "🔧", label: tool.replaceAll("_", " ") };
}

/** What the model wrote: the query, or the evidence id for read_result. */
export function toolInput(entry: Pick<ToolCallEntry, "args">): string {
  const a = entry.args;
  for (const key of ["query", "evidence_id", "id", "url"]) {
    const v = a[key];
    if (typeof v === "string" && v.trim()) return v;
  }
  const first = Object.values(a).find((v) => typeof v === "string");
  return typeof first === "string" ? first : "";
}

/** ["E1","E2","E3","E5"] → "E1–E3, E5". Unknown labels are kept as they are. */
export function evidenceRange(ids: readonly string[]): string {
  const nums: number[] = [];
  const other: string[] = [];
  for (const id of ids) {
    const m = /^E(\d+)$/.exec(id);
    if (m) nums.push(Number(m[1]));
    else other.push(id);
  }
  const sorted = [...new Set(nums)].sort((a, b) => a - b);
  const parts: string[] = [];
  for (let i = 0; i < sorted.length; ) {
    let j = i;
    while (j + 1 < sorted.length && sorted[j + 1] === sorted[j] + 1) j++;
    parts.push(j > i ? `E${sorted[i]}–E${sorted[j]}` : `E${sorted[i]}`);
    i = j + 1;
  }
  return [...parts, ...other].join(", ");
}

const sameArgs = (a: Record<string, unknown>, b: Record<string, unknown>) => JSON.stringify(a) === JSON.stringify(b);

/** The seqs of the search calls that followed an empty or weak search in the same task with
 * a different query or a different tool. A task = the tool calls of one agent in one
 * iteration; capped calls are ignored (as in the backend rule). */
export function rewriteMarkers(entries: readonly Entry[]): Set<number> {
  const groups = new Map<string, ToolCallEntry[]>();
  for (const e of entries) {
    if (e.action !== "TOOL_CALL") continue;
    const call = e as ToolCallEntry;
    if (call.status === "capped") continue;
    const key = `${call.agent}:${call.iteration}`;
    groups.set(key, [...(groups.get(key) ?? []), call]);
  }
  const out = new Set<number>();
  for (const calls of groups.values()) {
    calls.forEach((c, i) => {
      if (!SEARCH_TOOLS.has(c.tool) || !(c.status === "empty" || c.weak)) return;
      const next = calls.slice(i + 1).find((x) => SEARCH_TOOLS.has(x.tool));
      if (next && (next.tool !== c.tool || !sameArgs(next.args, c.args))) out.add(next.seq);
    });
  }
  return out;
}
