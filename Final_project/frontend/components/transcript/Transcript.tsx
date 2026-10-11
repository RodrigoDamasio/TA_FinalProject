import ToolCallCard from "@/components/activity/ToolCallCard";
import Badge from "@/components/Badge";
import Markdown from "@/components/report/Markdown";
import { agentKey, agentTitle } from "@/lib/agents";
import { flagLabel, formatMs } from "@/lib/format";
import type { DelegateEntry, Entry, FinalEntry, ResearchView, ResultEntry, ToolCallEntry } from "@/lib/schemas";
import { rewriteMarkers } from "@/lib/tools";
import { FactCheckTable, ReviewView } from "./WorkerData";

function Cost({ entry }: { entry: Entry }) {
  if (!entry.llm_calls && !entry.cached) return null;
  const how = entry.cached ? "cached" : `${entry.llm_calls} call${entry.llm_calls === 1 ? "" : "s"}`;
  return (
    <span className="text-xs font-normal text-zinc-600 dark:text-zinc-400">
      {how}
      {typeof entry.duration_ms === "number" ? ` · ${formatMs(entry.duration_ms)}` : ""}
    </span>
  );
}

function DecisionEntry({ entry, limit }: { entry: DelegateEntry | FinalEntry; limit: number }) {
  const forced = entry.action === "FINAL" && entry.forced;
  return (
    <div className="space-y-1">
      <p className="flex flex-wrap items-center gap-2 text-sm font-medium">
        <span>Supervisor · {forced ? "forced decision" : `decision ${entry.iteration} of ${limit}`}</span>
        {entry.action === "DELEGATE" && entry.repaired && <Badge tone="warn">reply repaired</Badge>}
        {forced && <Badge tone="warn">forced</Badge>}
        <Cost entry={entry} />
      </p>
      {entry.reason && <p className="text-sm text-zinc-700 dark:text-zinc-300">REASON: {entry.reason}</p>}
      {entry.action === "FINAL" ? (
        <p className="font-mono text-xs">FINAL (the report is shown above)</p>
      ) : (
        <ul className="space-y-1">
          {entry.delegations.map((x, i) => (
            <li key={i} className="rounded bg-zinc-100 p-2 font-mono text-xs break-words dark:bg-zinc-900">
              <span className="font-semibold">DELEGATE: {agentTitle(x.target_agent)}</span>
              <span className="block whitespace-pre-wrap">TASK: {x.task}</span>
            </li>
          ))}
          {entry.delegations.length > 1 && <li className="text-xs">These workers ran in parallel.</li>}
        </ul>
      )}
    </div>
  );
}

function ResultView({ entry, run, open }: { entry: ResultEntry; run: ResearchView; open: boolean }) {
  const key = agentKey(entry.agent);
  const sources = run.report?.sources ?? [];
  return (
    <details open={open}>
      <summary className="flex cursor-pointer flex-wrap items-center gap-2 text-sm font-medium">
        <span>Result from {agentTitle(entry.agent)}</span>
        {entry.failed && <Badge tone="bad">failed</Badge>}
        {entry.unsourced && <Badge tone="warn">unsourced</Badge>}
        {entry.evidence.length > 0 && <Badge>cites {entry.evidence.join(" ")}</Badge>}
        <Cost entry={entry} />
      </summary>
      <div className="mt-2 border-l-2 border-zinc-200 pl-3 text-sm dark:border-zinc-800">
        {key === "reviewer" && !entry.failed && <ReviewView data={entry.data} />}
        {key === "fact_checker" && !entry.failed && <FactCheckTable data={entry.data} />}
        {!(key === "reviewer" || key === "fact_checker") || entry.failed ? (
          <Markdown text={entry.content} sources={sources} runId={run.run_id} />
        ) : null}
      </div>
    </details>
  );
}

/** The conversation history of one run, in order: decisions, tool calls, worker results,
 * notices and human feedback. Unknown actions are shown as a generic line. */
export default function Transcript({ run }: { run: ResearchView }) {
  const history = run.conversation_history;
  const lastResult = [...history].reverse().find((e) => e.action === "RESULT")?.seq;
  const rewrites = rewriteMarkers(history);
  if (history.length === 0) {
    return <p className="text-sm text-zinc-600 dark:text-zinc-400">Nothing yet: the Supervisor is about to decide.</p>;
  }
  return (
    <ol aria-label="Transcript" className="space-y-3">
      {history.map((entry) => (
        <li key={entry.seq} className="min-w-0 rounded-md border border-zinc-200 p-3 dark:border-zinc-800">
          {(entry.action === "DELEGATE" || entry.action === "FINAL") && (
            <DecisionEntry entry={entry as DelegateEntry | FinalEntry} limit={run.metadata.iteration_limit} />
          )}
          {entry.action === "TOOL_CALL" && (
            <ToolCallCard entry={entry as ToolCallEntry} rewrote={rewrites.has(entry.seq)} />
          )}
          {entry.action === "RESULT" && (
            <ResultView entry={entry as ResultEntry} run={run} open={entry.seq === lastResult} />
          )}
          {entry.action === "FEEDBACK" && (
            <p className="text-sm">
              <span className="font-medium">Your feedback:</span> {"text" in entry ? entry.text : ""}
            </p>
          )}
          {entry.action === "NOTICE" && (
            <p className="text-sm text-zinc-700 dark:text-zinc-300">
              <span className="font-medium">Note{"flag" in entry && entry.flag ? ` (${flagLabel(entry.flag)})` : ""}:</span>{" "}
              {"text" in entry ? entry.text : ""}
            </p>
          )}
          {!["DELEGATE", "FINAL", "TOOL_CALL", "RESULT", "FEEDBACK", "NOTICE"].includes(entry.action) && (
            <p className="text-sm text-zinc-700 dark:text-zinc-300">
              <span className="font-medium">
                {agentTitle(entry.agent)} · {entry.action}
              </span>
              {"text" in entry && entry.text ? `: ${entry.text}` : ""}
            </p>
          )}
        </li>
      ))}
    </ol>
  );
}
