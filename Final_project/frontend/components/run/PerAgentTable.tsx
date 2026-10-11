import { agentKey, AGENT_ORDER, agentTitle } from "@/lib/agents";
import { formatMs } from "@/lib/format";
import type { Metadata } from "@/lib/schemas";

/** LLM calls, tokens and time per agent (`metadata.per_agent`). */
export default function PerAgentTable({ perAgent }: { perAgent: Metadata["per_agent"] }) {
  const rank = (name: string) => {
    const i = AGENT_ORDER.indexOf(agentKey(name));
    return i === -1 ? AGENT_ORDER.length : i;
  };
  const rows = Object.entries(perAgent).sort(([a], [b]) => rank(a) - rank(b));
  if (rows.length === 0) return null;
  return (
    <div role="region" aria-label="Per-agent usage" tabIndex={0} className="overflow-x-auto">
      <table className="w-full text-left text-xs">
        <caption className="sr-only">LLM calls, tokens and time per agent</caption>
        <thead>
          <tr className="border-b border-zinc-200 dark:border-zinc-800">
            <th scope="col" className="py-1 pr-3 font-medium">
              Agent
            </th>
            <th scope="col" className="py-1 pr-3 text-right font-medium">
              Calls
            </th>
            <th scope="col" className="py-1 pr-3 text-right font-medium">
              Tokens
            </th>
            <th scope="col" className="py-1 text-right font-medium">
              Time
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([name, a]) => (
            <tr key={name} className="border-b border-zinc-100 dark:border-zinc-900">
              <th scope="row" className="py-1 pr-3 font-normal">
                {agentTitle(name)}
              </th>
              <td className="py-1 pr-3 text-right">{a.calls}</td>
              <td className="py-1 pr-3 text-right">{a.tokens.toLocaleString("en-US")}</td>
              <td className="py-1 text-right">{formatMs(a.duration_ms)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
