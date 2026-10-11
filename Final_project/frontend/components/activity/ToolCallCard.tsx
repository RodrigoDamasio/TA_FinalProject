import Badge, { type Tone } from "@/components/Badge";
import { formatMs, plural } from "@/lib/format";
import type { ToolCallEntry } from "@/lib/schemas";
import { evidenceRange, toolInfo, toolInput } from "@/lib/tools";

const STATUS_TONE: Record<string, Tone> = {
  ok: "good",
  empty: "warn",
  error: "bad",
  refused: "bad",
  capped: "warn",
};

/** One Researcher tool call as the model wrote it: tool, query (or evidence id), provider,
 * results, evidence range, status, and the weak / repeat / fallback / capped flags. The core
 * visual of the agent design (FRONTEND_PLAN §3.3). All values are rendered as text. */
export default function ToolCallCard({ entry, rewrote }: { entry: ToolCallEntry; rewrote: boolean }) {
  const info = toolInfo(entry.tool);
  const input = toolInput(entry);
  const isRead = entry.tool === "read_result";
  const range = evidenceRange(entry.evidence);
  return (
    <div
      data-testid="tool-call"
      data-tool={entry.tool}
      data-status={entry.status}
      aria-label={`Tool call: ${entry.tool}, ${entry.status}`}
      role="group"
      className="space-y-1 rounded-md border border-zinc-300 bg-zinc-50 p-2 text-sm dark:border-zinc-700 dark:bg-zinc-900"
    >
      {rewrote && (
        <p className="text-xs font-medium text-orange-800 dark:text-orange-300">
          <span aria-hidden>↻ </span>rewrote the query
        </p>
      )}
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span aria-hidden>{info.icon}</span>
        <span className="font-mono text-xs font-semibold">{entry.tool}</span>
        <Badge tone={STATUS_TONE[entry.status] ?? "neutral"}>{entry.status}</Badge>
        {entry.weak && (
          <Badge tone="warn" title="The results matched the query poorly">
            weak
          </Badge>
        )}
        {entry.repeat && (
          <Badge tone="warn" title="The same call was made before in this task">
            repeat
          </Badge>
        )}
        {entry.fallback && (
          <Badge tone="accent" title="The tool's provider failed; another source answered">
            fallback → {entry.fallback}
          </Badge>
        )}
        {entry.status === "capped" && <span className="text-xs text-zinc-700 dark:text-zinc-300">not run: tool-call cap reached</span>}
      </div>
      {input && (
        <p className="break-words">
          <span className="sr-only">{isRead ? "Evidence: " : "Query: "}</span>
          {isRead ? <span className="font-mono">{input}</span> : <q className="italic">{input}</q>}
        </p>
      )}
      <p className="flex flex-wrap gap-x-3 text-xs text-zinc-700 dark:text-zinc-300">
        {entry.provider && <span>via {entry.provider}</span>}
        {entry.status !== "capped" && <span>{plural(entry.results, "result")}</span>}
        {range && <span>evidence {range}</span>}
        {typeof entry.duration_ms === "number" && entry.duration_ms > 0 && <span>{formatMs(entry.duration_ms)}</span>}
      </p>
      {entry.error && <p className="text-xs text-red-800 dark:text-red-300">{entry.error}</p>}
    </div>
  );
}
