/** The agent board as a pure reducer over run events. The same reducer runs over live SSE
 * events, over a stored run's history (reload, finished runs) and over a sample replay, so
 * the board is right however the page got there. */
import { shorten } from "./format";
import {
  type Entry,
  FactCheckDataSchema,
  ResearcherDataSchema,
  type ResearchView,
  type ResultEntry,
  ReviewDataSchema,
  type RunEvent,
  type RunStatus,
} from "./schemas";

export type CardState = "idle" | "working" | "done" | "failed";

export interface Card {
  state: CardState;
  task: string | null;
  since: number | null; // ms timestamp when it started working
  note: string | null; // short result summary
  turns: number; // how many times it worked in this run
}

/** One feed line: a plain status text, or a history entry (decision, tool call, result,
 * notice, feedback) rendered by its own component. */
export type FeedItem =
  | { id: number; kind: "text"; agent: string; text: string }
  | { id: number; kind: "entry"; entry: Entry };

export interface Board {
  status: RunStatus | "idle";
  iteration: number;
  iterationLimit: number;
  cards: Record<string, Card>;
  feed: FeedItem[];
  evidence: number; // evidence items collected so far
  reportReady: { title: string; sections: number } | null;
}

export const AGENT_ORDER = ["supervisor", "researcher", "writer", "reviewer", "fact_checker"];

const TITLES: Record<string, string> = {
  supervisor: "Supervisor",
  researcher: "Researcher",
  writer: "Writer",
  reviewer: "Reviewer",
  fact_checker: "Fact-Checker",
  human: "You",
  system: "System",
};

/** "Fact-Checker" (history display name) or "fact_checker" (SSE agent) → "fact_checker". */
export function agentKey(name: string): string {
  return name.trim().toLowerCase().replaceAll("-", "_").replaceAll(" ", "_");
}

export function agentTitle(name: string): string {
  const key = agentKey(name);
  return TITLES[key] ?? name.charAt(0).toUpperCase() + name.slice(1).replaceAll("_", " ");
}

const idleCard = (): Card => ({ state: "idle", task: null, since: null, note: null, turns: 0 });

export function emptyBoard(names: string[] = AGENT_ORDER): Board {
  return {
    status: "idle",
    iteration: 0,
    iterationLimit: 0,
    cards: Object.fromEntries(names.map((n) => [n, idleCard()])),
    feed: [],
    evidence: 0,
    reportReady: null,
  };
}

export function working(board: Board): string[] {
  return Object.entries(board.cards)
    .filter(([, c]) => c.state === "working")
    .map(([n]) => n);
}

/** One-line summary of a worker result for its card. */
export function resultNote(entry: ResultEntry): string {
  if (entry.failed) return "could not finish";
  const key = agentKey(entry.agent);
  if (key === "researcher") {
    const data = ResearcherDataSchema.safeParse(entry.data);
    if (entry.unsourced) return "notes without sources";
    const n = entry.evidence.length;
    const tools = data.success && data.data.tool_calls !== undefined ? `${data.data.tool_calls} tool call${data.data.tool_calls === 1 ? "" : "s"} · ` : "";
    return `${tools}${n} evidence cited`;
  }
  if (key === "reviewer") {
    const review = ReviewDataSchema.safeParse(entry.data);
    if (review.success) {
      const n = review.data.issues.length;
      return `${review.data.verdict} · ${n} issue${n === 1 ? "" : "s"}`;
    }
  }
  if (key === "fact_checker") {
    const check = FactCheckDataSchema.safeParse(entry.data);
    if (check.success) {
      const ok = check.data.claims.filter((c) => c.status === "supported").length;
      return `${ok}/${check.data.claims.length} claims supported`;
    }
  }
  if (key === "writer") return "draft ready";
  return "done";
}

function setCard(board: Board, name: string, patch: Partial<Card>): Record<string, Card> {
  const current = board.cards[name] ?? idleCard();
  return { ...board.cards, [name]: { ...current, ...patch } };
}

function finishWorking(cards: Record<string, Card>, to: CardState): Record<string, Card> {
  return Object.fromEntries(
    Object.entries(cards).map(([n, c]) => [n, c.state === "working" ? { ...c, state: to } : c]),
  );
}

function hasEntry(board: Board, seq: number): boolean {
  return board.feed.some((f) => f.kind === "entry" && f.entry.seq === seq);
}

