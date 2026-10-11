"use client";
import { useNow } from "@/hooks/useNow";
import { AGENT_ORDER, agentTitle, type Board, type Card } from "@/lib/agents";
import { formatMs, shorten } from "@/lib/format";
import type { Agent } from "@/lib/schemas";

const STATE_STYLE: Record<Card["state"], string> = {
  idle: "border-zinc-300 dark:border-zinc-700",
  working: "border-orange-600 bg-orange-50 dark:border-orange-500 dark:bg-orange-950",
  done: "border-emerald-700 dark:border-emerald-600",
  failed: "border-red-700 dark:border-red-500",
};
const STATE_TEXT: Record<Card["state"], string> = {
  idle: "○ idle",
  working: "● working",
  done: "✓ done",
  failed: "✕ failed",
};

function AgentChip({ name, card, agent, now }: { name: string; card: Card; agent?: Agent; now: number | null }) {
  const elapsed = card.state === "working" && card.since && now ? Math.max(0, now - card.since) : null;
  const title = agent?.title ?? agentTitle(name);
  return (
    <li
      className={`min-w-0 rounded-md border-2 px-2 py-1 ${STATE_STYLE[card.state]}`}
      aria-label={`${title}: ${card.state}`}
      title={card.state === "working" && card.task ? shorten(card.task, 200) : (card.note ?? undefined)}
    >
      <span className="block truncate text-sm font-medium">{title}</span>
      <span
        className={`block text-xs font-medium ${card.state === "working" ? "text-orange-800 dark:text-orange-300" : "text-zinc-700 dark:text-zinc-300"}`}
      >
        {STATE_TEXT[card.state]}
        {elapsed !== null && elapsed >= 1000 ? ` · ${formatMs(elapsed)}` : ""}
      </span>
      {card.state !== "working" && card.note && (
        <span className="block truncate text-xs text-zinc-600 dark:text-zinc-400">{card.note}</span>
      )}
    </li>
  );
}

/** One chip per agent: idle · working · done · failed (two can work at once). */
export default function AgentBoard({ board, agents }: { board: Board; agents: Agent[] }) {
  const now = useNow(1000);
  const names = agents.length ? agents.map((a) => a.name) : AGENT_ORDER;
  const byName = new Map(agents.map((a) => [a.name, a]));
  return (
    <section aria-label="Agents" className="space-y-2">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-sm font-semibold tracking-wide uppercase">Agents</h3>
        {board.iterationLimit > 0 && (
          <span className="text-xs text-zinc-700 dark:text-zinc-300">
            decision {Math.min(board.iteration, board.iterationLimit)} of {board.iterationLimit}
          </span>
        )}
      </div>
      <ul className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
        {names.map((name) => (
          <AgentChip
            key={name}
            name={name}
            card={board.cards[name] ?? { state: "idle", task: null, since: null, note: null, turns: 0 }}
            agent={byName.get(name)}
            now={now}
          />
        ))}
      </ul>
    </section>
  );
}
