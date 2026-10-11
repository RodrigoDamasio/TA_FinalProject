"use client";
import ActivityFeed from "@/components/activity/ActivityFeed";
import AgentBoard from "@/components/activity/AgentBoard";
import ApprovalPanel from "@/components/ApprovalPanel";
import Badge from "@/components/Badge";
import Report from "@/components/report/Report";
import RunDetails from "@/components/run/RunDetails";
import { agentTitle, type Board, boardFromRun, working } from "@/lib/agents";
import { DEPTH_PROFILES, plural, SOURCE_LABELS } from "@/lib/format";
import type { Agent, Depth, ResearchView } from "@/lib/schemas";

export interface TurnProps {
  id: string; // anchor for the history sidebar
  question: string;
  view: ResearchView | null;
  missing: boolean;
  board: Board | null; // live or replay board (null for stored runs)
  lost: boolean;
  sample: boolean;
  busy: boolean;
  feedbackMax: number;
  agents: Agent[];
  activityOpen: boolean;
  onApprove: () => void;
  onReject: (feedback: string | null) => void;
  onRemove: () => void;
  onRunForReal?: () => void;
}

function Progress({ view, board, lost }: { view: ResearchView; board: Board; lost: boolean }) {
  const names = working(board);
  const who = names.length
    ? `${names.map(agentTitle).join(" and ")} ${names.length > 1 ? "are" : "is"} working`
    : view.status === "queued"
      ? "Waiting in the queue"
      : "Working";
  return (
    <div className="space-y-1 text-sm">
      <p className="flex flex-wrap items-center gap-2">
        <span className="inline-block size-2 rounded-full bg-orange-600 motion-safe:animate-pulse" aria-hidden />
        <span className="font-medium">{who}…</span>
      </p>
      {lost && <p className="text-xs">Live updates interrupted — refreshing every few seconds.</p>}
    </div>
  );
}

function Settings({ view }: { view: ResearchView }) {
  const depth = DEPTH_PROFILES[view.depth as Depth]?.label ?? view.depth;
  const sources = view.sources.map((s) => SOURCE_LABELS[s] ?? s).join(", ");
  return (
    <p className="text-xs text-zinc-600 dark:text-zinc-400">
      {depth} · {sources}
    </p>
  );
}

/** One question and its run: live agent board and activity, then the report and details. */
export default function Turn(p: TurnProps) {
  const v = p.view;
  const active = v !== null && (v.status === "running" || v.status === "queued");
  const board = p.board ?? (v ? boardFromRun(v) : null);
  return (
    <li id={p.id} className="scroll-mt-4 space-y-3">
      <div className="ml-auto max-w-[85%] rounded-lg bg-zinc-100 px-3 py-2 text-sm break-words whitespace-pre-wrap dark:bg-zinc-800">
        <span className="sr-only">You asked: </span>
        {p.question || v?.question || "(question)"}
      </div>
      <div className="min-w-0 space-y-4 rounded-lg border border-zinc-300 p-3 dark:border-zinc-700">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="text-sm font-semibold">Research assistant</span>
          <div className="flex flex-wrap items-center gap-2">
            {p.sample && <Badge tone="accent">Sample run — replay (0 AI calls)</Badge>}
            {v && <Settings view={v} />}
          </div>
        </div>

        {p.missing ? (
          <div className="space-y-2 text-sm">
            <p>This run is no longer available (runs are kept for 30 days).</p>
            <button type="button" onClick={p.onRemove} className="font-medium underline">
              Remove from the history
            </button>
          </div>
        ) : !v || !board ? (
          <p className="text-sm text-zinc-600 dark:text-zinc-400">Loading…</p>
        ) : (
          <>
            {v.flags.includes("possible_injection") && (
              <p className="rounded-md bg-amber-50 p-2 text-xs text-amber-900 dark:bg-amber-950 dark:text-amber-100">
                Phrases often used in prompt-injection attempts were found. The agents treated them as data, not as
                instructions.
              </p>
            )}
            {v.memory_used.length > 0 && (
              <p className="text-xs">
                Used memory from {plural(v.memory_used.length, "earlier report")}:{" "}
                {v.memory_used.map((m) => m.question).join("; ")}
              </p>
            )}
            {active && <Progress view={v} board={board} lost={p.lost} />}

            <details open={p.activityOpen || active || undefined} className="space-y-3">
              <summary className="cursor-pointer text-sm font-medium">
                How the agents worked ({plural(v.metadata.tool_calls || countTools(board), "tool call")})
              </summary>
              <div className="mt-3 space-y-4">
                <AgentBoard board={board} agents={p.agents} />
                <ActivityFeed board={board} runId={v.run_id} />
              </div>
            </details>

            {v.status === "awaiting_approval" &&
              (p.sample ? (
                <p className="text-sm">This stored run was waiting for approval.</p>
              ) : (
                <ApprovalPanel run={v} busy={p.busy} feedbackMax={p.feedbackMax} onApprove={p.onApprove} onReject={p.onReject} />
              ))}
            {v.status === "completed" && <Report run={v} />}
            {v.status === "failed" && (
              <p
                role="alert"
                className="rounded-md border border-red-300 bg-red-50 p-2 text-sm text-red-900 dark:border-red-800 dark:bg-red-950 dark:text-red-100"
              >
                The run failed: {v.error ?? "unknown error"}
              </p>
            )}
            {v.status === "cancelled" && <p className="text-sm">This run was cancelled.</p>}
            {(v.status === "completed" || v.status === "failed" || v.status === "cancelled") && <RunDetails run={v} />}
            {p.sample && p.onRunForReal && v.status === "completed" && (
              <button type="button" onClick={p.onRunForReal} className="text-sm font-medium text-orange-800 underline dark:text-orange-300">
                Run it for real
              </button>
            )}
          </>
        )}
      </div>
    </li>
  );
}

function countTools(board: Board): number {
  return board.feed.filter((f) => f.kind === "entry" && f.entry.action === "TOOL_CALL").length;
}
