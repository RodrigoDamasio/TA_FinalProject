"use client";
import Badge from "@/components/Badge";
import Markdown from "@/components/report/Markdown";
import { agentTitle, type Board, type FeedItem, resultNote } from "@/lib/agents";
import { flagLabel, shorten } from "@/lib/format";
import { type Entry, ResearcherDataSchema, type ResultEntry, type ToolCallEntry } from "@/lib/schemas";
import { rewriteMarkers } from "@/lib/tools";
import ToolCallCard from "./ToolCallCard";

const MAX_ITEMS = 120;
const WARN_NOTICES = new Set([
  "unsourced",
  "truncated",
  "unknown_citation",
  "cap_reached",
  "possible_injection",
  "degraded",
  "forced",
  "protocol",
]);

function Decision({ entry }: { entry: Extract<Entry, { action: "DELEGATE" | "FINAL" }> }) {
  if (entry.action === "FINAL") {
    return (
      <div className="text-sm">
        <p className="flex flex-wrap items-center gap-2 font-medium">
          Supervisor → FINAL {entry.forced && <Badge tone="warn">forced</Badge>}
        </p>
        {entry.reason && <p className="text-zinc-700 dark:text-zinc-300">{entry.reason}</p>}
      </div>
    );
  }
  const targets = entry.delegations.map((d) => agentTitle(d.target_agent));
  return (
    <div className="space-y-1 text-sm">
      <p className="flex flex-wrap items-center gap-2 font-medium">
        <span>
          Supervisor → DELEGATE {targets.join(" + ")}
          {targets.length > 1 ? " (in parallel)" : ""}
        </span>
        {entry.repaired && <Badge tone="warn">repaired</Badge>}
      </p>
      {entry.reason && <p className="text-zinc-700 dark:text-zinc-300">{entry.reason}</p>}
      {entry.delegations.map((d, i) => (
        <details key={i} className="text-xs">
          <summary className="cursor-pointer text-zinc-700 dark:text-zinc-300">
            Task for {agentTitle(d.target_agent)}: {shorten(d.task, 70)}
          </summary>
          <p className="mt-1 rounded bg-zinc-100 p-2 whitespace-pre-wrap dark:bg-zinc-900">{d.task}</p>
        </details>
      ))}
    </div>
  );
}

function Result({ entry, runId }: { entry: ResultEntry; runId: string }) {
  const data = ResearcherDataSchema.safeParse(entry.data);
  const summary = data.success && data.data.summary ? data.data.summary : resultNote(entry);
  return (
    <details className="text-sm">
      <summary className="cursor-pointer">
        <span className="font-medium">{agentTitle(entry.agent)} finished:</span> {summary}
        {entry.failed && (
          <>
            {" "}
            <Badge tone="bad">failed</Badge>
          </>
        )}
        {entry.unsourced && (
          <>
            {" "}
            <Badge tone="warn">unsourced</Badge>
          </>
        )}
      </summary>
      <div className="mt-2 border-l-2 border-zinc-200 pl-3 dark:border-zinc-800">
        <Markdown text={entry.content} runId={runId} />
      </div>
    </details>
  );
}

function Item({ item, runId, rewrites }: { item: FeedItem; runId: string; rewrites: Set<number> }) {
  if (item.kind === "text") {
    return <p className="text-sm text-zinc-700 dark:text-zinc-300">{item.text}</p>;
  }
  const e = item.entry;
  switch (e.action) {
    case "DELEGATE":
    case "FINAL":
      return <Decision entry={e as Extract<Entry, { action: "DELEGATE" | "FINAL" }>} />;
    case "TOOL_CALL":
      return <ToolCallCard entry={e as ToolCallEntry} rewrote={rewrites.has(e.seq)} />;
    case "RESULT":
      return <Result entry={e as ResultEntry} runId={runId} />;
    case "FEEDBACK":
      return (
        <p className="text-sm">
          <span className="font-medium">Your feedback:</span> {"text" in e ? e.text : ""}
        </p>
      );
    case "NOTICE": {
      const flag = "flag" in e ? e.flag : null;
      return (
        <p className="flex flex-wrap items-center gap-2 text-sm">
          {flag && <Badge tone={WARN_NOTICES.has(flag) ? "warn" : "neutral"}>{flagLabel(flag)}</Badge>}
          <span>{"text" in e ? e.text : ""}</span>
        </p>
      );
    }
    default:
      return <p className="text-sm text-zinc-700 dark:text-zinc-300">{e.action}</p>;
  }
}

/** Every step, newest last: Supervisor decisions, tool-call cards (with the rewrite marker),
 * worker results (collapsible findings) and notices. */
export default function ActivityFeed({ board, runId }: { board: Board; runId: string }) {
  const items = board.feed.slice(-MAX_ITEMS);
  const entries = board.feed.flatMap((f) => (f.kind === "entry" ? [f.entry] : []));
  const rewrites = rewriteMarkers(entries);
  return (
    <section aria-label="Activity" className="space-y-2">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-sm font-semibold tracking-wide uppercase">Activity</h3>
        <span className="text-xs text-zinc-700 dark:text-zinc-300" data-testid="evidence-counter">
          Evidence: {board.evidence}
        </span>
      </div>
      {items.length === 0 ? (
        <p className="text-sm text-zinc-600 dark:text-zinc-400">No activity yet.</p>
      ) : (
        <ol aria-label="Activity feed" className="space-y-2">
          {items.map((item) => (
            <li key={item.id} data-agent={item.kind === "text" ? item.agent : item.entry.agent}>
              <Item item={item} runId={runId} rewrites={rewrites} />
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
