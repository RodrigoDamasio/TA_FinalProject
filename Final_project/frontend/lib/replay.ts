/** Sample replay (Lab 3): a stored run's events with delays, so the same components animate
 * it — agent board, tool-call cards, report — at 0 LLM calls. */
import { eventsFromRun } from "./agents";
import type { ResearchView, RunEvent } from "./schemas";

export interface Frame {
  delayMs: number; // wait before applying this event
  event: RunEvent;
}

const DELAYS: Partial<Record<RunEvent["type"], number>> = {
  run_started: 100,
  agent_started: 400,
  decision: 600,
  tool_call: 700,
  evidence_added: 0,
  worker_result: 800,
  notice: 250,
  report_ready: 300,
};

export function replayFrames(run: ResearchView, speed = 1): Frame[] {
  const events = eventsFromRun(run);
  return events.map((event, i) => {
    // agent_started right after a parallel decision: no extra wait between the two starts
    const prev = events[i - 1];
    const parallelStart = event.type === "agent_started" && prev?.type === "agent_started";
    const base = parallelStart ? 0 : (DELAYS[event.type] ?? 500);
    return { delayMs: Math.round(base * speed), event };
  });
}