export function reduce(board: Board, event: RunEvent, now: number): Board {
  const nextId = (board.feed.at(-1)?.id ?? 0) + 1;
  const text = (agent: string, t: string): FeedItem[] => [...board.feed, { id: nextId, kind: "text", agent, text: t }];
  const entry = (e: Entry): FeedItem[] => [...board.feed, { id: nextId, kind: "entry", entry: e }];

  switch (event.type) {
    case "run_started": {
      const { iteration, iteration_limit } = event.data;
      const t =
        iteration > 0
          ? `Revising after your feedback (${iteration_limit - iteration} more decisions)`
          : `Run started: up to ${iteration_limit} Supervisor decisions`;
      return { ...board, status: "running", iteration, iterationLimit: iteration_limit, feed: text("system", t) };
    }
    case "agent_started": {
      const { agent, iteration, task } = event.data;
      const key = agentKey(agent);
      const prev = board.cards[key] ?? idleCard();
      const cards = setCard(board, key, { state: "working", task, since: now, turns: prev.turns + 1 });
      const t =
        key === "supervisor"
          ? `Supervisor is deciding (decision ${iteration + 1} of ${board.iterationLimit || "?"})`
          : `${agentTitle(agent)} started: ${shorten(task ?? "", 90)}`;
      return { ...board, status: "running", cards, feed: text(key, t) };
    }
    case "decision": {
      const d = event.data;
      if (hasEntry(board, d.seq)) return board;
      const note =
        d.action === "FINAL"
          ? "wrote the final answer"
          : `delegated to ${d.delegations.map((x) => agentTitle(x.target_agent)).join(" + ")}`;
      const cards = setCard(board, "supervisor", { state: "done", note });
      return { ...board, iteration: Math.max(board.iteration, d.iteration), cards, feed: entry(d) };
    }
    case "tool_call": {
      if (hasEntry(board, event.data.seq)) return board;
      return { ...board, feed: entry(event.data) };
    }
    case "worker_result": {
      const r = event.data;
      if (hasEntry(board, r.seq)) return board;
      const cards = setCard(board, agentKey(r.agent), { state: r.failed ? "failed" : "done", note: resultNote(r) });
      return { ...board, cards, feed: entry(r) };
    }
    case "notice":
      if (hasEntry(board, event.data.seq)) return board;
      return { ...board, feed: entry(event.data) };
    case "feedback":
      if (hasEntry(board, event.data.seq)) return board;
      return { ...board, status: "queued", feed: entry(event.data) };
    case "evidence_added":
      return { ...board, evidence: Math.max(board.evidence, event.data.total) };
    case "report_ready":
      return { ...board, reportReady: event.data };
    case "awaiting_approval":
      return {
        ...board,
        status: "awaiting_approval",
        cards: finishWorking(board.cards, "done"),
        feed: text("system", "Waiting for your approval"),
      };
    case "completed": {
      const { forced, degraded } = event.data;
      const how = degraded ? " (assembled automatically)" : forced ? " (forced completion)" : "";
      return {
        ...board,
        status: "completed",
        cards: finishWorking(board.cards, "done"),
        feed: text("system", `Report ready${how}`),
      };
    }
    case "failed":
      return {
        ...board,
        status: "failed",
        cards: finishWorking(board.cards, "failed"),
        feed: text("system", `Run failed: ${event.data.error ?? "unknown error"}`),
      };
    case "cancelled":
      return {
        ...board,
        status: "cancelled",
        cards: finishWorking(board.cards, "idle"),
        feed: text("system", `Run cancelled${event.data.reason ? `: ${event.data.reason}` : ""}`),
      };
  }
}

/** A stored run as the event sequence that produced it (for reloads and replays). */
export function eventsFromRun(run: ResearchView): RunEvent[] {
  const events: RunEvent[] = [];
  let id = 0;
  const push = (e: Omit<RunEvent, "id">) => events.push({ ...e, id: --id } as RunEvent);
  push({ type: "run_started", data: { iteration: 0, iteration_limit: run.metadata.max_iterations ?? run.metadata.iteration_limit } });
  const started = new Set<string>();
  const evidence = new Set<string>();
  for (const e of run.conversation_history) {
    switch (e.action) {
      case "DELEGATE":
      case "FINAL": {
        const d = e as Extract<Entry, { action: "DELEGATE" | "FINAL" }>;
        push({ type: "agent_started", data: { agent: "supervisor", iteration: d.iteration - 1, task: null } });
        push({ type: "decision", data: d });
        if (d.action === "DELEGATE") {
          // Parallel delegations start together; their results follow.
          for (const x of d.delegations) {
            push({ type: "agent_started", data: { agent: agentKey(x.target_agent), iteration: d.iteration, task: x.task } });
            started.add(agentKey(x.target_agent));
          }
        }
        break;
      }
      case "TOOL_CALL": {
        const t = e as Extract<Entry, { action: "TOOL_CALL" }>;
        const added = t.evidence.filter((x) => !evidence.has(x));
        added.forEach((x) => evidence.add(x));
        if (added.length) push({ type: "evidence_added", data: { count: added.length, total: evidence.size } });
        push({ type: "tool_call", data: t });
        break;
      }
      case "RESULT": {
        const r = e as ResultEntry;
        const key = agentKey(r.agent);
        if (!started.has(key)) {
          push({ type: "agent_started", data: { agent: key, iteration: r.iteration, task: r.task } });
        }
        started.delete(key);
        push({ type: "worker_result", data: r });
        break;
      }
      case "FEEDBACK":
        push({ type: "feedback", data: e as Extract<Entry, { action: "FEEDBACK" }> });
        push({ type: "run_started", data: { iteration: e.iteration, iteration_limit: run.metadata.iteration_limit } });
        break;
      case "NOTICE":
        push({ type: "notice", data: e as Extract<Entry, { action: "NOTICE" }> });
        break;
      default:
        break; // unknown actions are shown in the transcript only
    }
  }
  if (run.status === "completed") {
    if (run.report) push({ type: "report_ready", data: { title: run.report.title, sections: run.report.sections.length } });
    push({
      type: "completed",
      data: { final_output: run.final_output, forced: run.metadata.forced, degraded: run.metadata.degraded },
    });
  } else if (run.status === "awaiting_approval") {
    push({ type: "awaiting_approval", data: { draft: run.approval.draft ?? "", expires_at: run.approval.expires_at } });
  } else if (run.status === "failed") {
    push({ type: "failed", data: { error: run.error ?? "unknown error" } });
  } else if (run.status === "cancelled") {
    push({ type: "cancelled", data: { reason: run.error } });
  }
  return events;
}

export function boardFromRun(run: ResearchView, names: string[] = AGENT_ORDER): Board {
  const board = eventsFromRun(run).reduce((b, e) => reduce(b, e, 0), emptyBoard(names));
  return {
    ...board,
    iteration: run.metadata.iterations,
    iterationLimit: run.metadata.iteration_limit,
    evidence: Math.max(board.evidence, run.metadata.evidence),
  };
}
