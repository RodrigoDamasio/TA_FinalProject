import type { Sample } from "@/lib/schemas";

/** The stored sample runs as cards: a click replays one (0 LLM calls). */
export default function Suggestions({
  samples,
  disabled,
  onReplay,
}: {
  samples: Sample[];
  disabled: boolean;
  onReplay: (sample: Sample) => void;
}) {
  if (samples.length === 0) return null;
  return (
    <section aria-label="Samples" className="space-y-2">
      <h2 className="text-sm font-semibold">Sample runs (replayed, 0 AI calls)</h2>
      <ul className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        {samples.map((s) => (
          <li key={s.id}>
            <button
              type="button"
              disabled={disabled}
              onClick={() => onReplay(s)}
              className="h-full w-full rounded-md border border-zinc-300 p-2 text-left text-sm hover:border-orange-700 disabled:opacity-60 dark:border-zinc-700"
            >
              <span className="block font-medium">{s.title}</span>
              <span className="block text-xs text-zinc-600 dark:text-zinc-400">{s.question}</span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
