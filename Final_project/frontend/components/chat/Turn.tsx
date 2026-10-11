"use client";
import ActivityFeed from "@/components/activity/ActivityFeed";
import AgentBoard from "@/components/activity/AgentBoard";
import ApprovalPanel from "@/components/ApprovalPanel";
import Badge, { type Tone } from "@/components/Badge";
import Report from "@/components/report/Report";
import RunDetails from "@/components/run/RunDetails";
import { agentTitle, type Board, boardFromRun, working } from "@/lib/agents";
import { plainPreview } from "@/lib/citations";
import { DEPTH_PROFILES, plural, SOURCE_LABELS } from "@/lib/format";
import type { Agent, Depth, ResearchView, RunStatus } from "@/lib/schemas";

export interface TurnProps {
  id: string; // anchor: `turn-<run_id>`
  question: string;
  turn: number | null; // position in the chat; null for a sample replay
  view: ResearchView | null;
  missing: boolean;
  board: Board | null; // live or replay board (null for stored runs)
  lost: boolean;
  sample: boolean;
  busy: boolean;
  feedbackMax: number;
  agents: Agent[];
  expanded: boolean;
  onToggle?: () => void; // absent: always expanded (a sample replay)
  activityOpen: boolean; // keep "How the agents worked" open after the run (sample replays)
  onApprove: () => void;
  onReject: (feedback: string | null) => void;
  onRunForReal?: () => void;
}

const STATUS_TEXT: Record<RunStatus, [string, Tone]> = {
  queued: ["Queued", "neutral"],
  running: ["Working…", "accent"],
  awaiting_approval: ["Waiting for your approval", "warn"],
  completed: ["Completed", "good"],
  failed: ["Failed", "bad"],
  cancelled: ["Cancelled", "neutral"],
};

export function turnLabel(turn: number): string {
  return turn >= 2 ? `Follow-up · turn ${turn}` : `Turn ${turn}`;
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

function countTools(board: Board): number {
  return board.feed.filter((f) => f.kind === "entry" && f.entry.action === "TOOL_CALL").length;
}

function countDecisions(board: Board): number {
  return board.feed.filter((f) => f.kind === "entry" && (f.entry.action === "DELEGATE" || f.entry.action === "FINAL"))
    .length;
}

/** "3 tool calls · 17 evidence · 4 decisions": what "How the agents worked" holds. */
export function activitySummary(view: ResearchView, board: Board): string {
  const tools = Math.max(view.metadata.tool_calls, countTools(board));
  const evidence = Math.max(view.metadata.evidence, board.evidence);
  return `${plural(tools, "tool call")} · ${evidence} evidence · ${plural(countDecisions(board), "decision")}`;
}

/** The collapsed card: report title, a 2-line summary and badges. No report Markdown is
 * rendered (cheaper DOM on long chats). */
function Collapsed({ view }: { view: ResearchView }) {
  const [status, tone] = STATUS_TEXT[view.status];
  const m = view.metadata;
  const depth = DEPTH_PROFILES[view.depth as Depth]?.label ?? view.depth;
  return (
    <div className="space-y-1.5">
      {view.report && <p className="font-semibold break-words">{plainPreview(view.report.title)}</p>}
      {view.report && (
        <p className="line-clamp-2 text-sm break-words text-zinc-700 dark:text-zinc-300">{plainPreview(view.report.summary)}</p>
      )}
      <p className="flex flex-wrap gap-1.5">
        {view.status !== "completed" && <Badge tone={tone}>{status}</Badge>}
        <Badge>{plural(m.tool_calls, "tool call")}</Badge>
        <Badge>{m.evidence} evidence</Badge>
        {m.grounding && (
          <Badge tone="good">
            {m.grounding.supported}/{m.grounding.checked} supported
          </Badge>
        )}
        <Badge>{depth}</Badge>
      </p>
    </div>
  );
}

/** One question and its run. Expanded: the live agent board and activity, then the report and
 * details. Collapsed: one card. The header (the question) toggles between them. */
export default function Turn(p: TurnProps) {
  const v = p.view;
  const active = v !== null && (v.status === "running" || v.status === "queued");
  const board = p.board ?? (v ? boardFromRun(v) : null);
  const question = p.question || v?.question || "(question)";
  const turn = v?.turn ?? p.turn;
  const bodyId = `${p.id}-body`;
  const contextTurns = v?.metadata.context_turns ?? 0;

  return (
    <li id={p.id} className="scroll-mt-4 space-y-2" data-expanded={p.expanded}>
      {p.onToggle ? (
        <button
          type="button"
          id={`${p.id}-toggle`}
          aria-expanded={p.expanded}
          aria-controls={bodyId}
          onClick={p.onToggle}
          className="flex w-full items-start gap-2 rounded-lg bg-zinc-100 px-3 py-2 text-left text-sm hover:bg-zinc-200 dark:bg-zinc-800 dark:hover:bg-zinc-700"
        >
          <span aria-hidden className="w-3 shrink-0">
            {p.expanded ? "▾" : "▸"}
          </span>
          <span className="min-w-0 flex-1">
            {turn !== null && (
              <span className="block text-xs font-medium text-zinc-700 dark:text-zinc-300">
                {turnLabel(turn)}
                <span className="sr-only">:</span>
              </span>
            )}{" "}
            <span className="block break-words whitespace-pre-wrap">{question}</span>
          </span>
        </button>
      ) : (
        <div className="ml-auto max-w-[85%] rounded-lg bg-zinc-100 px-3 py-2 text-sm break-words whitespace-pre-wrap dark:bg-zinc-800">
          <span className="sr-only">You asked: </span>
          {question}
        </div>
      )}

      <div id={bodyId} className="min-w-0 space-y-4 rounded-lg border border-zinc-300 p-3 dark:border-zinc-700">
        {p.expanded && (
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-sm font-semibold">Research assistant</span>
            <div className="flex flex-wrap items-center gap-2">
              {p.sample && <Badge tone="accent">Sample run — replay (0 AI calls)</Badge>}
              {v && <Settings view={v} />}
            </div>
          </div>
        )}
        {contextTurns > 0 && (
          <p className="text-xs text-zinc-700 dark:text-zinc-300">
            <span aria-hidden>💬 </span>Used context from {plural(contextTurns, "earlier turn")}
          </p>
        )}

        {p.missing ? (
          <p className="text-sm">This run is no longer available (runs are kept for 30 days).</p>
        ) : !v || !board ? (
          <p className="text-sm text-zinc-600 dark:text-zinc-400">Loading…</p>
        ) : !p.expanded ? (
          <Collapsed view={v} />
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

            {/* Open while the run is live; remounted (closed) when it ends, leaving the summary. */}
            <details key={active ? "live" : "done"} open={active || p.activityOpen || undefined} className="space-y-3">
              <summary className="cursor-pointer text-sm font-medium">
                How the agents worked{" "}
                <span className="font-normal text-zinc-700 dark:text-zinc-300">· {activitySummary(v, board)}</span>
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
