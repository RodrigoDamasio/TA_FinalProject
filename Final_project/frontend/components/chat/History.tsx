import { formatDate, shorten } from "@/lib/format";
import type { ThreadItem } from "@/lib/thread";

/** The local history (this browser only): jump to a run, or forget it. Collapsed by default
 * (a drawer on phones, the top of the sidebar on wide screens). */
export default function History({
  items,
  onRemove,
}: {
  items: ThreadItem[];
  onRemove: (runId: string) => void;
}) {
  const list =
    items.length === 0 ? (
      <p className="text-xs text-zinc-600 dark:text-zinc-400">Your questions appear here (kept in this browser).</p>
    ) : (
      <ol className="space-y-1">
        {[...items].reverse().map((i) => (
          <li key={i.run_id} className="flex items-start gap-1">
            <a href={`#turn-${i.run_id}`} className="min-w-0 flex-1 rounded px-1 py-0.5 text-sm break-words hover:bg-zinc-100 dark:hover:bg-zinc-800">
              {shorten(i.question || i.run_id, 80)}
              <span className="block text-xs text-zinc-600 dark:text-zinc-400">{formatDate(i.created_at)}</span>
            </a>
            <button
              type="button"
              onClick={() => onRemove(i.run_id)}
              aria-label={`Remove from history: ${shorten(i.question || i.run_id, 60)}`}
              className="rounded px-1 text-sm text-zinc-700 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-800"
            >
              ×
            </button>
          </li>
        ))}
      </ol>
    );
  return (
    <nav aria-label="History" className="min-w-0">
      <details className="rounded-md border border-zinc-300 p-2 dark:border-zinc-700">
        <summary className="cursor-pointer text-sm font-medium">History ({items.length})</summary>
        <div className="mt-2">{list}</div>
      </details>
    </nav>
  );
}
